"""Unified command-line surface for the REGDOCS Atlas pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .artifacts import inventory as artifact_inventory
from .artifacts import recovery_plan
from .db import migrate, migration_status, open_ledger, verify_schema
from .paths import DATABASE_PATH, PIPELINE_DIR, PROJECT_ROOT
from .version import release_version

LEGACY_STAGE_SCRIPTS = {
    "scout": "regdocs_1_scout.py",
    "download": "regdocs_2_download.py",
    "azure": "regdocs_3_azure.py",
    "docling": "regdocs_3_docling.py",
    "normalize": "regdocs_4_normalize.py",
    "index": "regdocs_5_index.py",
}

HELP = """REGDOCS Atlas unified pipeline

Usage:
  python pipeline.py version
  python pipeline.py diagnostics
  python pipeline.py status

  python pipeline.py scout [stage options...]
  python pipeline.py download [stage options...]
  python pipeline.py analyze azure [stage options...]
  python pipeline.py analyze docling [stage options...]
  python pipeline.py normalize [--provider azure|docling] [stage options...]
  python pipeline.py index [stage options...]

  python pipeline.py db migrate [--db PATH]
  python pipeline.py db status [--db PATH]
  python pipeline.py db verify [--db PATH]

  python pipeline.py rebuild inventory
  python pipeline.py rebuild plan

The existing pipeline/regdocs_* scripts remain supported compatibility entry points.
"""


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _exec_legacy(script_name: str, args: Sequence[str]) -> int:
    script = PIPELINE_DIR / script_name
    if not script.is_file():
        raise RuntimeError(f"Legacy stage entry point is missing: {script}")
    os.execv(sys.executable, [sys.executable, str(script), *args])
    return 0


def _db_path(args: Sequence[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=str(DATABASE_PATH))
    parsed, unknown = parser.parse_known_args(list(args))
    if unknown:
        raise SystemExit(f"Unknown database option(s): {' '.join(unknown)}")
    return Path(parsed.db).expanduser().resolve()


def _db_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py db migrate|status|verify [--db PATH]")
        return 0
    action = args[0]
    db_path = _db_path(args[1:])
    if action == "migrate":
        con = open_ledger(db_path)
        try:
            result = migrate(con, release_version())
            result["database"] = str(db_path)
            result["verification"] = verify_schema(con)
            _print_json(result)
            return 0 if result["verification"]["ok"] else 1
        finally:
            con.close()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1
    con = open_ledger(db_path, readonly=True)
    try:
        if action == "status":
            result = migration_status(con)
            result["database"] = str(db_path)
            if "pipeline_metadata" in {
                str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }:
                row = con.execute(
                    "SELECT value, updated_at FROM pipeline_metadata WHERE key='release_version'"
                ).fetchone()
                if row is not None:
                    result["database_release_version"] = row["value"]
                    result["database_release_updated_at"] = row["updated_at"]
            _print_json(result)
            return 0
        if action == "verify":
            result = verify_schema(con)
            result["database"] = str(db_path)
            _print_json(result)
            return 0 if result["ok"] else 1
    finally:
        con.close()
    print(f"Unknown db command: {action}", file=sys.stderr)
    return 2


def _status() -> int:
    result: dict[str, object] = {
        "release_version": release_version(),
        "project_root": str(PROJECT_ROOT),
        "artifacts": artifact_inventory().to_dict(),
    }
    if DATABASE_PATH.is_file():
        con = open_ledger(DATABASE_PATH, readonly=True)
        try:
            result["database"] = {
                "path": str(DATABASE_PATH),
                "migrations": migration_status(con),
                "schema": verify_schema(con),
            }
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(runs)").fetchall()}
            release_expr = "release_version" if "release_version" in columns else "NULL AS release_version"
            rows = con.execute(
                f"""
                SELECT id, stage, status, started_at, finished_at,
                       {release_expr}, progress_message
                FROM runs ORDER BY id DESC LIMIT 10
                """
            ).fetchall()
            result["recent_runs"] = [dict(row) for row in rows]
        finally:
            con.close()
    else:
        result["database"] = {"path": str(DATABASE_PATH), "exists": False}
    _print_json(result)
    return 0


def _diagnostics() -> int:
    result: dict[str, object] = {
        "release_version": release_version(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "project_root": str(PROJECT_ROOT),
        "pipeline_dir": str(PIPELINE_DIR),
        "artifacts": artifact_inventory().to_dict(),
    }
    if DATABASE_PATH.is_file():
        con = open_ledger(DATABASE_PATH, readonly=True)
        try:
            result["database_migrations"] = migration_status(con)
            result["database_schema"] = verify_schema(con)
        finally:
            con.close()
    else:
        result["database_migrations"] = {"database_exists": False}
    _print_json(result)
    return 0


def _rebuild_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py rebuild inventory|plan")
        return 0
    if args[0] == "inventory":
        _print_json(artifact_inventory().to_dict())
        return 0
    if args[0] == "plan":
        _print_json(recovery_plan())
        return 0
    print(f"Unknown rebuild command: {args[0]}", file=sys.stderr)
    return 2


def _normalize_args(args: Sequence[str]) -> list[str]:
    values = list(args)
    if "--provider" in values:
        index = values.index("--provider")
        if index + 1 >= len(values):
            raise SystemExit("--provider requires azure or docling")
        provider = values[index + 1]
        if provider not in {"azure", "docling"}:
            raise SystemExit("--provider must be azure or docling")
        values[index:index + 2] = ["--analysis-provider", provider]
    elif values and values[0] in {"azure", "docling"}:
        values = ["--analysis-provider", values[0], *values[1:]]
    return values


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(HELP)
        return 0
    command = args[0]
    rest = args[1:]
    if command in {"version", "--version"}:
        print(release_version())
        return 0
    if command == "diagnostics":
        return _diagnostics()
    if command == "status":
        return _status()
    if command == "db":
        return _db_command(rest)
    if command == "rebuild":
        return _rebuild_command(rest)
    if command in {"scout", "download", "index"}:
        return _exec_legacy(LEGACY_STAGE_SCRIPTS[command], rest)
    if command == "analyze":
        if not rest or rest[0] not in {"azure", "docling"}:
            print("Usage: python pipeline.py analyze azure|docling [stage options...]", file=sys.stderr)
            return 2
        provider = rest[0]
        return _exec_legacy(LEGACY_STAGE_SCRIPTS[provider], rest[1:])
    if command == "normalize":
        return _exec_legacy(LEGACY_STAGE_SCRIPTS["normalize"], _normalize_args(rest))
    print(f"Unknown command: {command}\n\n{HELP}", file=sys.stderr)
    return 2
