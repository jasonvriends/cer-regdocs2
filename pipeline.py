#!/usr/bin/env python3
"""REGDOCS Atlas POC command entry point.

Public stage commands are intentionally action-oriented: naming a stage never
starts work. The root wrapper translates explicit public actions into the
existing proven stage-core flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from regdocs_atlas.cli import main as cli_main
from regdocs_atlas.paths import DATABASE_PATH
from regdocs_atlas.runtime.console import standardized_stage_console
from regdocs_atlas.scout_coverage import refresh_scout_coverage


ROOT_HELP = """REGDOCS Atlas 0.0.1 POC

Bare stage names are safe: they show help and never start work.
See SYNTAX.md for every public switch and examples.

Global:
  python pipeline.py version
  python pipeline.py status [--json]
  python pipeline.py diagnostics
  python pipeline.py cost rates
  python pipeline.py cost azure [--run-id N]

Stages:
  python pipeline.py scout ACTION ...
  python pipeline.py download ACTION ...
  python pipeline.py analyze azure ACTION ...
  python pipeline.py analyze docling ACTION ...
  python pipeline.py normalize ACTION ...
  python pipeline.py index ACTION ...

Database / recovery:
  python pipeline.py db ACTION ...
  python pipeline.py rebuild ACTION ...
  python pipeline.py recover scout ...

Help:
  python pipeline.py help
  python pipeline.py help scout
  python pipeline.py help download
  python pipeline.py help analyze azure
  python pipeline.py help analyze docling
  python pipeline.py help normalize
  python pipeline.py help index
"""

SCOUT_HELP = """Scout

No bare Scout acquisition is allowed.

Actions:
  coverage                 Show/refresh durable completed date ranges; no REGDOCS request
  status [--json]          Show Scout status; no REGDOCS request
  audit                    Verify ledger/raw Scout evidence; no REGDOCS request
  schema                   Check Scout/base schema; no REGDOCS request
  repair                   Repair known containers; contacts REGDOCS and writes evidence
  probe                    Fetch/parse an explicit date range without updating documents;
                           contacts REGDOCS and still preserves runs/errors/raw snapshots
  run                      Acquire an explicit date range and update the ledger/evidence

Both probe and run require:
  --start-date YYYY-MM-DD --end-date YYYY-MM-DD

Examples:
  python pipeline.py scout coverage
  python pipeline.py scout probe --start-date 2026-08-08 --end-date 2026-08-08 --limit 5
  python pipeline.py scout run --start-date 2026-08-08 --end-date 2026-08-09

See SYNTAX.md for all Scout switches.
"""

DOWNLOAD_HELP = """Download

Actions:
  status [--json]          Show current download state; no network
  plan                     Preview eligible downloads; no network and no writes
  sidecars                 Write deterministic sidecars from current SQLite/files; no network
  run                      Reconcile/download eligible source files

Examples:
  python pipeline.py download status
  python pipeline.py download plan --limit 20
  python pipeline.py download sidecars
  python pipeline.py download run

See SYNTAX.md for all Download switches.
"""

AZURE_HELP = """Analyze / Azure Content Understanding

Actions:
  plan                     Select candidates in Azure dry-run mode; NO Azure submission
  run                      Perform Azure Content Understanding analysis; may be billable

Both actions require an explicit scope:
  --all | --limit N | --document-id ID

Examples:
  python pipeline.py analyze azure plan --all
  python pipeline.py analyze azure plan --limit 10
  python pipeline.py analyze azure run --document-id 1234567

Never use run until plan shows the intended candidates.
See SYNTAX.md for all Azure switches.
"""

DOCLING_HELP = """Analyze / Docling

Actions:
  status                   Show current Docling corpus/quarantine state
  run                      Run local Docling conversion in isolated child processes

Docling has no fake/offline dry-run action. Use status, then bound a real local
run with --max-documents when testing.

Examples:
  python pipeline.py analyze docling status
  python pipeline.py analyze docling run --max-documents 1

See SYNTAX.md for all Docling switches.
"""

NORMALIZE_HELP = """Normalize

Actions:
  status                   Show latest Normalize run
  plan                     Resolve selected Stage 3 artifacts without replacing canonical JSONL
  run                      Rebuild canonical normalized JSONL locally

plan and run require --provider azure|docling.

Examples:
  python pipeline.py normalize status
  python pipeline.py normalize plan --provider azure --limit 100
  python pipeline.py normalize run --provider azure

See SYNTAX.md for all Normalize switches.
"""

INDEX_HELP = """Index / Azure AI Search

Actions:
  plan                     Validate/map normalized chunks; Azure Search is not contacted
  publish                  Create/use the index and upload selected chunks
  query TEXT               Query the existing Azure AI Search index; does not publish

Examples:
  python pipeline.py index plan
  python pipeline.py index publish
  python pipeline.py index query "pipeline abandonment" --top 5

See SYNTAX.md for all Index switches.
"""

ANALYZE_HELP = """Analyze

Choose a provider and an explicit action:
  python pipeline.py analyze azure
  python pipeline.py analyze docling

The provider name alone is also safe and shows provider-specific help.
"""

DIRECT_COMMANDS = {"version", "status", "diagnostics", "cost", "db", "rebuild", "recover"}
HELP_FLAGS = {"help", "-h", "--help"}


def _print_help(text: str) -> int:
    print(text.rstrip())
    return 0


def _error(message: str, help_text: str | None = None) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    if help_text:
        print(file=sys.stderr)
        print(help_text.rstrip(), file=sys.stderr)
    return 2


def _has_option(args: Sequence[str], name: str) -> bool:
    return any(value == name or value.startswith(name + "=") for value in args)


def _contains_help(args: Sequence[str]) -> bool:
    return any(value in {"-h", "--help"} for value in args)


def _reject_options(args: Sequence[str], names: set[str], *, use: str) -> int | None:
    for name in names:
        if _has_option(args, name):
            return _error(f"{name} is action-owned in the public CLI. Use: {use}")
    return None


def _require_option(args: Sequence[str], name: str, *, help_text: str) -> int | None:
    if _has_option(args, name):
        return None
    return _error(f"Missing required {name}.", help_text)


def _require_scout_range(args: Sequence[str]) -> int | None:
    if _has_option(args, "--start-date") and _has_option(args, "--end-date"):
        return None
    return _error(
        "Scout probe/run requires both --start-date YYYY-MM-DD and --end-date YYYY-MM-DD. "
        "Check the durable watermark first with: python pipeline.py scout coverage",
        SCOUT_HELP,
    )


def _coverage(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py scout coverage")
    parser.add_argument("--db", default=str(DATABASE_PATH))
    options = parser.parse_args(args)
    result = refresh_scout_coverage(Path(options.db))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def _status_args(args: Sequence[str], *, json_flag: str = "--status-json") -> list[str]:
    values = list(args)
    if "--json" in values:
        values.remove("--json")
        values.insert(0, json_flag)
    else:
        values.insert(0, "--status")
    return values


def _route_scout(rest: list[str]) -> tuple[list[str] | None, bool, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, False, _print_help(SCOUT_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, False, _print_help(SCOUT_HELP)
    if action == "coverage":
        return None, False, _coverage(options)
    if action == "status":
        return ["scout", *_status_args(options)], False, None
    if action == "audit":
        return ["scout", "--audit", *options], False, None
    if action == "schema":
        return ["scout", "--check-schema", *options], False, None
    if action == "repair":
        rejected = _reject_options(
            options,
            {"--audit", "--check-schema", "--status", "--status-json", "--dry-run"},
            use="python pipeline.py scout repair [options]",
        )
        if rejected is not None:
            return None, False, rejected
        return ["scout", "--repair-containers", *options], False, None
    if action in {"probe", "run"}:
        range_error = _require_scout_range(options)
        if range_error is not None:
            return None, False, range_error
        rejected = _reject_options(
            options,
            {"--audit", "--check-schema", "--repair-containers", "--status", "--status-json", "--dry-run"},
            use=(
                "python pipeline.py scout probe --start-date ... --end-date ..."
                if action == "probe"
                else "python pipeline.py scout run --start-date ... --end-date ..."
            ),
        )
        if rejected is not None:
            return None, False, rejected
        translated = ["scout", *options]
        if action == "probe":
            translated.insert(1, "--dry-run")
        return translated, action == "run", None
    return None, False, _error(f"Unknown Scout action: {action}", SCOUT_HELP)


def _route_download(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(DOWNLOAD_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, _print_help(DOWNLOAD_HELP)
    if action == "status":
        return ["download", *_status_args(options)], None
    if action == "plan":
        rejected = _reject_options(
            options,
            {"--dry-run", "--status", "--status-json"},
            use="python pipeline.py download plan [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["download", "--dry-run", *options], None
    if action == "sidecars":
        rejected = _reject_options(
            options,
            {"--status", "--status-json"},
            use="python pipeline.py download sidecars [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["download", "--sidecars-only", *options], None
    if action == "run":
        rejected = _reject_options(
            options,
            {"--dry-run", "--status", "--status-json", "--sidecars-only"},
            use="python pipeline.py download run [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["download", *options], None
    return None, _error(f"Unknown Download action: {action}", DOWNLOAD_HELP)


def _route_azure(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(AZURE_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, _print_help(AZURE_HELP)
    if action not in {"plan", "run"}:
        return None, _error(f"Unknown Azure action: {action}", AZURE_HELP)
    rejected = _reject_options(
        options,
        {"--dry-run"},
        use=f"python pipeline.py analyze azure {action} [--all|--limit N|--document-id ID]",
    )
    if rejected is not None:
        return None, rejected
    if not any(_has_option(options, name) for name in ("--all", "--limit", "--document-id")):
        return None, _error(
            "Azure plan/run requires an explicit scope: --all, --limit N, or --document-id ID.",
            AZURE_HELP,
        )
    translated = ["analyze", "azure", *options]
    if action == "plan":
        translated.insert(2, "--dry-run")
    return translated, None


def _route_docling(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(DOCLING_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, _print_help(DOCLING_HELP)
    if action == "status":
        rejected = _reject_options(
            options,
            {"--status"},
            use="python pipeline.py analyze docling status [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["analyze", "docling", "--status", *options], None
    if action == "run":
        rejected = _reject_options(
            options,
            {"--status"},
            use="python pipeline.py analyze docling run [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["analyze", "docling", *options], None
    return None, _error(f"Unknown Docling action: {action}", DOCLING_HELP)


def _route_analyze(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(ANALYZE_HELP)
    provider, provider_args = rest[0], rest[1:]
    if provider == "azure":
        return _route_azure(provider_args)
    if provider == "docling":
        return _route_docling(provider_args)
    return None, _error(f"Unknown analysis provider: {provider}", ANALYZE_HELP)


def _require_provider(options: Sequence[str]) -> int | None:
    if not _has_option(options, "--provider"):
        return _error("Normalize plan/run requires --provider azure|docling.", NORMALIZE_HELP)
    return None


def _route_normalize(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(NORMALIZE_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, _print_help(NORMALIZE_HELP)
    if action == "status":
        rejected = _reject_options(
            options,
            {"--status", "--dry-run"},
            use="python pipeline.py normalize status [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["normalize", "--status", *options], None
    if action in {"plan", "run"}:
        provider_error = _require_provider(options)
        if provider_error is not None:
            return None, provider_error
        rejected = _reject_options(
            options,
            {"--status", "--dry-run"},
            use=f"python pipeline.py normalize {action} --provider azure|docling [options]",
        )
        if rejected is not None:
            return None, rejected
        translated = ["normalize", *options]
        if action == "plan":
            translated.insert(1, "--dry-run")
        return translated, None
    return None, _error(f"Unknown Normalize action: {action}", NORMALIZE_HELP)


def _route_index(rest: list[str]) -> tuple[list[str] | None, int | None]:
    if not rest or rest[0] in HELP_FLAGS:
        return None, _print_help(INDEX_HELP)
    action, options = rest[0], rest[1:]
    if _contains_help(options):
        return None, _print_help(INDEX_HELP)
    if action == "plan":
        rejected = _reject_options(
            options,
            {"--dry-run", "--query"},
            use="python pipeline.py index plan [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["index", "--dry-run", *options], None
    if action == "publish":
        rejected = _reject_options(
            options,
            {"--dry-run", "--query"},
            use="python pipeline.py index publish [options]",
        )
        if rejected is not None:
            return None, rejected
        return ["index", *options], None
    if action == "query":
        if not options or options[0].startswith("--"):
            return None, _error("Index query requires query text after the action.", INDEX_HELP)
        text, query_options = options[0], options[1:]
        rejected = _reject_options(
            query_options,
            {"--dry-run", "--query", "--recreate-index"},
            use='python pipeline.py index query "text" [--top N] [--filter ODATA]',
        )
        if rejected is not None:
            return None, rejected
        return ["index", "--query", text, *query_options], None
    return None, _error(f"Unknown Index action: {action}", INDEX_HELP)


def _help_for(parts: list[str]) -> int:
    if not parts:
        return _print_help(ROOT_HELP)
    if parts[0] == "scout":
        return _print_help(SCOUT_HELP)
    if parts[0] == "download":
        return _print_help(DOWNLOAD_HELP)
    if parts[0] == "normalize":
        return _print_help(NORMALIZE_HELP)
    if parts[0] == "index":
        return _print_help(INDEX_HELP)
    if parts[0] == "analyze":
        if len(parts) >= 2 and parts[1] == "azure":
            return _print_help(AZURE_HELP)
        if len(parts) >= 2 and parts[1] == "docling":
            return _print_help(DOCLING_HELP)
        return _print_help(ANALYZE_HELP)
    return _print_help(ROOT_HELP)


def _refresh_coverage_after_scout() -> None:
    try:
        refresh_scout_coverage(DATABASE_PATH)
    except Exception as exc:
        print(
            f"Scout coverage refresh warning: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def main() -> int:
    args = list(sys.argv[1:])
    if not args:
        return _print_help(ROOT_HELP)
    if args[0] in HELP_FLAGS:
        return _help_for(args[1:])

    command = args[0]
    translated: list[str] | None = None
    immediate: int | None = None
    refresh_scout = False

    if command == "scout":
        translated, refresh_scout, immediate = _route_scout(args[1:])
    elif command == "download":
        translated, immediate = _route_download(args[1:])
    elif command == "analyze":
        translated, immediate = _route_analyze(args[1:])
    elif command == "normalize":
        translated, immediate = _route_normalize(args[1:])
    elif command == "index":
        translated, immediate = _route_index(args[1:])
    elif command in DIRECT_COMMANDS:
        return int(cli_main(args))
    else:
        return _error(f"Unknown command: {command}", ROOT_HELP)

    if immediate is not None:
        return int(immediate)
    if translated is None:
        return 0

    with standardized_stage_console():
        code = int(cli_main(translated))
    if refresh_scout and code in {0, 2}:
        _refresh_coverage_after_scout()
    return code


if __name__ == "__main__":
    raise SystemExit(main())