#!/usr/bin/env python3
"""Public Stage 2 entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-2-download"
COMPONENT_VERSION = "1.1.2"
PARSER_VERSION = "document-ledger-download-2026-08-07-v1.1.1-sidecars-docs"
SIDECAR_SCHEMA = "cer-regdocs-document-sidecar"
SIDECAR_SCHEMA_VERSION = 1
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "parser_version": PARSER_VERSION,
    "sidecar_schema": SIDECAR_SCHEMA,
    "sidecar_schema_version": SIDECAR_SCHEMA_VERSION,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_2_download_core.py", COMPONENT))
