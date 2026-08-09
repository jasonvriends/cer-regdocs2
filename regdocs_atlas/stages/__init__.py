"""REGDOCS Atlas processing stages.

New stage implementations live here. The legacy ``pipeline/`` directory is
being retired one stage at a time while ``pipeline.py`` remains the public CLI.
"""

# Temporary alias for stage cores moved without rewriting their historical
# imports in the same commit. Remove once all migrated stages import
# ``regdocs_atlas.paths`` directly.
from . import _legacy_paths_compat as _legacy_paths_compat
