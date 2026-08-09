#!/usr/bin/env python3
"""Single-threaded crash-resilient REGDOCS Azure analyzer.

The public Stage 3 Azure command is a supervisor. It never imports the Azure
SDK and never parses source PDFs. Instead it launches exactly one child process
at a time via ``regdocs_3_analyze_worker.py --document-id ...``. If a child
segfaults, is OOM-killed, or otherwise dies before recording a terminal result,
the supervisor survives, records the crash in durable state, retries the same
document, and eventually quarantines it so the remaining queue can continue.

The worker is the previous Stage 3 Azure implementation, including artifact
reconciliation, hash verification, Azure retries, and Content-Range recovery.
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

SCRIPT_VERSION = "3.5.0"
DEFAULT_API_VERSION = "2025-11-01"
DEFAULT_ANALYZER_ID = "prebuilt-layout"
DEFAULT_POLLING_INTERVAL = 3
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_RETRY_BASE_DELAY = 2.0
DEFAULT_RETRY_MAX_DELAY = 30.0
DEFAULT_WORKER_MAX_ATTEMPTS = 3
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
            "schema_version": 1,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "documents": {},
        }
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or not isinstance(state.get("documents", {}), dict):
        raise ValueError(f"Invalid Azure supervisor state: {path}")
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

        identity_changed = (
            (info.get("file_sha256") and info.get("file_sha256") != sha256)
            or (info.get("analyzer_id") and info.get("analyzer_id") != analyzer_id)
            or (info.get("api_version") and info.get("api_version") != api_version)
        )
        if identity_changed:
            info["quarantined"] = False
            info["crash_attempts"] = 0
            info["identity_changed_at"] = utcnow()

        info["file_sha256"] = sha256
        info["analyzer_id"] = analyzer_id
        info["api_version"] = api_version

        if info.get("quarantined"):
            continue

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
    worker = Path(__file__).with_name("regdocs_3_analyze_worker.py")
    cmd = [
        sys.executable,
        str(worker),
        "--db", str(args.db),
        "--api-version", args.api_version,
        "--polling-interval", str(args.polling_interval),
        "--max-attempts", str(args.max_attempts),
        "--retry-base-delay", str(args.retry_base_delay),
        "--retry-max-delay", str(args.retry_max_delay),
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
        description="REGDOCS Stage 3: durable single-threaded Azure Content Understanding analysis",
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
    p.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.environ.get("CONTENTUNDERSTANDING_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
        help="Azure submission attempts inside each worker",
    )
    p.add_argument(
        "--retry-base-delay",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_RETRY_BASE_DELAY", DEFAULT_RETRY_BASE_DELAY)),
    )
    p.add_argument(
        "--retry-max-delay",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_RETRY_MAX_DELAY", DEFAULT_RETRY_MAX_DELAY)),
    )
    p.add_argument("--download-dir", type=Path, default=stored_path(DOWNLOAD_FILES_DIR))
    p.add_argument("--output-dir", type=Path, default=stored_path(CONTENT_UNDERSTANDING_DIR))
    p.add_argument("--lock-file", type=Path, default=stored_path(ANALYZE_LOCK_PATH))
    p.add_argument("--force-lock", action="store_true")
    p.add_argument(
        "--state-file",
        type=Path,
        default=stored_path(DEFAULT_STATE_FILE),
        help="Durable supervisor crash/quarantine state",
    )
    p.add_argument(
        "--worker-max-attempts",
        type=int,
        default=DEFAULT_WORKER_MAX_ATTEMPTS,
        help="Maximum crash-level child attempts before quarantining one document",
    )
    p.add_argument(
        "--worker-sleep-seconds",
        type=float,
        default=DEFAULT_WORKER_SLEEP_SECONDS,
        help="Pause between child processes",
    )
    p.add_argument("--retry-quarantined", action="store_true")
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
    if args.max_attempts < 1:
        print("ERROR: --max-attempts must be at least 1", file=sys.stderr)
        return 2
    if args.worker_max_attempts < 1:
        print("ERROR: --worker-max-attempts must be at least 1", file=sys.stderr)
        return 2
    if args.worker_sleep_seconds < 0:
        print("ERROR: --worker-sleep-seconds cannot be negative", file=sys.stderr)
        return 2
    if args.retry_base_delay < 0 or args.retry_max_delay < 0:
        print("ERROR: retry delays cannot be negative", file=sys.stderr)
        return 2
    if args.retry_max_delay < args.retry_base_delay:
        print("ERROR: --retry-max-delay must be >= --retry-base-delay", file=sys.stderr)
        return 2

    worker = Path(__file__).with_name("regdocs_3_analyze_worker.py")
    if not worker.is_file():
        print(f"ERROR: Azure worker not found: {worker}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(args.state_file)
    if args.retry_quarantined:
        for info in state["documents"].values():
            if isinstance(info, dict) and info.get("quarantined"):
                info["quarantined"] = False
                info["crash_attempts"] = 0
                info["retry_started_at"] = utcnow()
        save_state(args.state_file, state)

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
        quarantined_existing = sorted(
            doc_id for doc_id, info in state["documents"].items()
            if isinstance(info, dict) and info.get("quarantined")
        )

        print(f"Azure supervisor {SCRIPT_VERSION}: {len(selected)} document(s) selected")
        print("Concurrency: 1 child process")
        print(f"Worker:      {worker}")
        print(f"State:       {args.state_file}")
        print(f"Analyzer:    {args.analyzer_id}")
        print(f"API:         {args.api_version}")
        print(f"Crash retry: {args.worker_max_attempts} worker attempt(s) per document")
        if quarantined_existing:
            print(
                f"Quarantined: {len(quarantined_existing)} document(s) skipped; "
                "use --retry-quarantined to retry them"
            )

        succeeded = 0
        handled_failed = 0
        skipped = 0
        quarantined_now = 0
        launched = 0

        for index, document_id in enumerate(selected, start=1):
            info = state["documents"].setdefault(document_id, {})
            while True:
                info["worker_launches"] = int(info.get("worker_launches") or 0) + 1
                info["last_started_at"] = utcnow()
                info["last_exit_code"] = None
                info["last_signal"] = None
                save_state(args.state_file, state)

                launched += 1
                crash_attempt = int(info.get("crash_attempts") or 0) + 1
                print(
                    f"[{index}/{len(selected)}] {document_id}: starting Azure child "
                    f"(crash attempt {crash_attempt}/{args.worker_max_attempts})",
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
                    return 130
                if returncode == 2:
                    print(
                        f"    {document_id}: worker configuration/input error; aborting supervisor",
                        file=sys.stderr,
                    )
                    return 2

                if returncode == 0:
                    info["crash_attempts"] = 0
                    info["quarantined"] = False
                    info["completed_at"] = utcnow()
                    save_state(args.state_file, state)
                    if status == "SUCCEEDED":
                        succeeded += 1
                        pages = analysis["page_count"] if analysis is not None else None
                        print(f"    {document_id}: SUCCEEDED pages={pages}", flush=True)
                    else:
                        skipped += 1
                        print(
                            f"    {document_id}: worker completed exit=0 status={status or 'NO_ANALYSIS_ROW'}",
                            flush=True,
                        )
                    break

                if status == "FAILED" and error_code != "WORKER_CRASH":
                    # The worker survived long enough to record a normal per-document failure.
                    # Azure-level retry policy already ran inside that worker, so advance.
                    info["crash_attempts"] = 0
                    info["quarantined"] = False
                    info["completed_at"] = utcnow()
                    save_state(args.state_file, state)
                    handled_failed += 1
                    print(
                        f"    {document_id}: FAILED normally ({error_code or 'ANALYZE_FAILED'}); advancing",
                        file=sys.stderr,
                        flush=True,
                    )
                    break

                # No terminal DB result means the child died outside normal Python error handling.
                info["crash_attempts"] = int(info.get("crash_attempts") or 0) + 1
                signal_name = info.get("last_signal")
                detail = f" signal={signal_name}" if signal_name else ""
                crash_message = (
                    f"Azure worker exited {returncode}{detail} before recording a terminal result; "
                    f"crash attempt {info['crash_attempts']}/{args.worker_max_attempts}"
                )
                mark_worker_crash(
                    con,
                    document_id,
                    args.analyzer_id,
                    args.api_version,
                    crash_message,
                )

                if int(info["crash_attempts"]) >= args.worker_max_attempts:
                    info["quarantined"] = True
                    info["quarantined_at"] = utcnow()
                    save_state(args.state_file, state)
                    quarantined_now += 1
                    print(
                        f"    {document_id}: {crash_message}; QUARANTINED, continuing queue",
                        file=sys.stderr,
                        flush=True,
                    )
                    break

                save_state(args.state_file, state)
                print(
                    f"    {document_id}: {crash_message}; retrying in a fresh process",
                    file=sys.stderr,
                    flush=True,
                )
                if args.worker_sleep_seconds:
                    time.sleep(args.worker_sleep_seconds)

            if args.worker_sleep_seconds:
                time.sleep(args.worker_sleep_seconds)

        elapsed = time.monotonic() - started
        total_quarantined = sum(
            1 for info in state["documents"].values()
            if isinstance(info, dict) and info.get("quarantined")
        )
        print()
        print(
            f"Azure supervisor complete: selected={len(selected)} launched={launched} "
            f"succeeded={succeeded} normal_failed={handled_failed} skipped={skipped} "
            f"newly_quarantined={quarantined_now} total_quarantined={total_quarantined} "
            f"elapsed={elapsed:.1f}s concurrency=1"
        )
        return 1 if handled_failed or quarantined_now else 0
    finally:
        con.close()
        lock.__exit__()


if __name__ == "__main__":
    raise SystemExit(main())
