#!/usr/bin/env python3
"""Compatibility release command backed by the central migration engine."""

from __future__ import annotations

import argparse
import json
import os
import sys

from regdocs_paths import DATABASE_PATH, PROJECT_ROOT, resolve_stored_path, stored_path
from regdocs_atlas.db import migration_status, open_ledger
from regdocs_atlas.db.connection import column_names, table_exists
from regdocs_atlas.version import RELEASE_NOTES_PATH, VERSION_PATH, release_version


def release_status(con):
    version = release_version()
    result = {
        "repository_release": version,
        "version_file": stored_path(VERSION_PATH),
        "release_notes": stored_path(RELEASE_NOTES_PATH),
        "migrations": migration_status(con),
    }
    if not table_exists(con, "runs"):
        result["database_release_tracking"] = "runs table missing"
        return result
    if "release_version" not in column_names(con, "runs"):
        result["database_release_tracking"] = "not installed"
        return result

    result["database_release_tracking"] = "installed"
    if table_exists(con, "pipeline_metadata"):
        row = con.execute(
            "SELECT value, updated_at FROM pipeline_metadata WHERE key='release_version'"
        ).fetchone()
        if row is not None:
            result["database_current_release"] = row["value"]
            result["database_release_updated_at"] = row["updated_at"]

    rows = con.execute(
        """
        SELECT COALESCE(release_version, '<pre-release-versioning>') AS release_version,
               COUNT(*) AS run_count
        FROM runs
        GROUP BY COALESCE(release_version, '<pre-release-versioning>')
        ORDER BY release_version
        """
    ).fetchall()
    result["runs_by_release"] = {
        str(row["release_version"]): int(row["run_count"]) for row in rows
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="REGDOCS Atlas repository release and SQLite migration metadata",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default=stored_path(DATABASE_PATH), help="REGDOCS SQLite ledger")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--sync-db",
        action="store_true",
        help="Compatibility alias: run the safe central database migration command",
    )
    group.add_argument("--status", action="store_true", help="Show repository, migration, and SQLite release state")
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

    if args.sync_db:
        target = PROJECT_ROOT / "pipeline.py"
        os.execv(
            sys.executable,
            [sys.executable, str(target), "db", "migrate", "--db", str(db)],
        )
        return 0

    con = open_ledger(db, readonly=True)
    try:
        print(json.dumps(release_status(con), indent=2, sort_keys=True))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
