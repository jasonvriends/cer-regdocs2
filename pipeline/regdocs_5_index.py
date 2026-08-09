#!/usr/bin/env python3
"""Public Stage 5 index entry point for the REGDOCS Atlas release."""

from __future__ import annotations

from regdocs_entrypoint import delegate

COMPONENT = {
    "name": "stage-5-index",
    "version": "5.0.1",
    "index_contract": "keyword-filter-facet-v1",
    "default_index_name": "regdocs-chunks",
}


if __name__ == "__main__":
    raise SystemExit(delegate(__file__, "regdocs_5_index_core.py", COMPONENT))
