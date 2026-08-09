#!/usr/bin/env python3
"""Stage 4 provider selector.

Thin compatibility launcher for the first multi-provider implementation. Azure
keeps the existing Stage 4 defaults. Docling resolves the newest successful
Docling version present in the ledger, then delegates to regdocs_4_normalize.
The normalizer remains the single implementation of the normalized corpus
contract.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import regdocs_4_normalize as normalize
from regdocs_paths import DATABASE_PATH, resolve_stored_path, stored_path

AZURE_ANALYZER = "prebuilt-layout"
AZURE_VERSION = "2025-11-01"
DOCLING_ANALYZER = "docling-standard"


def latest_docling_version(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            """
            SELECT api_version
            FROM analyses
            WHERE analyzer_id=? AND status='SUCCEEDED'
            ORDER BY id DESC
            LIMIT 1
            """,
            (DOCLING_ANALYZER,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError(
            "No successful Docling analysis exists in the ledger. Run "
            "pipeline/regdocs_3_docling.py first."
        )
    return str(row[0])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="REGDOCS Stage 4: choose Azure or Docling analysis input",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
    )
    p.add_argument("--analysis-provider", choices=("azure", "docling"), default="azure")
    p.add_argument("--db", default=stored_path(DATABASE_PATH))
    p.add_argument("--help", action="store_true")
    return p


def main() -> int:
    selector, remaining = build_parser().parse_known_args()
    if selector.help:
        build_parser().print_help()
        print("\nAll remaining options are passed through to regdocs_4_normalize.py.")
        print("Examples:")
        print("  python pipeline/regdocs_4_normalize_provider.py --analysis-provider azure --limit 10 --dry-run")
        print("  python pipeline/regdocs_4_normalize_provider.py --analysis-provider docling --limit 10 --dry-run")
        return 0

    db = resolve_stored_path(selector.db)
    if selector.analysis_provider == "azure":
        analyzer_id = AZURE_ANALYZER
        api_version = AZURE_VERSION
    else:
        analyzer_id = DOCLING_ANALYZER
        api_version = latest_docling_version(db)

    delegated = [
        "regdocs_4_normalize.py",
        "--db", str(db),
        "--analyzer-id", analyzer_id,
        "--api-version", api_version,
        *remaining,
    ]
    print(
        f"Analysis provider: {selector.analysis_provider}; "
        f"analyzer={analyzer_id}; version={api_version}"
    )
    parser = normalize.build_parser()
    args = parser.parse_args(delegated[1:])
    return normalize.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
