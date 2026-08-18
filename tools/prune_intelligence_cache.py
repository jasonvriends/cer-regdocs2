#!/usr/bin/env python3
"""Remove obsolete current-model Stage 6 cache rows before a full publication.

The extraction cache is intentionally durable, but a full corpus run must not
materialize responses for normalized requests that no longer exist. This tool
keeps cache hits for every request still present in the current normalized
corpus and removes only stale rows for the active model/prompt version.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from regdocs_atlas.model_enrichment import PROMPT_VERSION, extraction_batches


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--chunks", required=True, type=Path)
    value.add_argument("--cache", required=True, type=Path)
    value.add_argument("--model", required=True)
    value.add_argument("--max-input-characters", type=int, default=60000)
    value.add_argument("--max-chunks", type=int, default=16)
    return value


def run(args: argparse.Namespace) -> int:
    if not args.chunks.is_file():
        raise FileNotFoundError(f"Expected normalized chunks: {args.chunks}")
    if not args.cache.is_file():
        print("No Stage 6 extraction cache exists yet; nothing to prune.", flush=True)
        return 0
    if args.max_input_characters < 1000:
        raise ValueError("--max-input-characters must be >= 1000")
    if not 1 <= args.max_chunks <= 100:
        raise ValueError("--max-chunks must be 1..100")

    expected = {
        batch.request_hash
        for batch in extraction_batches(
            args.chunks,
            document_ids=None,
            limit_documents=None,
            max_input_characters=args.max_input_characters,
            max_chunks=args.max_chunks,
        )
    }
    if not expected:
        raise ValueError("The normalized corpus produced no Stage 6 extraction requests")

    connection = sqlite3.connect(args.cache, timeout=60)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='extraction_results'"
        ).fetchone()
        if not table:
            print("Stage 6 cache has no extraction_results table yet; nothing to prune.", flush=True)
            return 0

        current = {
            str(row[0])
            for row in connection.execute(
                "SELECT request_hash FROM extraction_results WHERE model=? AND prompt_version=?",
                (args.model, PROMPT_VERSION),
            )
        }
        stale = sorted(current - expected)
        if stale:
            connection.executemany(
                "DELETE FROM extraction_results WHERE request_hash=? AND model=? AND prompt_version=?",
                [(request_hash, args.model, PROMPT_VERSION) for request_hash in stale],
            )
            connection.commit()

        reusable = len(current & expected)
        missing = len(expected - current)
        print(
            f"Stage 6 cache reconciliation: expected={len(expected):,} "
            f"reusable={reusable:,} missing={missing:,} stale_removed={len(stale):,}",
            flush=True,
        )
        return 0
    finally:
        connection.close()


def main() -> int:
    try:
        return run(parser().parse_args())
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
