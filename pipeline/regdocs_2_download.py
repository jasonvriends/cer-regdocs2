#!/usr/bin/env python3
"""Public Stage 2 entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT = {
    "name": "stage-2-download",
    "version": "1.1.2",
    "parser_version": "document-ledger-download-2026-08-07-v1.1.1-sidecars-docs",
    "sidecar_schema": "cer-regdocs-document-sidecar",
    "sidecar_schema_version": 1,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_2_download_core.py", COMPONENT))
