"""Pilot CLI for manifest-backed database reconstruction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .db.safety import assert_no_active_stage_locks
from .paths import (
    ANALYZE_LOCK_PATH,
    DOWNLOAD_LOCK_PATH,
    NORMALIZE_LOCK_PATH,
    PIPELINE_LOCK_PATH,
    PROJECT_ROOT,
    SCOUT_LOCK_PATH,
)
from .rebuild_manifest_overlay import rebuild_create
from .runtime.locks import ProcessLock

STAGE_LOCKS = (SCOUT_LOCK_PATH, DOWNLOAD_LOCK_PATH, ANALYZE_LOCK_PATH, NORMALIZE_LOCK_PATH)


def main(args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py rebuild create")
    parser.add_argument("--output")
    parser.add_argument(
        "--flat",
        action="store_true",
        help=(
            "Create a clean Stage 1-3 operational baseline from durable workspace "
            "artifacts, discarding historical runs/errors/recovery bookkeeping"
        ),
    )
    options = parser.parse_args(list(args) if args is not None else None)
    default_name = "regdocs.flat.db" if options.flat else "regdocs.rebuilt.db"
    output = Path(options.output) if options.output else PROJECT_ROOT / "database" / default_name

    with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:rebuild"):
        assert_no_active_stage_locks(STAGE_LOCKS)
        if options.flat:
            from .flatten import flatten_create
            result = flatten_create(output)
        else:
            result = rebuild_create(output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
