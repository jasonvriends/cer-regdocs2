#!/usr/bin/env python3
"""Public Stage 3 Azure entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-3-azure"
COMPONENT_VERSION = "3.7.1"
WORKER_COMPONENT_VERSION = "3.6.2"
ANALYZER_ID = "prebuilt-layout"
API_VERSION = "2025-11-01"
PARSER_VERSION = "azure-content-understanding-2025-11-01"
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "worker_component_version": WORKER_COMPONENT_VERSION,
    "analyzer_id": ANALYZER_ID,
    "api_version": API_VERSION,
    "parser_version": PARSER_VERSION,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_3_azure_core.py", COMPONENT))
