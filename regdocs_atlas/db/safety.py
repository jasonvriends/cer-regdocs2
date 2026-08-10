"""Safety helpers for SQLite schema migrations and ledger backups."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..runtime.locks import pid_is_running, read_lock_pid


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def assert_no_active_stage_locks(lock_paths: Iterable[Path]) -> None:
    blockers: list[str] = []
    for path in lock_paths:
        if not path.exists():
            continue
        pid = read_lock_pid(path)
        if pid is None:
            blockers.append(f"{path} (unreadable/unknown owner)")
        elif pid_is_running(pid):
            blockers.append(f"{path} (PID {pid} is running)")
    if blockers:
        raise RuntimeError(
            "Refusing database schema migration while pipeline work may be active:\n  - "
            + "\n  - ".join(blockers)
        )


def backup_database(source: Path, backup_dir: Path, *, release: str) -> Path:
    """Create a transactionally consistent SQLite backup, including WAL state."""
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    backup_dir = backup_dir.expanduser().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    base = f"{source.stem}-before-{release}-{utc_stamp()}"
    destination = backup_dir / f"{base}.db"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"{base}-{counter}.db"
        counter += 1

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60.0)
    dst = sqlite3.connect(destination, timeout=60.0)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(f"file:{destination}?mode=ro", uri=True, timeout=60.0)
    try:
        rows = [str(row[0]) for row in check.execute("PRAGMA integrity_check").fetchall()]
        if rows != ["ok"]:
            raise RuntimeError(f"Backup integrity_check failed for {destination}: {rows[:5]}")
    finally:
        check.close()
    return destination


def integrity_report(con: sqlite3.Connection) -> dict[str, object]:
    integrity = [str(row[0]) for row in con.execute("PRAGMA integrity_check").fetchall()]
    foreign_keys = [tuple(row) for row in con.execute("PRAGMA foreign_key_check").fetchall()]
    return {
        "ok": integrity == ["ok"] and not foreign_keys,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "foreign_key_examples": foreign_keys[:20],
    }
