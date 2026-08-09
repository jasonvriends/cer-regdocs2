#!/usr/bin/env python3
"""Public Stage 3 Azure entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT = {
    "name": "stage-3-azure",
    "version": "3.7.1",
    "worker_component_version": "3.6.2",
    "analyzer_id": "prebuilt-layout",
    "api_version": "2025-11-01",
    "parser_version": "azure-content-understanding-2025-11-01",
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_3_azure_core.py", COMPONENT))
