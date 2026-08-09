#!/usr/bin/env python3
"""Public Stage 1 entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-1-scout"
COMPONENT_VERSION = "1.1.2"
PARSER_VERSION = "document-ledger-scout-2026-08-07-v1-container-tree-audit-progress"
SCHEMA_VERSION = 2
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "parser_version": PARSER_VERSION,
    "schema_version": SCHEMA_VERSION,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_1_scout_core.py", COMPONENT))
