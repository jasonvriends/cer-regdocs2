#!/usr/bin/env python3
"""Public Stage 5 index entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT_NAME = "stage-5-index"
COMPONENT_VERSION = "5.0.1"
INDEX_CONTRACT = "keyword-filter-facet-v1"
DEFAULT_INDEX_NAME = "regdocs-chunks"
COMPONENT = {
    "name": COMPONENT_NAME,
    "version": COMPONENT_VERSION,
    "index_contract": INDEX_CONTRACT,
    "default_index_name": DEFAULT_INDEX_NAME,
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_5_index_core.py", COMPONENT))
