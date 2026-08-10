"""Pilot CLI for durable rebuild preparation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .analysis_manifests import export_analysis_manifests
from .db.safety import assert_no_active_stage_locks
from .paths import (
    ANALYZE_LOCK_PATH,
    DATABASE_PATH,
    DOWNLOAD_LOCK_PATH,
    NORMALIZE_LOCK_PATH,
    PIPELINE_LOCK_PATH,
    SCOUT_LOCK_PATH,
)
from .runtime.locks import ProcessLock
from .scout_coverage import refresh_scout_coverage
from .scout_manifests import export_scout_manifests

STAGE_LOCKS = (SCOUT_LOCK_PATH, DOWNLOAD_LOCK_PATH, ANALYZE_LOCK_PATH, NORMALIZE_LOCK_PATH)


def main(args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.py rebuild prepare")
    parser.add_argument("--db", default=str(DATABASE_PATH))
    parser.add_argument("--no-verify-raw", action="store_true")
    parser.add_argument("--no-verify-analysis", action="store_true")
    options = parser.parse_args(list(args) if args is not None else None)
    db_path = Path(options.db).expanduser().resolve()
    with ProcessLock(PIPELINE_LOCK_PATH, role="pipeline:rebuild_prepare"):
        assert_no_active_stage_locks(STAGE_LOCKS)
        scout = export_scout_manifests(db_path, verify_raw=not options.no_verify_raw)
        coverage = refresh_scout_coverage(db_path)
        stage3 = export_analysis_manifests(
            db_path, verify_artifacts=not options.no_verify_analysis
        )
    result = dict(scout)
    result["scout_coverage"] = coverage
    result["stage3_analysis"] = stage3
    result["ok"] = bool(scout.get("ok")) and bool(stage3.get("ok"))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
