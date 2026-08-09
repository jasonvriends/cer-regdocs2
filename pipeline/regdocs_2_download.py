#!/usr/bin/env python3
"""Public Stage 2 entry point for the REGDOCS Atlas release.

Deterministic recovery sidecars are enabled by default for normal Stage 2 runs.
Use ``--no-sidecars`` only when intentionally suppressing that durable recovery
artifact; the internal downloader still owns the sidecar format and write logic.
"""

from __future__ import annotations

import sys

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
    "sidecars_default": True,
}


def stage_args(argv: list[str]) -> list[str]:
    values = list(argv)
    if "--no-sidecars" in values:
        values = [value for value in values if value != "--no-sidecars"]
        return values
    if not any(value in {"--sidecars", "--write-sidecars", "--sidecars-only"} for value in values):
        values.append("--sidecars")
    return values


if __name__ == "__main__":
    raise SystemExit(
        delegate(
            __file__,
            "regdocs_2_download_core.py",
            COMPONENT,
            argv=stage_args(sys.argv[1:]),
        )
    )
