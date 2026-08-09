"""CLI adapter for selective Scout recovery execution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .paths import DATABASE_PATH, PIPELINE_LOCK_PATH
from .runtime.locks import ProcessLock
from .scout_recovery import execute_scout_recovery


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py recover scout --execute")
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--db", default=str(DATABASE_PATH))
    parser.add_argument("--priority", choices=("HIGH", "NORMAL", "LOW"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--force-lock", action="store_true")
    args = parser.parse_args(list(argv))
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    # The selective recovery implementation intentionally reuses the proven
    # Scout parser. That core now lives in the package's transitional legacy
    # stage directory rather than under pipeline/.
    legacy_stage_dir = Path(__file__).resolve().parent / "stages" / "legacy"
    if str(legacy_stage_dir) not in sys.path:
        sys.path.insert(0, str(legacy_stage_dir))

    with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:recover_scout", force=args.force_lock):
        result = execute_scout_recovery(
            Path(args.db).expanduser().resolve(),
            priority=args.priority,
            limit=args.limit,
            timeout_seconds=args.timeout,
        )
    print(
        "Scout recovery "
        f"run={result['run_id']} status={result['status']} "
        f"complete={result['completed']} partial={result['partial']} failed={result['failed']}"
    )
    return 0 if int(result["failed"]) == 0 else 2
