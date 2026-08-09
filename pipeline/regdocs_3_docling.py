#!/usr/bin/env python3
"""Public Stage 3 Docling entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT = {
    "name": "stage-3-docling",
    "version": "3d.3.0",
    "worker_component_version": "3d.3.1",
    "analyzer_id": "docling-standard",
    "parser_version": "regdocs-docling-projection-2026-08-08-v1",
    "provider_version": "installed-docling-package-version",
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_3_docling_core.py", COMPONENT))
