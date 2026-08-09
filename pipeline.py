#!/usr/bin/env python3
"""REGDOCS Atlas POC command entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from regdocs_atlas.cli import main as cli_main
from regdocs_atlas.paths import DATABASE_PATH
from regdocs_atlas.scout_coverage import refresh_scout_coverage


SCOUT_READ_ONLY_FLAGS = {
    "--help", "-h", "--version", "--diagnostics", "--status", "--status-json", "--dry-run"
}


def _coverage(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py scout --coverage")
    parser.add_argument("--db", default=str(DATABASE_PATH))
    options = parser.parse_args(args)
    result = refresh_scout_coverage(Path(options.db))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def main() -> int:
    args = list(sys.argv[1:])
    if args and args[0] == "scout":
        if len(args) >= 2 and args[1] == "coverage":
            return _coverage(args[2:])
        if "--coverage" in args[1:]:
            return _coverage([value for value in args[1:] if value != "--coverage"])

    code = int(cli_main(args))

    # Keep the durable coverage watermark in workspace rather than relying on
    # historical runs that may later be removed by a flat rebuild.
    if (
        args
        and args[0] == "scout"
        and code in {0, 2}
        and not any(flag in args[1:] for flag in SCOUT_READ_ONLY_FLAGS)
    ):
        try:
            refresh_scout_coverage(DATABASE_PATH)
        except Exception as exc:
            print(
                f"Scout coverage refresh warning: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
