"""Shared runtime primitives for REGDOCS Atlas."""

# Install the narrow Scout facet-console extension before callers import
# ``regdocs_atlas.runtime.console``.  The extension only affects terminal
# presentation; stage execution and persistence are unchanged.
from .console_facet import install as _install_console_facet

_install_console_facet()
del _install_console_facet
