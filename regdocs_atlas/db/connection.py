"""SQLite connection policy for the REGDOCS Atlas ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_ledger(path: Path, *, readonly: bool = False, timeout: float = 60.0) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 60000")
    if not readonly:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def column_names(con: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(con, table):
        return set()
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
