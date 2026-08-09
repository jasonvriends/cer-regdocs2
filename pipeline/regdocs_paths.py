#!/usr/bin/env python3
"""Compatibility export for the canonical ``regdocs_atlas.paths`` contract.

Legacy stage implementations import ``regdocs_paths`` because they execute from
``pipeline/``. Keep that import stable while owning path definitions in one
package module.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regdocs_atlas.paths import *  # noqa: F401,F403,E402
