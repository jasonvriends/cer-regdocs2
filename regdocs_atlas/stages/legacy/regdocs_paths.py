#!/usr/bin/env python3
"""Compatibility export for unchanged migrated stage cores."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from regdocs_atlas.paths import *  # noqa: F401,F403,E402
