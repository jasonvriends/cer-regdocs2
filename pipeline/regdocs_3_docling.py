#!/usr/bin/env python3
"""Public Stage 3 Docling entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-3-docling"
COMPONENT_VERSION = "3d.3.0"
WORKER_COMPONENT_VERSION = "3d.3.1"
ANALYZER_ID = "docling-standard"
PARSER_VERSION = "regdocs-docling-projection-2026-08-08-v1"
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "worker_component_version": WORKER_COMPONENT_VERSION,
    "analyzer_id": ANALYZER_ID,
    "parser_version": PARSER_VERSION,
    "provider_version": "installed-docling-package-version",
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_3_docling_core.py", COMPONENT))
