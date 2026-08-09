#!/usr/bin/env python3
"""Compatibility launcher for Stage 4 provider selection.

The public entry point is now ``regdocs_4_normalize.py``.  This file remains so
older commands continue to work unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    target = Path(__file__).with_name("regdocs_4_normalize.py")
    os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
