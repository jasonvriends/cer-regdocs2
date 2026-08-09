#!/usr/bin/env python3
"""Single-threaded crash-resilient REGDOCS Docling analyzer.

One supervisor invocation owns one ``runs`` row. Exactly one child process runs
at a time, and each child analyzes one document via ``regdocs_3_docling_worker``.
Children attach their ``analyses`` rows to the supervisor-owned run instead of
creating a pipeline run per document.

Unlike Azure, Docling is local and keeps its same-run retry/quarantine policy.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from regdocs_paths import ANALYZE_DIR, DATABASE_PATH, DOWNLOAD_DIR, resolve_stored_path, stored_path

SCRIPT_VERSION = "3d.2.0"
PARSER_VERSION = "regdocs-docling-projection-2026-08-08-v1"
DEFAULT_ANALYZER_ID = "docling-standard"
DEFAULT_OUTPUT_DIR = ANALYZE_DIR / "docling"
DEFAULT_STATE_FILE = DEFAULT_OUTPUT_DIR / "supervisor-state.json"
DEFAULT_MAX_ATTEMPTS = 3


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(partial, path)


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "documents": {},
        }
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or not isinstance(state.get("documents", {}), dict):
        raise ValueError(f"Invalid Docling state: {path}")
    state.setdefault("schema_version", 1)
    state.setdefault("created_at", utcnow())
    state.setdefault("documents", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utcnow()
    atomic_write_json(path, state)


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def current_document_ids(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT d.id FROM files f JOIN documents d ON d.id=f.document_id WHERE f.is_current=1"
    ).fetchall()
    values = [str(r[0]) for r in rows]
    return sorted(values, key=lambda x: (0, int(x), x) if x.isdigit() else (1, x.casefold(), x))


def successful_ids(con: sqlite3.Connection, analyzer_id: str, version: str) -> set[str]:
    rows = con.execute(
        """
        SELECT DISTINCT a.document_id
        FROM analyses a JOIN files f ON f.id=a.file_id
        WHERE a.status='SUCCEEDED' AND a.analyzer_id=? AND a.api_version=?
          AND f.is_current=1 AND f.sha256=a.file_sha256
        """,
        (analyzer_id, version),
    ).fetchall()
    return {str(r[0]) for r in rows}


def selectable_ids(
    con: sqlite3.Connection,
    state: dict[str, Any],
    analyzer_id: str,
    version: str,
    max_attempts: int,
) -> list[str]:
    done = successful_ids(con, analyzer_id, version)
    selected: list[str] = []
    for document_id in current_document_ids(con):
        if document_id in done:
            continue
        info = state["documents"].get(document_id, {})
        if not isinstance(info, dict):
            info = {}
        if info.get("quarantined"):
            continue
        if int(info.get("attempts") or 0) >= max_attempts:
            continue
        selected.append(document_id)
    return selected


def select_next(
    con: sqlite3.Connection,
    state: dict[str, Any],
    analyzer_id: str,
    version: str,
    max_attempts: int,
) -> Optional[str]:
    selected = selectable_ids(con, state, analyzer_id, version, max_attempts)
    return selected[0] if selected else None


def current_analysis(
    con: sqlite3.Connection,
    document_id: str,
    analyzer_id: str,
    version: str,
) -> Optional[sqlite3.Row]:
    return con.execute(
        """
        SELECT a.status, a.error_code, a.error_message, a.page_count
        FROM analyses a
        JOIN files f ON f.id=a.file_id AND f.sha256=a.file_sha256
        WHERE f.is_current=1 AND a.document_id=? AND a.analyzer_id=? AND a.api_version=?
        ORDER BY a.id DESC LIMIT 1
        """,
        (document_id, analyzer_id, version),
    ).fetchone()


def create_supervisor_run(
    con: sqlite3.Connection,
    args: argparse.Namespace,
    version: str,
    total: int,
) -> int:
    now = utcnow()
    params = {
        "provider": "docling",
        "db": stored_path(args.db),
        "download_dir": stored_path(args.download_dir),
        "output_dir": stored_path(args.output_dir),
        "analyzer_id": args.analyzer_id,
        "docling_version": version,
        "max_attempts": args.max_attempts,
        "max_documents": args.max_documents,
        "retry_quarantined": args.retry_quarantined,
        "concurrency": 1,
        "run_owner": "supervisor",
    }
    cur = con.execute(
        """
        INSERT INTO runs (
            stage, status, started_at, parameters_json, summary_json,
            script_version, parser_version, current_phase, heartbeat_at,
            completed_units, total_units, progress_message
        ) VALUES ('analyze_docling','RUNNING',?,?,'{}',?,?, 'converting',?,0,?,?)
        """,
        (
            now,
            json.dumps(params, sort_keys=True),
            SCRIPT_VERSION,
            PARSER_VERSION,
            now,
            total,
            f"Docling supervisor selected {total} document(s)",
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def update_supervisor_run(
    con: sqlite3.Connection,
    run_id: int,
    total: int,
    completed: int,
    launched: int,
    succeeded: int,
    failed_attempts: int,
    quarantined_now: int,
    *,
    status: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    now = utcnow()
    summary = {
        "provider": "docling",
        "documents_total": total,
        "documents_completed": completed,
        "worker_launches": launched,
        "succeeded": succeeded,
        "failed_attempts": failed_attempts,
        "quarantined": quarantined_now,
        "concurrency": 1,
    }
    progress = message or (
        f"Docling completed={completed}/{total}, launches={launched}, "
        f"succeeded={succeeded}, failed_attempts={failed_attempts}, "
        f"quarantined={quarantined_now}"
    )
    if status is None:
        con.execute(
            """
            UPDATE runs
            SET heartbeat_at=?, completed_units=?, total_units=?, summary_json=?,
                progress_message=?, successful_requests=?, failed_requests=?
            WHERE id=?
            """,
            (
                now,
                completed,
                total,
                json.dumps(summary, sort_keys=True),
                progress,
                succeeded,
                failed_attempts,
                run_id,
            ),
        )
    else:
        con.execute(
            """
            UPDATE runs
            SET status=?, finished_at=?, heartbeat_at=?, current_phase='finished',
                completed_units=?, total_units=?, summary_json=?, progress_message=?,
                successful_requests=?, failed_requests=?
            WHERE id=?
            """,
            (
                status,
                now,
                now,
                completed,
                total,
                json.dumps(summary, sort_keys=True),
                progress,
                succeeded,
                failed_attempts,
                run_id,
            ),
        )
    con.commit()


def child_bootstrap(worker: Path) -> str:
    """Bind the existing worker to the supervisor-owned run.

    The worker's analysis logic remains unchanged. This wrapper replaces its
    per-process run allocator and suppresses worker-level ``UPDATE runs`` calls;
    all other SQLite statements pass through to the real connection.
    """
    return f"""
import sys
sys.path.insert(0, {str(worker.parent)!r})
import regdocs_3_docling_worker as worker
parent_run_id = int(sys.argv[1])
_original_open_db = worker.open_db
class RunBoundConnection:
    def __init__(self, con):
        self._con = con
    def execute(self, sql, params=()):
        normalized = ' '.join(str(sql).split()).upper()
        if normalized.startswith('UPDATE RUNS SET'):
            return self._con.execute('SELECT 1')
        return self._con.execute(sql, params)
    def __getattr__(self, name):
        return getattr(self._con, name)
worker.open_db = lambda path: RunBoundConnection(_original_open_db(path))
worker.create_run = lambda con, args, version, total: parent_run_id
sys.argv = [worker.__file__] + sys.argv[2:]
raise SystemExit(worker.main())
""".strip()


def child_command(args: argparse.Namespace, document_id: str, run_id: int) -> list[str]:
    worker = Path(__file__).with_name("regdocs_3_docling_worker.py")
    return [
        sys.executable,
        "-c",
        child_bootstrap(worker),
        str(run_id),
        "--db", str(args.db),
        "--download-dir", str(args.download_dir),
        "--output-dir", str(args.output_dir),
        "--analyzer-id", args.analyzer_id,
        "--document-id", document_id,
    ]


def print_worker_diagnostics(stdout: str, stderr: str) -> None:
    blocks = []
    if stdout.strip():
        blocks.append(stdout.rstrip())
    if stderr.strip():
        blocks.append(stderr.rstrip())
    if not blocks:
        return
    print("    worker diagnostics:", file=sys.stderr)
    for block in blocks:
        for line in block.splitlines():
            print(f"      {line}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Single-threaded crash-resilient REGDOCS Docling analysis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--db", default=stored_path(DATABASE_PATH))
    p.add_argument("--download-dir", default=stored_path(DOWNLOAD_DIR))
    p.add_argument("--output-dir", default=stored_path(DEFAULT_OUTPUT_DIR))
    p.add_argument("--state-file", default=stored_path(DEFAULT_STATE_FILE))
    p.add_argument("--analyzer-id", default=DEFAULT_ANALYZER_ID)
    p.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    p.add_argument("--max-documents", type=int, help="Stop after launching N child documents")
    p.add_argument("--sleep-seconds", type=float, default=0.25)
    p.add_argument("--retry-quarantined", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--version", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.version:
        print(SCRIPT_VERSION)
        return 0
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be >= 1")
    if args.max_documents is not None and args.max_documents < 1:
        raise SystemExit("--max-documents must be >= 1")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds must be >= 0")

    args.db = resolve_stored_path(args.db)
    args.download_dir = resolve_stored_path(args.download_dir)
    args.output_dir = resolve_stored_path(args.output_dir)
    args.state_file = resolve_stored_path(args.state_file)

    try:
        version = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit("Docling is not installed. Run: pip install -r pipeline/requirements-docling.txt")

    state = load_state(args.state_file)
    if args.retry_quarantined:
        for info in state["documents"].values():
            if isinstance(info, dict) and info.get("quarantined"):
                info["attempts"] = 0
                info["quarantined"] = False
                info["retry_started_at"] = utcnow()
        save_state(args.state_file, state)

    con = open_db(args.db)
    run_id: Optional[int] = None
    total = completed = launched = succeeded_run = failed_attempts = quarantined_now = 0
    started = time.monotonic()

    try:
        if args.status:
            current = current_document_ids(con)
            done = successful_ids(con, args.analyzer_id, version)
            quarantined = sorted(
                doc_id for doc_id, info in state["documents"].items()
                if isinstance(info, dict) and info.get("quarantined")
            )
            print(json.dumps({
                "docling_version": version,
                "current_documents": len(current),
                "succeeded_current": len(done),
                "remaining_current": len([x for x in current if x not in done]),
                "quarantined": quarantined,
                "state_file": stored_path(args.state_file),
                "concurrency": 1,
            }, indent=2, sort_keys=True))
            return 0

        initial_selected = selectable_ids(
            con, state, args.analyzer_id, version, args.max_attempts
        )
        total = len(initial_selected)
        run_id = create_supervisor_run(con, args, version, total)

        print(
            f"Run {run_id}: Docling supervisor {SCRIPT_VERSION}; "
            f"{total} document(s) eligible"
        )
        print("Concurrency:    1 child process")
        print(f"Crash retries:  up to {args.max_attempts} attempt(s) per document")
        print()

        if total == 0:
            update_supervisor_run(
                con, run_id, 0, 0, 0, 0, 0, 0,
                status="SUCCEEDED",
                message="Docling supervisor: no eligible documents",
            )
            return 0

        while True:
            if args.max_documents is not None and launched >= args.max_documents:
                break

            document_id = select_next(
                con, state, args.analyzer_id, version, args.max_attempts
            )
            if document_id is None:
                break

            info = state["documents"].setdefault(document_id, {})
            if not isinstance(info, dict):
                info = {}
                state["documents"][document_id] = info
            info["attempts"] = int(info.get("attempts") or 0) + 1
            info["last_started_at"] = utcnow()
            info["last_exit_code"] = None
            info["last_signal"] = None
            info["last_pipeline_run_id"] = run_id
            save_state(args.state_file, state)

            launched += 1
            print(
                f"[{launched}] {document_id} attempt {info['attempts']}/{args.max_attempts} ... ",
                end="",
                flush=True,
            )

            try:
                result = subprocess.run(
                    child_command(args, document_id, run_id),
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                returncode = int(result.returncode)
                worker_stdout = result.stdout or ""
                worker_stderr = result.stderr or ""
            except KeyboardInterrupt:
                save_state(args.state_file, state)
                print("INTERRUPTED")
                update_supervisor_run(
                    con, run_id, total, completed, launched - 1, succeeded_run,
                    failed_attempts, quarantined_now, status="INTERRUPTED",
                    message="Docling supervisor interrupted by user",
                )
                print(
                    f"Docling interrupted; Run {run_id} preserved.",
                    file=sys.stderr,
                )
                return 130

            info["last_finished_at"] = utcnow()
            info["last_exit_code"] = returncode
            if returncode < 0:
                try:
                    info["last_signal"] = signal.Signals(-returncode).name
                except ValueError:
                    info["last_signal"] = str(-returncode)
            save_state(args.state_file, state)

            con.close()
            con = open_db(args.db)
            analysis = current_analysis(con, document_id, args.analyzer_id, version)
            status = str(analysis["status"]) if analysis is not None else None
            is_success = status == "SUCCEEDED"

            if returncode == 130:
                print("INTERRUPTED")
                print_worker_diagnostics(worker_stdout, worker_stderr)
                update_supervisor_run(
                    con, run_id, total, completed, launched, succeeded_run,
                    failed_attempts, quarantined_now, status="INTERRUPTED",
                    message=f"Docling worker interrupted on {document_id}",
                )
                return 130

            if returncode == 2:
                print("CONFIG_ERROR")
                print_worker_diagnostics(worker_stdout, worker_stderr)
                update_supervisor_run(
                    con, run_id, total, completed, launched, succeeded_run,
                    failed_attempts, quarantined_now, status="FAILED",
                    message=f"Docling worker configuration/input error on {document_id}",
                )
                return 2

            if is_success:
                info["completed_at"] = utcnow()
                info["quarantined"] = False
                save_state(args.state_file, state)
                succeeded_run += 1
                completed += 1
                pages = int(analysis["page_count"] or 0) if analysis is not None else 0
                print(f"SUCCEEDED pages={pages}", flush=True)
            else:
                failed_attempts += 1
                extra = f" signal={info['last_signal']}" if info.get("last_signal") else ""
                if int(info["attempts"]) >= args.max_attempts:
                    info["quarantined"] = True
                    info["quarantined_at"] = utcnow()
                    save_state(args.state_file, state)
                    quarantined_now += 1
                    completed += 1
                    print(f"QUARANTINED exit={returncode}{extra}")
                    print_worker_diagnostics(worker_stdout, worker_stderr)
                else:
                    print(f"FAILED exit={returncode}{extra}; retrying in fresh child")
                    print_worker_diagnostics(worker_stdout, worker_stderr)

            update_supervisor_run(
                con,
                run_id,
                total,
                completed,
                launched,
                succeeded_run,
                failed_attempts,
                quarantined_now,
            )

            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)

        remaining_selectable = selectable_ids(
            con, state, args.analyzer_id, version, args.max_attempts
        )
        current = current_document_ids(con)
        done = successful_ids(con, args.analyzer_id, version)
        total_quarantined = sum(
            1 for info in state["documents"].values()
            if isinstance(info, dict) and info.get("quarantined")
        )
        bounded_stop = bool(
            args.max_documents is not None
            and launched >= args.max_documents
            and remaining_selectable
        )
        if quarantined_now:
            final_status = "COMPLETED_WITH_ERRORS"
        elif bounded_stop:
            final_status = "PARTIAL"
        else:
            final_status = "SUCCEEDED"

        elapsed = time.monotonic() - started
        update_supervisor_run(
            con,
            run_id,
            total,
            completed,
            launched,
            succeeded_run,
            failed_attempts,
            quarantined_now,
            status=final_status,
            message=(
                f"Docling {final_status}: run_succeeded={succeeded_run}, "
                f"failed_attempts={failed_attempts}, quarantined_now={quarantined_now}, "
                f"corpus_succeeded={len(done)}/{len(current)}, "
                f"total_quarantined={total_quarantined}, elapsed={elapsed:.1f}s"
            ),
        )
        print()
        print(
            f"Run {run_id} {final_status}: {len(done)}/{len(current)} current documents "
            f"succeeded; {total_quarantined} quarantined; launches={launched}; "
            f"concurrency=1; elapsed={elapsed:.1f}s."
        )
        return 1 if quarantined_now else 0

    except KeyboardInterrupt:
        if run_id is not None:
            with contextlib.suppress(Exception):
                update_supervisor_run(
                    con, run_id, total, completed, launched, succeeded_run,
                    failed_attempts, quarantined_now, status="INTERRUPTED",
                    message="Docling supervisor interrupted by user",
                )
        print("\nDocling interrupted; state preserved.", file=sys.stderr)
        return 130
    except Exception as exc:
        if run_id is not None:
            with contextlib.suppress(Exception):
                update_supervisor_run(
                    con, run_id, total, completed, launched, succeeded_run,
                    failed_attempts, quarantined_now, status="FAILED",
                    message=f"Docling supervisor failed: {str(exc)[:800]}",
                )
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
