#!/usr/bin/env python3
"""Single-threaded crash-resilient REGDOCS Azure analyzer.

The public Stage 3 Azure command is a durable supervisor. It never imports the
Azure SDK or parses source PDFs itself. Instead it launches exactly one child
process per selected document through ``regdocs_3_azure_worker.py``.

Azure is intentionally conservative about retries because a repeated submission
can be billable. Each document gets one worker launch per supervisor run and
each Azure request/range gets one application-level submission attempt. A
handled failure, segfault, OOM kill, or other child crash is recorded and the
supervisor advances to the next document. A later normal Stage 3 Azure run is
the retry boundary.

The worker preserves the existing Azure implementation, including artifact
reconciliation, hash verification, Content-Range recovery, and ledger writes.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from regdocs_paths import (
    ANALYZE_LOCK_PATH,
    CONTENT_UNDERSTANDING_DIR,
    DATABASE_PATH,
    DOWNLOAD_FILES_DIR,
    resolve_stored_path,
    stored_path,
)

SCRIPT_VERSION = "3.6.0"
DEFAULT_API_VERSION = "2025-11-01"
DEFAULT_ANALYZER_ID = "prebuilt-layout"
DEFAULT_POLLING_INTERVAL = 3
DEFAULT_WORKER_SLEEP_SECONDS = 0.25
DEFAULT_STATE_FILE = CONTENT_UNDERSTANDING_DIR / "supervisor-state.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_path_component(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in value.strip())
    return cleaned or "unknown"


def _read_lock_pid(path: Path) -> Optional[int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class StageLock:
    """Exclusive lock held by the durable supervisor for the whole Stage 3 run."""

    def __init__(self, path: Path, *, force: bool = False):
        self.path = path
        self.force = force
        self.owned = False

    def __enter__(self) -> "StageLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force and self.path.exists():
            self.path.unlink()
        elif self.path.exists():
            existing_pid = _read_lock_pid(self.path)
            if existing_pid is not None and not _pid_is_running(existing_pid):
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                else:
                    print(
                        f"Removing stale analyze lock: {self.path} "
                        f"(PID {existing_pid} is not running).",
                        file=sys.stderr,
                    )

        payload = {
            "pid": os.getpid(),
            "created_at": utcnow(),
            "role": "azure_supervisor",
            "script_version": SCRIPT_VERSION,
        }
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            detail = ""
            with contextlib.suppress(OSError):
                detail = self.path.read_text(encoding="utf-8")
            raise RuntimeError(
                f"Analyze lock already exists: {self.path}. Confirm no analyzer is running "
                "before using --force-lock."
                + (f"\nLock contents: {detail}" if detail else "")
            ) from exc

        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        self.owned = True
        return self

    def __exit__(self, *_: Any) -> None:
        if self.owned:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self.owned = False


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 2,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "documents": {},
        }
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or not isinstance(state.get("documents", {}), dict):
        raise ValueError(f"Invalid Azure supervisor state: {path}")
    state.setdefault("schema_version", 2)
    state.setdefault("created_at", utcnow())
    state.setdefault("documents", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["schema_version"] = 2
    state["updated_at"] = utcnow()
    atomic_write_json(path, state)


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def canonical_artifact_paths(
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    document_id: str,
    sha256: str,
) -> tuple[Path, Path]:
    analyzer_component = safe_path_component(analyzer_id)
    api_component = safe_path_component(api_version)
    identity = sha256.lower()
    raw_path = (
        output_dir / "raw" / analyzer_component / api_component /
        document_id / f"{identity}.json"
    )
    md_path = (
        output_dir / "markdown" / analyzer_component / api_component /
        document_id / f"{identity}.md"
    )
    return raw_path, md_path


def canonical_success_is_usable(
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    document_id: str,
    sha256: str,
) -> bool:
    """Fast local check used only for supervisor queue selection.

    The worker remains authoritative and performs the full reconciliation audit.
    Requiring both canonical files here intentionally sends legacy/stale success
    rows through a worker once so it can repair/canonicalize them without an
    Azure call.
    """
    raw_path, md_path = canonical_artifact_paths(
        output_dir, analyzer_id, api_version, document_id, sha256
    )
    if not raw_path.is_file() or not md_path.is_file():
        return False
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    actual_analyzer = payload.get("analyzerId") or payload.get("analyzer_id")
    actual_api = payload.get("apiVersion") or payload.get("api_version")
    return actual_analyzer == analyzer_id and actual_api == api_version


def _analysis_columns(con: sqlite3.Connection) -> set[str]:
    try:
        return {str(r[1]) for r in con.execute("PRAGMA table_info(analyses)").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def current_files(con: sqlite3.Connection, document_id: Optional[str]) -> list[sqlite3.Row]:
    params: list[Any] = []
    where = "WHERE f.is_current=1"
    if document_id is not None:
        where += " AND d.id=?"
        params.append(document_id)
    rows = con.execute(
        f"""
        SELECT d.id AS document_id, f.id AS file_id, f.sha256
        FROM files f
        JOIN documents d ON d.id=f.document_id
        {where}
        """,
        params,
    ).fetchall()
    return sorted(
        rows,
        key=lambda r: (
            0, int(str(r["document_id"])), str(r["document_id"])
        ) if str(r["document_id"]).isdigit() else (
            1, str(r["document_id"]).casefold(), str(r["document_id"])
        ),
    )


def matching_analysis_status(
    con: sqlite3.Connection,
    file_id: int,
    sha256: str,
    analyzer_id: str,
    api_version: str,
) -> Optional[sqlite3.Row]:
    needed = {"file_id", "file_sha256", "analyzer_id", "api_version", "status"}
    if not needed.issubset(_analysis_columns(con)):
        return None
    return con.execute(
        """
        SELECT status, error_code, error_message, page_count
        FROM analyses
        WHERE file_id=? AND file_sha256=? AND analyzer_id=? AND api_version=?
        """,
        (file_id, sha256, analyzer_id, api_version),
    ).fetchone()


def select_documents(
    con: sqlite3.Connection,
    state: dict[str, Any],
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    document_id: Optional[str],
    limit: Optional[int],
    force: bool,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for row in current_files(con, document_id):
        doc_id = str(row["document_id"])
        if doc_id in seen:
            continue
        seen.add(doc_id)

        sha256 = str(row["sha256"])
        info = state["documents"].setdefault(doc_id, {})
        if not isinstance(info, dict):
            info = {}
            state["documents"][doc_id] = info

        # 3.5.x used persistent quarantine state. Azure 3.6+ deliberately does
        # not suppress failed documents across runs: a later refresh is the
        # explicit retry boundary.
        if info.pop("quarantined", False):
            info["legacy_quarantine_cleared_at"] = utcnow()
        info.pop("quarantined_at", None)
        info.pop("crash_attempts", None)

        info["file_sha256"] = sha256
        info["analyzer_id"] = analyzer_id
        info["api_version"] = api_version

        if not force:
            analysis = matching_analysis_status(
                con,
                int(row["file_id"]),
                sha256,
                analyzer_id,
                api_version,
            )
            if analysis is not None and analysis["status"] == "SUCCEEDED":
                if canonical_success_is_usable(
                    output_dir,
                    analyzer_id,
                    api_version,
                    doc_id,
                    sha256,
                ):
                    continue

        selected.append(doc_id)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def worker_lock_path(supervisor_lock: Path) -> Path:
    return supervisor_lock.with_name(supervisor_lock.name + ".worker")


def child_command(args: argparse.Namespace, document_id: str) -> list[str]:
    worker = Path(__file__).with_name("regdocs_3_azure_worker.py")
    cmd = [
        sys.executable,
        str(worker),
        "--db", str(args.db),
        "--api-version", args.api_version,
        "--polling-interval", str(args.polling_interval),
        # Cost-safety invariant: the public Azure supervisor never asks its
        # worker to resubmit a failed request/range during the same run.
        "--max-attempts", "1",
        "--download-dir", str(args.download_dir),
        "--output-dir", str(args.output_dir),
        "--lock-file", str(worker_lock_path(args.lock_file)),
        "--analyzer-id", args.analyzer_id,
        "--document-id", document_id,
    ]
    if args.force:
        cmd.append("--force")
    if args.no_reconcile_artifacts:
        cmd.append("--no-reconcile-artifacts")
    if args.no_verify_hash:
        cmd.append("--no-verify-hash")
    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def child_environment(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    if args.endpoint:
        env["CONTENTUNDERSTANDING_ENDPOINT"] = args.endpoint
    if args.key:
        # Keep the API key out of the child command line / process listing.
        env["CONTENTUNDERSTANDING_KEY"] = args.key
    env["CONTENTUNDERSTANDING_API_VERSION"] = args.api_version
    env["CONTENTUNDERSTANDING_ANALYZER_ID"] = args.analyzer_id
    # The worker receives --max-attempts 1 explicitly; remove an inherited
    # override as defense in depth for the public supervisor path.
    env.pop("CONTENTUNDERSTANDING_MAX_ATTEMPTS", None)
    return env


def current_document_analysis(
    con: sqlite3.Connection,
    document_id: str,
    analyzer_id: str,
    api_version: str,
) -> Optional[sqlite3.Row]:
    needed = {"file_id", "file_sha256", "analyzer_id", "api_version", "status"}
    if not needed.issubset(_analysis_columns(con)):
        return None
    return con.execute(
        """
        SELECT a.status, a.error_code, a.error_message, a.page_count
        FROM files f
        JOIN analyses a ON a.file_id=f.id AND a.file_sha256=f.sha256
        WHERE f.is_current=1 AND f.document_id=?
          AND a.analyzer_id=? AND a.api_version=?
        ORDER BY a.id DESC
        LIMIT 1
        """,
        (document_id, analyzer_id, api_version),
    ).fetchone()


def mark_worker_crash(
    con: sqlite3.Connection,
    document_id: str,
    analyzer_id: str,
    api_version: str,
    message: str,
) -> None:
    needed = {
        "file_id", "file_sha256", "analyzer_id", "api_version", "status",
        "finished_at", "error_code", "error_message", "updated_at",
    }
    if not needed.issubset(_analysis_columns(con)):
        return
    now = utcnow()
    con.execute(
        """
        UPDATE analyses
        SET status='FAILED', finished_at=?, error_code='WORKER_CRASH',
            error_message=?, updated_at=?
        WHERE id=(
            SELECT a.id
            FROM files f
            JOIN analyses a ON a.file_id=f.id AND a.file_sha256=f.sha256
            WHERE f.is_current=1 AND f.document_id=?
              AND a.analyzer_id=? AND a.api_version=?
            ORDER BY a.id DESC
            LIMIT 1
        )
        """,
        (now, message[:4000], now, document_id, analyzer_id, api_version),
    )
    con.commit()


def returncode_signal(returncode: int) -> Optional[str]:
    if returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return str(-returncode)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "REGDOCS Stage 3 Azure: durable single-threaded analysis with no "
            "automatic resubmission retries"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--db", type=Path, default=stored_path(DATABASE_PATH))
    p.add_argument("--endpoint", default=os.environ.get("CONTENTUNDERSTANDING_ENDPOINT"))
    p.add_argument("--key", default=os.environ.get("CONTENTUNDERSTANDING_KEY"))
    p.add_argument(
        "--api-version",
        default=os.environ.get("CONTENTUNDERSTANDING_API_VERSION", DEFAULT_API_VERSION),
    )
    p.add_argument(
        "--polling-interval",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_POLLING_INTERVAL", DEFAULT_POLLING_INTERVAL)),
    )
    p.add_argument("--download-dir", type=Path, default=stored_path(DOWNLOAD_FILES_DIR))
    p.add_argument("--output-dir", type=Path, default=stored_path(CONTENT_UNDERSTANDING_DIR))
    p.add_argument("--lock-file", type=Path, default=stored_path(ANALYZE_LOCK_PATH))
    p.add_argument("--force-lock", action="store_true")
    p.add_argument(
        "--state-file",
        type=Path,
        default=stored_path(DEFAULT_STATE_FILE),
        help="Durable supervisor process-history state",
    )
    p.add_argument(
        "--worker-sleep-seconds",
        type=float,
        default=DEFAULT_WORKER_SLEEP_SECONDS,
        help="Pause between document worker processes",
    )
    p.add_argument(
        "--analyzer-id",
        default=os.environ.get("CONTENTUNDERSTANDING_ANALYZER_ID", DEFAULT_ANALYZER_ID),
    )
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--limit", type=int, help="Process at most N eligible documents")
    scope.add_argument("--document-id", help="Analyze one REGDOCS document ID")
    scope.add_argument("--all", dest="all_candidates", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-reconcile-artifacts", action="store_true")
    p.add_argument("--no-verify-hash", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.db = resolve_stored_path(args.db)
    args.download_dir = resolve_stored_path(args.download_dir)
    args.output_dir = resolve_stored_path(args.output_dir)
    args.lock_file = resolve_stored_path(args.lock_file)
    args.state_file = resolve_stored_path(args.state_file)

    if not args.db.is_file():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("ERROR: --limit must be at least 1", file=sys.stderr)
        return 2
    if args.polling_interval <= 0:
        print("ERROR: --polling-interval must be greater than 0", file=sys.stderr)
        return 2
    if args.worker_sleep_seconds < 0:
        print("ERROR: --worker-sleep-seconds cannot be negative", file=sys.stderr)
        return 2

    worker = Path(__file__).with_name("regdocs_3_azure_worker.py")
    if not worker.is_file():
        print(f"ERROR: Azure worker not found: {worker}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(args.state_file)

    try:
        lock = StageLock(args.lock_file, force=args.force_lock)
        lock.__enter__()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    con = open_db(args.db)
    started = time.monotonic()
    try:
        selected = select_documents(
            con,
            state,
            args.output_dir,
            args.analyzer_id,
            args.api_version,
            args.document_id,
            args.limit,
            args.force,
        )
        save_state(args.state_file, state)

        print(f"Azure supervisor {SCRIPT_VERSION}: {len(selected)} document(s) selected")
        print("Concurrency:         1 child process")
        print("Worker launches:     1 per document per run")
        print("Azure app retries:   disabled (1 submission attempt per request/range)")
        print("Retry boundary:      next normal Stage 3 Azure refresh/rerun")
        print(f"Worker:              {worker}")
        print(f"State:               {args.state_file}")
        print(f"Analyzer:            {args.analyzer_id}")
        print(f"API:                 {args.api_version}")

        succeeded = 0
        handled_failed = 0
        crashed = 0
        skipped = 0
        launched = 0

        for index, document_id in enumerate(selected, start=1):
            info = state["documents"].setdefault(document_id, {})
            if not isinstance(info, dict):
                info = {}
                state["documents"][document_id] = info

            info["worker_launches"] = int(info.get("worker_launches") or 0) + 1
            info["last_started_at"] = utcnow()
            info["last_exit_code"] = None
            info["last_signal"] = None
            info["last_run_status"] = "RUNNING"
            save_state(args.state_file, state)

            launched += 1
            print(
                f"[{index}/{len(selected)}] {document_id}: starting one Azure child; "
                "no same-run retry",
                flush=True,
            )

            try:
                result = subprocess.run(
                    child_command(args, document_id),
                    env=child_environment(args),
                    check=False,
                )
                returncode = int(result.returncode)
            except KeyboardInterrupt:
                info["last_run_status"] = "INTERRUPTED"
                save_state(args.state_file, state)
                print(
                    "\nAzure supervisor interrupted; committed worker results and state are preserved.",
                    file=sys.stderr,
                )
                return 130

            info["last_finished_at"] = utcnow()
            info["last_exit_code"] = returncode
            info["last_signal"] = returncode_signal(returncode)
            save_state(args.state_file, state)

            # Re-open after every child so the supervisor sees committed WAL changes.
            con.close()
            con = open_db(args.db)
            analysis = current_document_analysis(
                con, document_id, args.analyzer_id, args.api_version
            )
            status = str(analysis["status"]) if analysis is not None else None
            error_code = str(analysis["error_code"] or "") if analysis is not None else ""

            if returncode == 130:
                info["last_run_status"] = "INTERRUPTED"
                save_state(args.state_file, state)
                return 130
            if returncode == 2:
                info["last_run_status"] = "CONFIG_ERROR"
                save_state(args.state_file, state)
                print(
                    f"    {document_id}: worker configuration/input error; aborting supervisor",
                    file=sys.stderr,
                )
                return 2

            if returncode == 0:
                info["last_run_status"] = status or "NO_ANALYSIS_ROW"
                info["completed_at"] = utcnow()
                save_state(args.state_file, state)
                if status == "SUCCEEDED":
                    succeeded += 1
                    pages = analysis["page_count"] if analysis is not None else None
                    print(f"    {document_id}: SUCCEEDED pages={pages}", flush=True)
                else:
                    skipped += 1
                    print(
                        f"    {document_id}: worker completed exit=0 "
                        f"status={status or 'NO_ANALYSIS_ROW'}",
                        flush=True,
                    )
            elif status == "FAILED" and error_code != "WORKER_CRASH":
                info["last_run_status"] = "FAILED"
                info["completed_at"] = utcnow()
                save_state(args.state_file, state)
                handled_failed += 1
                print(
                    f"    {document_id}: FAILED ({error_code or 'ANALYZE_FAILED'}); "
                    "not retrying this run; next refresh may retry",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                signal_name = info.get("last_signal")
                detail = f" signal={signal_name}" if signal_name else ""
                crash_message = (
                    f"Azure worker exited {returncode}{detail} before recording a normal "
                    "terminal result"
                )
                mark_worker_crash(
                    con,
                    document_id,
                    args.analyzer_id,
                    args.api_version,
                    crash_message,
                )
                info["last_run_status"] = "WORKER_CRASH"
                info["crash_count"] = int(info.get("crash_count") or 0) + 1
                info["last_crashed_at"] = utcnow()
                save_state(args.state_file, state)
                crashed += 1
                print(
                    f"    {document_id}: {crash_message}; not retrying this run; "
                    "next refresh may retry",
                    file=sys.stderr,
                    flush=True,
                )

            if args.worker_sleep_seconds:
                time.sleep(args.worker_sleep_seconds)

        elapsed = time.monotonic() - started
        print()
        print(
            f"Azure supervisor complete: selected={len(selected)} launched={launched} "
            f"succeeded={succeeded} failed={handled_failed} crashed={crashed} "
            f"skipped={skipped} elapsed={elapsed:.1f}s concurrency=1 retries=0"
        )
        return 1 if handled_failed or crashed else 0
    finally:
        con.close()
        lock.__exit__()


if __name__ == "__main__":
    raise SystemExit(main())