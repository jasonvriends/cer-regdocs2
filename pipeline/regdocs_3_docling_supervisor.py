#!/usr/bin/env python3
"""Single-threaded crash-resilient supervisor for REGDOCS Docling analysis.

Exactly one child process runs at a time. Each child analyzes one document via
``regdocs_3_docling.py --document-id ...``. If the child exits abnormally
(including a segfault or OOM kill), the supervisor records the attempt and
continues/retries without losing already committed analyses.
"""

from __future__ import annotations

import argparse
import importlib.metadata
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

from regdocs_paths import ANALYZE_DIR, DATABASE_PATH, DOWNLOAD_DIR, resolve_stored_path, stored_path

SCRIPT_VERSION = "3ds.0.1"
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
        return {"schema_version": 1, "created_at": utcnow(), "updated_at": utcnow(), "documents": {}}
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or not isinstance(state.get("documents", {}), dict):
        raise ValueError(f"Invalid supervisor state: {path}")
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


def select_next(
    con: sqlite3.Connection,
    state: dict[str, Any],
    analyzer_id: str,
    version: str,
    max_attempts: int,
) -> Optional[str]:
    done = successful_ids(con, analyzer_id, version)
    for document_id in current_document_ids(con):
        if document_id in done:
            continue
        info = state["documents"].get(document_id, {})
        if info.get("quarantined"):
            continue
        if int(info.get("attempts") or 0) >= max_attempts:
            continue
        return document_id
    return None


def child_command(args: argparse.Namespace, document_id: str) -> list[str]:
    worker = Path(__file__).with_name("regdocs_3_docling.py")
    return [
        sys.executable,
        str(worker),
        "--db", str(args.db),
        "--download-dir", str(args.download_dir),
        "--output-dir", str(args.output_dir),
        "--analyzer-id", args.analyzer_id,
        "--document-id", document_id,
    ]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Single-threaded REGDOCS Docling supervisor (one document per child process)",
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

        launched = 0
        while True:
            if args.max_documents is not None and launched >= args.max_documents:
                break
            document_id = select_next(con, state, args.analyzer_id, version, args.max_attempts)
            if document_id is None:
                break

            info = state["documents"].setdefault(document_id, {})
            info["attempts"] = int(info.get("attempts") or 0) + 1
            info["last_started_at"] = utcnow()
            info["last_exit_code"] = None
            info["last_signal"] = None
            save_state(args.state_file, state)

            launched += 1
            print(
                f"[{launched}] {document_id}: starting single child "
                f"(attempt {info['attempts']}/{args.max_attempts})",
                flush=True,
            )

            # Deliberately blocking: there is never more than one child process.
            try:
                result = subprocess.run(child_command(args, document_id), check=False)
                returncode = int(result.returncode)
            except KeyboardInterrupt:
                save_state(args.state_file, state)
                print("\nSupervisor interrupted; state preserved.", file=sys.stderr)
                return 130

            info["last_finished_at"] = utcnow()
            info["last_exit_code"] = returncode
            if returncode < 0:
                try:
                    info["last_signal"] = signal.Signals(-returncode).name
                except ValueError:
                    info["last_signal"] = str(-returncode)
            save_state(args.state_file, state)

            # Refresh DB visibility after every child process.
            con.close()
            con = open_db(args.db)
            succeeded = document_id in successful_ids(con, args.analyzer_id, version)

            if succeeded:
                info["completed_at"] = utcnow()
                info["quarantined"] = False
                save_state(args.state_file, state)
                print(f"    {document_id}: SUCCEEDED", flush=True)
            else:
                if int(info["attempts"]) >= args.max_attempts:
                    info["quarantined"] = True
                    info["quarantined_at"] = utcnow()
                    save_state(args.state_file, state)
                    extra = f" signal={info['last_signal']}" if info.get("last_signal") else ""
                    print(
                        f"    {document_id}: exit={returncode}{extra}; QUARANTINED after {info['attempts']} attempts",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    extra = f" signal={info['last_signal']}" if info.get("last_signal") else ""
                    print(
                        f"    {document_id}: exit={returncode}{extra}; will retry before advancing",
                        file=sys.stderr,
                        flush=True,
                    )

            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)

        current = current_document_ids(con)
        done = successful_ids(con, args.analyzer_id, version)
        quarantined = [
            doc_id for doc_id, info in state["documents"].items()
            if isinstance(info, dict) and info.get("quarantined")
        ]
        print(
            f"Supervisor complete: {len(done)}/{len(current)} current documents succeeded; "
            f"{len(quarantined)} quarantined; concurrency=1."
        )
        return 1 if quarantined else 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
