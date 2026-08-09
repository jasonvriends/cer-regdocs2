#!/usr/bin/env python3
"""Repository-wide REGDOCS Atlas release metadata and SQLite release stamping."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from regdocs_paths import DATABASE_PATH, PROJECT_ROOT, resolve_stored_path, stored_path

VERSION_PATH = PROJECT_ROOT / "VERSION"
RELEASE_NOTES_PATH = PROJECT_ROOT / "RELEASE_NOTES.md"
METADATA_TABLE = "pipeline_metadata"
RELEASE_COLUMN = "release_version"
RELEASE_TRIGGER = "trg_runs_set_release_version"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def release_version() -> str:
    try:
        value = VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Unable to read repository release version: {VERSION_PATH}") from exc
    if not value:
        raise RuntimeError(f"Repository release version is empty: {VERSION_PATH}")
    return value


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_release_schema(con: sqlite3.Connection, version: str) -> dict[str, Any]:
    if not table_exists(con, "runs"):
        raise RuntimeError("SQLite ledger does not contain the required runs table")

    changed = False
    columns = column_names(con, "runs")
    if RELEASE_COLUMN not in columns:
        con.execute(f"ALTER TABLE runs ADD COLUMN {RELEASE_COLUMN} TEXT")
        changed = True

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {METADATA_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    now = utcnow()
    con.execute(
        f"""
        INSERT INTO {METADATA_TABLE}(key, value, updated_at)
        VALUES ('release_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (version, now),
    )

    # The trigger deliberately reads the current release from pipeline_metadata
    # instead of embedding a literal version. A future release therefore only
    # needs to sync the metadata row after VERSION changes.
    con.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {RELEASE_TRIGGER}
        AFTER INSERT ON runs
        FOR EACH ROW
        WHEN NEW.{RELEASE_COLUMN} IS NULL
        BEGIN
            UPDATE runs
            SET {RELEASE_COLUMN} = (
                SELECT value FROM {METADATA_TABLE}
                WHERE key='release_version'
                LIMIT 1
            )
            WHERE id = NEW.id;
        END
        """
    )

    # Record when unified release metadata was first installed without claiming
    # that older historical runs belonged to the current release.
    con.execute(
        f"""
        INSERT OR IGNORE INTO {METADATA_TABLE}(key, value, updated_at)
        VALUES ('release_versioning_started_at', ?, ?)
        """,
        (now, now),
    )
    con.commit()

    historical_without_release = int(
        con.execute(
            f"SELECT COUNT(*) FROM runs WHERE {RELEASE_COLUMN} IS NULL"
        ).fetchone()[0]
    )
    return {
        "release_version": version,
        "schema_changed": changed,
        "historical_runs_without_release": historical_without_release,
    }


def release_status(con: sqlite3.Connection) -> dict[str, Any]:
    version = release_version()
    result: dict[str, Any] = {
        "repository_release": version,
        "version_file": stored_path(VERSION_PATH),
        "release_notes": stored_path(RELEASE_NOTES_PATH),
    }
    if not table_exists(con, "runs"):
        result["database_release_tracking"] = "runs table missing"
        return result
    if RELEASE_COLUMN not in column_names(con, "runs"):
        result["database_release_tracking"] = "not installed"
        return result

    result["database_release_tracking"] = "installed"
    if table_exists(con, METADATA_TABLE):
        row = con.execute(
            f"SELECT value, updated_at FROM {METADATA_TABLE} WHERE key='release_version'"
        ).fetchone()
        if row is not None:
            result["database_current_release"] = row["value"]
            result["database_release_updated_at"] = row["updated_at"]

    rows = con.execute(
        f"""
        SELECT COALESCE({RELEASE_COLUMN}, '<pre-release-versioning>') AS release_version,
               COUNT(*) AS run_count
        FROM runs
        GROUP BY COALESCE({RELEASE_COLUMN}, '<pre-release-versioning>')
        ORDER BY release_version
        """
    ).fetchall()
    result["runs_by_release"] = {
        str(row["release_version"]): int(row["run_count"]) for row in rows
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="REGDOCS Atlas repository release and SQLite release metadata",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default=stored_path(DATABASE_PATH), help="REGDOCS SQLite ledger")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--sync-db",
        action="store_true",
        help="Add/update release metadata in SQLite without rewriting historical run versions",
    )
    group.add_argument("--status", action="store_true", help="Show repository and SQLite release state")
    group.add_argument("--version", action="store_true", help="Print the repository release version")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    version = release_version()
    if args.version or (not args.sync_db and not args.status):
        print(version)
        return 0

    db = resolve_stored_path(args.db)
    if not db.is_file():
        print(f"Database not found: {db}", file=sys.stderr)
        return 1

    con = open_db(db)
    try:
        if args.sync_db:
            result = ensure_release_schema(con, version)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.status:
            print(json.dumps(release_status(con), indent=2, sort_keys=True))
            return 0
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
