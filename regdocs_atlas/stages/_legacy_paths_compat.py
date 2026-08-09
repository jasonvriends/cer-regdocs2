"""Temporary import compatibility while unchanged legacy stage cores move packages.

Remove this module once migrated stages import ``regdocs_atlas.paths`` directly.
"""

import sys
from .. import paths as _paths

sys.modules.setdefault("regdocs_paths", _paths)
