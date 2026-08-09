#!/usr/bin/env python3
"""Compatibility launcher for the canonical Stage 5 implementation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from regdocs_atlas.stages.index import COMPONENT_VERSION, DEFAULT_INDEX_NAME, main
from regdocs_atlas.version import release_version

INDEX_CONTRACT = "keyword-filter-facet-v1"


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--version" in args:
        print(release_version())
        raise SystemExit(0)
    if "--diagnostics" in args:
        print(json.dumps({
            "release_version": release_version(),
            "component": "stage-5-index",
            "component_version": COMPONENT_VERSION,
            "index_contract": INDEX_CONTRACT,
            "default_index_name": DEFAULT_INDEX_NAME,
            "implementation": "regdocs_atlas/stages/index.py",
        }, indent=2, sort_keys=True))
        raise SystemExit(0)
    raise SystemExit(main(args))
