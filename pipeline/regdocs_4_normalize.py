#!/usr/bin/env python3
"""Public Stage 4 normalize entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-4-normalize"
COMPONENT_VERSION = "4.2.0"
WORKER_COMPONENT_VERSION = "4.1.0"
PARSER_VERSION = "regdocs-normalizer-2026-08-08-v2"
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "worker_component_version": WORKER_COMPONENT_VERSION,
    "parser_version": PARSER_VERSION,
    "providers": ["azure", "docling"],
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_4_normalize_core.py", COMPONENT))
