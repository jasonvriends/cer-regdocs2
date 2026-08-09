"""Unified command-line surface for the REGDOCS Atlas pipeline."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .artifacts import inventory as artifact_inventory
from .artifacts import recovery_plan
from .costs import (
    AzureContentUnderstandingRates,
    azure_run_cost_snapshot,
    latest_provider_run,
    persist_local_compute_cost,
    persist_run_cost,
    pricing_environment_help,
)
from .db import migrate, migration_plan, migration_status, open_ledger, verify_schema
from .db.connection import table_exists
from .db.safety import assert_no_active_stage_locks, backup_database, integrity_report
from .paths import (
    ANALYZE_LOCK_PATH,
    DATABASE_BACKUP_DIR,
    DATABASE_PATH,
    DOWNLOAD_LOCK_PATH,
    NORMALIZE_LOCK_PATH,
    PIPELINE_DIR,
    PIPELINE_LOCK_PATH,
    PIPELINE_LOG_PATH,
    PROJECT_ROOT,
    SCOUT_LOCK_PATH,
)
from .rebuild import rebuild_create, recovery_queue
from .rebuild_compare import compare_ledgers
from .runtime.locks import ProcessLock
from .runtime.presentation import banner, run_line, stage_label
from .scout_manifests import export_scout_manifests
from .version import release_version

LEGACY_STAGE_SCRIPTS = {
    "scout": "regdocs_1_scout.py",
    "download": "regdocs_2_download.py",
    "azure": "regdocs_3_azure.py",
    "docling": "regdocs_3_docling.py",
    "normalize": "regdocs_4_normalize.py",
    "index": "regdocs_5_index.py",
}
STAGE_LOCKS = (SCOUT_LOCK_PATH, DOWNLOAD_LOCK_PATH, ANALYZE_LOCK_PATH, NORMALIZE_LOCK_PATH)
READ_ONLY_STAGE_FLAGS = {"--help", "-h", "--version", "--diagnostics", "--status", "--status-json", "--dry-run"}

HELP = """REGDOCS Atlas unified pipeline

Usage:
  python pipeline.py version
  python pipeline.py diagnostics
  python pipeline.py status [--json]

  python pipeline.py scout [stage options...]
  python pipeline.py download [stage options...]
  python pipeline.py analyze azure [stage options...]
  python pipeline.py analyze docling [stage options...]
  python pipeline.py normalize [--provider azure|docling] [stage options...]
  python pipeline.py index [stage options...]

  python pipeline.py cost azure [--run-id N]
  python pipeline.py cost rates

  python pipeline.py db migrate [--db PATH] [--plan] [--no-backup]
  python pipeline.py db status [--db PATH]
  python pipeline.py db verify [--db PATH]

  python pipeline.py rebuild inventory
  python pipeline.py rebuild plan
  python pipeline.py rebuild prepare [--db database/regdocs.db]
  python pipeline.py rebuild create [--output database/regdocs.rebuilt.db]
  python pipeline.py rebuild verify [--db database/regdocs.rebuilt.db]
  python pipeline.py rebuild compare [--source database/regdocs.db] [--rebuilt database/regdocs.rebuilt.db]

  python pipeline.py recover scout [--db PATH] [--priority HIGH|NORMAL|LOW] [--limit N]

Preferred mutating commands share one orchestration lock and one canonical log:
  database/locks/pipeline.lock
  workspace/pipeline.log

The existing pipeline/regdocs_* scripts remain supported compatibility entry points.
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _append_log(mode: str, text: str) -> None:
    PIPELINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = text.rstrip("\n")
    with PIPELINE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{_utcnow()} [{mode}] {clean}\n")


def _latest_run_id(db_path: Path = DATABASE_PATH) -> int:
    if not db_path.is_file():
        return 0
    con = open_ledger(db_path, readonly=True)
    try:
        if not table_exists(con, "runs"):
            return 0
        return int(con.execute("SELECT COALESCE(MAX(id),0) FROM runs").fetchone()[0])
    finally:
        con.close()


def _read_only_stage(args: Sequence[str]) -> bool:
    return any(value in READ_ONLY_STAGE_FLAGS for value in args)


def _cost_text(cost: dict[str, object]) -> str:
    observed = cost.get("estimated_cost_usd")
    projected = cost.get("projected_total_cost_usd")
    usage = cost.get("usage") if isinstance(cost.get("usage"), dict) else {}
    pages = int(usage.get("document_pages_total", 0) or 0) if isinstance(usage, dict) else 0
    if observed is None:
        return f"COST  n/a  pages_metered={pages:,}  configure rates with: pipeline.py cost rates"
    projected_text = f"${float(projected):,.4f}" if projected is not None else "n/a"
    return f"COST  observed=${float(observed):,.4f}  projected={projected_text}  pages_metered={pages:,}"


def _cost_monitor(provider: str, before_run_id: int, stop: threading.Event, mode: str) -> None:
    last_marker: tuple[int, int] | None = None
    while not stop.wait(2.0):
        try:
            run_id = latest_provider_run(DATABASE_PATH, provider, after_run_id=before_run_id)
            if run_id is None:
                continue
            if provider == "azure":
                snapshot = azure_run_cost_snapshot(DATABASE_PATH, run_id)
                marker = (run_id, int(snapshot.get("documents_with_usage") or 0))
                if marker != last_marker:
                    line = _cost_text(snapshot)
                    print(f"[{mode}] {line}")
                    _append_log(mode, line)
                    last_marker = marker
        except Exception as exc:
            _append_log(mode, f"cost monitor warning: {type(exc).__name__}: {exc}")


def _finalize_provider_cost(provider: str | None, before_run_id: int, mode: str) -> None:
    if provider not in {"azure", "docling"}:
        return
    try:
        run_id = latest_provider_run(DATABASE_PATH, provider, after_run_id=before_run_id)
        if run_id is None:
            return
        if provider == "azure":
            snapshot = azure_run_cost_snapshot(DATABASE_PATH, run_id)
            persist_run_cost(DATABASE_PATH, run_id, snapshot)
            line = _cost_text(snapshot)
        else:
            persist_local_compute_cost(DATABASE_PATH, run_id)
            line = "COST  n/a (local Docling compute)"
        print(f"[{mode}] {line}")
        _append_log(mode, line)
    except Exception as exc:
        _append_log(mode, f"final cost warning: {type(exc).__name__}: {exc}")


def _refresh_scout_manifests(mode: str) -> None:
    try:
        result = export_scout_manifests(DATABASE_PATH, verify_raw=False)
        line = (
            "SCOUT MANIFESTS  "
            f"documents={result['document_manifests_written']:,} "
            f"snapshots={result['snapshot_manifests_written']:,}"
        )
        print(f"[{mode}] {line}")
        _append_log(mode, line)
    except Exception as exc:
        _append_log(mode, f"Scout manifest refresh warning: {type(exc).__name__}: {exc}")


def _run_legacy(script_name: str, args: Sequence[str], *, stage: str, provider: str | None = None) -> int:
    script = PIPELINE_DIR / script_name
    if not script.is_file():
        raise RuntimeError(f"Legacy stage entry point is missing: {script}")
    mode = stage_label(stage, provider)
    print(banner(stage, provider=provider, log_path=str(PIPELINE_LOG_PATH.relative_to(PROJECT_ROOT))))
    _append_log(mode, f"START release={release_version()} command={script_name} args={list(args)!r}")
    command = [sys.executable, str(script), *args]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["REGDOCS_STAGE"] = mode
    env["REGDOCS_PIPELINE_LOG"] = str(PIPELINE_LOG_PATH)
    before_run_id = _latest_run_id()
    read_only = _read_only_stage(args)
    lock_context = contextlib.nullcontext() if read_only else ProcessLock(PIPELINE_LOCK_PATH, role=f"pipeline:{mode.lower()}")
    monitor_stop = threading.Event()
    monitor: threading.Thread | None = None
    return_code = 1
    try:
        with lock_context:
            if provider in {"azure", "docling"} and not read_only:
                monitor = threading.Thread(
                    target=_cost_monitor,
                    args=(provider, before_run_id, monitor_stop, mode),
                    daemon=True,
                )
                monitor.start()
            process = subprocess.Popen(
                command, env=env, stdin=None, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    print(line, end="")
                    _append_log(mode, line)
                return_code = process.wait()
            except KeyboardInterrupt:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(2)
                return_code = process.wait()
            if stage == "scout" and not read_only and return_code in {0, 2}:
                _refresh_scout_manifests(mode)
    finally:
        monitor_stop.set()
        if monitor is not None:
            monitor.join(timeout=3.0)
        if not read_only:
            _finalize_provider_cost(provider, before_run_id, mode)
    _append_log(mode, f"FINISH exit_code={return_code}")
    return int(return_code)


def _db_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=str(DATABASE_PATH))
    return parser


def _migration_args(args: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pipeline.py db migrate")
    parser.add_argument("--db", default=str(DATABASE_PATH))
    parser.add_argument("--plan", action="store_true", help="Show pending migrations without writing")
    parser.add_argument("--no-backup", action="store_true", help="Skip the default consistent SQLite backup")
    parser.add_argument("--backup-dir", default=str(DATABASE_BACKUP_DIR))
    parser.add_argument("--force-lock", action="store_true")
    return parser.parse_args(list(args))


def _db_path(args: Sequence[str]) -> Path:
    parsed, unknown = _db_parser().parse_known_args(list(args))
    if unknown:
        raise SystemExit(f"Unknown database option(s): {' '.join(unknown)}")
    return Path(parsed.db).expanduser().resolve()


def _migration_plan_for(db_path: Path) -> dict[str, object]:
    if db_path.is_file():
        con = open_ledger(db_path, readonly=True)
        try:
            result = migration_plan(con)
        finally:
            con.close()
    else:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        try:
            result = migration_plan(con)
        finally:
            con.close()
    result["database"] = str(db_path)
    result["database_exists"] = db_path.is_file()
    result["backup_would_be_created"] = db_path.is_file()
    return result


def _db_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py db migrate|status|verify [options]")
        return 0
    action = args[0]
    if action == "migrate":
        options = _migration_args(args[1:])
        db_path = Path(options.db).expanduser().resolve()
        if options.plan:
            _print_json(_migration_plan_for(db_path))
            return 0
        with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:database_migration", force=options.force_lock):
            assert_no_active_stage_locks(STAGE_LOCKS)
            backup_path: Path | None = None
            if db_path.is_file() and not options.no_backup:
                backup_path = backup_database(db_path, Path(options.backup_dir), release=release_version())
            con = open_ledger(db_path)
            try:
                result = migrate(con, release_version())
                result["database"] = str(db_path)
                result["backup"] = str(backup_path) if backup_path else None
                result["verification"] = verify_schema(con)
                result["integrity"] = integrity_report(con)
                ok = bool(result["verification"]["ok"]) and bool(result["integrity"]["ok"])
                result["ok"] = ok
                _print_json(result)
                return 0 if ok else 1
            finally:
                con.close()
    db_path = _db_path(args[1:])
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1
    con = open_ledger(db_path, readonly=True)
    try:
        if action == "status":
            result = migration_status(con)
            result["database"] = str(db_path)
            if table_exists(con, "pipeline_metadata"):
                row = con.execute("SELECT value, updated_at FROM pipeline_metadata WHERE key='release_version'").fetchone()
                if row is not None:
                    result["database_release_version"] = row["value"]
                    result["database_release_updated_at"] = row["updated_at"]
            _print_json(result)
            return 0
        if action == "verify":
            result = verify_schema(con)
            result["integrity"] = integrity_report(con)
            result["database"] = str(db_path)
            result["ok"] = bool(result["ok"]) and bool(result["integrity"]["ok"])
            _print_json(result)
            return 0 if result["ok"] else 1
    finally:
        con.close()
    print(f"Unknown db command: {action}", file=sys.stderr)
    return 2


def _run_provider(row: sqlite3.Row) -> str | None:
    try:
        params = json.loads(row["parameters_json"] or "{}")
    except (json.JSONDecodeError, KeyError):
        params = {}
    if isinstance(params, dict) and params.get("provider"):
        return str(params["provider"])
    if str(row["stage"] or "") == "analyze_docling":
        return "docling"
    return None


def _status(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py status", add_help=False)
    parser.add_argument("--json", action="store_true")
    options, unknown = parser.parse_known_args(list(args))
    if unknown:
        raise SystemExit(f"Unknown status option(s): {' '.join(unknown)}")
    result: dict[str, object] = {
        "release_version": release_version(), "project_root": str(PROJECT_ROOT),
        "artifacts": artifact_inventory().to_dict(), "pipeline_lock": str(PIPELINE_LOCK_PATH),
        "pipeline_log": str(PIPELINE_LOG_PATH),
    }
    recent: list[dict[str, object]] = []
    if DATABASE_PATH.is_file():
        con = open_ledger(DATABASE_PATH, readonly=True)
        try:
            result["database"] = {
                "path": str(DATABASE_PATH), "migrations": migration_status(con),
                "schema": verify_schema(con),
            }
            if table_exists(con, "runs"):
                columns = {str(r[1]) for r in con.execute("PRAGMA table_info(runs)").fetchall()}
                release_expr = "release_version" if "release_version" in columns else "NULL AS release_version"
                rows = con.execute(
                    f"""SELECT id,stage,status,started_at,finished_at,{release_expr},progress_message,
                           completed_units,total_units,parameters_json,summary_json
                    FROM runs ORDER BY id DESC LIMIT 10"""
                ).fetchall()
                for row in rows:
                    item: dict[str, object] = dict(row)
                    provider = _run_provider(row)
                    item["provider"] = provider
                    try:
                        summary = json.loads(row["summary_json"] or "{}")
                    except json.JSONDecodeError:
                        summary = {}
                    cost = summary.get("cost") if isinstance(summary, dict) else None
                    if row["status"] == "RUNNING" and provider == "azure":
                        with contextlib.suppress(Exception):
                            cost = azure_run_cost_snapshot(DATABASE_PATH, int(row["id"]))
                    elif provider == "docling" and not isinstance(cost, dict):
                        cost = {"pricing_status": "n/a_local_compute", "estimated_cost_usd": None}
                    item["cost"] = cost
                    recent.append(item)
                result["recent_runs"] = recent
            else:
                result["recent_runs"] = []
            if table_exists(con, "recovery_tasks"):
                rows = con.execute("SELECT status,priority,COUNT(*) AS n FROM recovery_tasks GROUP BY status,priority").fetchall()
                result["recovery_tasks"] = [dict(row) for row in rows]
        finally:
            con.close()
    else:
        result["database"] = {"path": str(DATABASE_PATH), "exists": False}
        result["recent_runs"] = []
    if options.json:
        _print_json(result)
        return 0
    print(banner("status", log_path=str(PIPELINE_LOG_PATH.relative_to(PROJECT_ROOT))))
    print(f"DB    {DATABASE_PATH.relative_to(PROJECT_ROOT)}")
    print(f"LOCK  {PIPELINE_LOCK_PATH.relative_to(PROJECT_ROOT)}")
    print()
    if not recent:
        print("No pipeline runs recorded.")
        return 0
    print("RECENT RUNS")
    for item in recent:
        print(run_line(item))
        message = str(item.get("progress_message") or "").strip()
        if message:
            print(f"          {message}")
        cost = item.get("cost")
        if isinstance(cost, dict):
            if item.get("provider") == "azure":
                print(f"          {_cost_text(cost)}")
            elif item.get("provider") == "docling":
                print("          COST  n/a (local Docling compute)")
    return 0


def _diagnostics() -> int:
    result: dict[str, object] = {
        "release_version": release_version(), "python_version": sys.version.split()[0],
        "python_executable": sys.executable, "project_root": str(PROJECT_ROOT),
        "pipeline_dir": str(PIPELINE_DIR), "artifacts": artifact_inventory().to_dict(),
        "migration_backup_dir": str(DATABASE_BACKUP_DIR), "pipeline_lock": str(PIPELINE_LOCK_PATH),
        "pipeline_log": str(PIPELINE_LOG_PATH), "azure_cost_rate_environment": pricing_environment_help(),
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


def _cost_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py cost azure [--run-id N] | cost rates")
        return 0
    if args[0] == "rates":
        _print_json({"environment": pricing_environment_help(), "current": AzureContentUnderstandingRates.from_env().to_dict()})
        return 0
    if args[0] != "azure":
        print(f"Unknown cost provider: {args[0]}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="pipeline.py cost azure")
    parser.add_argument("--run-id", type=int)
    options = parser.parse_args(list(args[1:]))
    run_id = options.run_id if options.run_id is not None else latest_provider_run(DATABASE_PATH, "azure")
    if run_id is None:
        print("No Azure analysis run found.", file=sys.stderr)
        return 1
    _print_json(azure_run_cost_snapshot(DATABASE_PATH, run_id))
    return 0


def _rebuild_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py rebuild inventory|plan|prepare|create|verify|compare")
        return 0
    action = args[0]
    if action == "inventory":
        _print_json(artifact_inventory().to_dict())
        return 0
    if action == "plan":
        _print_json(recovery_plan())
        return 0
    if action == "prepare":
        parser = argparse.ArgumentParser(prog="pipeline.py rebuild prepare")
        parser.add_argument("--db", default=str(DATABASE_PATH))
        parser.add_argument("--no-verify-raw", action="store_true")
        options = parser.parse_args(list(args[1:]))
        with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:rebuild_prepare"):
            assert_no_active_stage_locks(STAGE_LOCKS)
            result = export_scout_manifests(
                Path(options.db), verify_raw=not options.no_verify_raw
            )
            _print_json(result)
            return 0 if result.get("ok") else 2
    if action == "create":
        parser = argparse.ArgumentParser(prog="pipeline.py rebuild create")
        parser.add_argument("--output", default=str(PROJECT_ROOT / "database" / "regdocs.rebuilt.db"))
        options = parser.parse_args(list(args[1:]))
        with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:rebuild"):
            assert_no_active_stage_locks(STAGE_LOCKS)
            _print_json(rebuild_create(Path(options.output)))
        return 0
    if action == "verify":
        parser = argparse.ArgumentParser(prog="pipeline.py rebuild verify")
        parser.add_argument("--db", default=str(PROJECT_ROOT / "database" / "regdocs.rebuilt.db"))
        options = parser.parse_args(list(args[1:]))
        db_path = Path(options.db).expanduser().resolve()
        if not db_path.is_file():
            print(f"Database not found: {db_path}", file=sys.stderr)
            return 1
        con = open_ledger(db_path, readonly=True)
        try:
            result = {"database": str(db_path), "schema": verify_schema(con), "integrity": integrity_report(con)}
            result["ok"] = bool(result["schema"]["ok"]) and bool(result["integrity"]["ok"])
            _print_json(result)
            return 0 if result["ok"] else 1
        finally:
            con.close()
    if action == "compare":
        parser = argparse.ArgumentParser(prog="pipeline.py rebuild compare")
        parser.add_argument("--source", default=str(DATABASE_PATH))
        parser.add_argument("--rebuilt", default=str(PROJECT_ROOT / "database" / "regdocs.rebuilt.db"))
        options = parser.parse_args(list(args[1:]))
        result = compare_ledgers(Path(options.source), Path(options.rebuilt))
        _print_json(result)
        return 0 if result["source_and_stage3_equivalent"] else 2
    print(f"Unknown rebuild command: {action}", file=sys.stderr)
    return 2


def _recover_command(args: Sequence[str]) -> int:
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python pipeline.py recover scout [--db PATH] [--priority ...] [--limit N] [--ids-only]")
        return 0
    if args[0] != "scout":
        print(f"Unknown recovery type: {args[0]}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="pipeline.py recover scout")
    parser.add_argument("--db", default=str(DATABASE_PATH))
    parser.add_argument("--priority", choices=("HIGH", "NORMAL", "LOW"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids-only", action="store_true")
    options = parser.parse_args(list(args[1:]))
    result = recovery_queue(Path(options.db), priority=options.priority, limit=options.limit)
    if options.ids_only:
        for task in result["tasks"]:
            print(task["document_id"])
    else:
        _print_json(result)
    return 0


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
        return _status(rest)
    if command == "cost":
        return _cost_command(rest)
    if command == "db":
        return _db_command(rest)
    if command == "rebuild":
        return _rebuild_command(rest)
    if command == "recover":
        return _recover_command(rest)
    if command == "scout":
        return _run_legacy(LEGACY_STAGE_SCRIPTS["scout"], rest, stage="scout")
    if command == "download":
        return _run_legacy(LEGACY_STAGE_SCRIPTS["download"], rest, stage="download")
    if command == "index":
        return _run_legacy(LEGACY_STAGE_SCRIPTS["index"], rest, stage="index")
    if command == "analyze":
        if not rest or rest[0] not in {"azure", "docling"}:
            print("Usage: python pipeline.py analyze azure|docling [stage options...]", file=sys.stderr)
            return 2
        provider = rest[0]
        return _run_legacy(LEGACY_STAGE_SCRIPTS[provider], rest[1:], stage="analyze", provider=provider)
    if command == "normalize":
        normalized = _normalize_args(rest)
        provider: str | None = None
        if "--analysis-provider" in normalized:
            provider = normalized[normalized.index("--analysis-provider") + 1]
        return _run_legacy(LEGACY_STAGE_SCRIPTS["normalize"], normalized, stage="normalize", provider=provider)
    print(f"Unknown command: {command}\n\n{HELP}", file=sys.stderr)
    return 2
