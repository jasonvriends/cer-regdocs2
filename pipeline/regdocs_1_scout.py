#!/usr/bin/env python3
"""Public Stage 1 entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT = {
    "name": "stage-1-scout",
    "version": "1.1.2",
    "parser_version": "document-ledger-scout-2026-08-07-v1-container-tree-audit-progress",
    "schema_version": 2,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_1_scout_core.py", COMPONENT))
