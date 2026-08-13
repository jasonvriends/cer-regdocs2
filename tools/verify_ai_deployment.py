#!/usr/bin/env python3
"""Verify a REGDOCS hybrid Search deployment before application cutover."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from regdocs_atlas.paths import NORMALIZE_DIR, resolve_stored_path, stored_path
from regdocs_atlas.stages.index import credential


def normalized_count(path: Path) -> int:
    chunks = path / "chunks.jsonl"
    if not chunks.is_file():
        raise FileNotFoundError(f"Missing {chunks}")
    with chunks.open("rb") as stream:
        return sum(block.count(b"\n") for block in iter(lambda: stream.read(8 * 1024 * 1024), b""))


def one_search(client: Any, query: str, mode: str, vector_field: str, semantic: Optional[str]) -> dict[str, Any]:
    from azure.search.documents.models import VectorizableTextQuery

    options: dict[str, Any] = {
        "top": 3,
        "select": ["chunk_id", "document_id", "page_start", "title", "heading"],
        "query_type": "simple",
    }
    if mode == "hybrid":
        options["vector_queries"] = [VectorizableTextQuery(text=query, fields=vector_field, k_nearest_neighbors=50)]
        options["vector_filter_mode"] = "preFilter"
        if semantic:
            options.update(
                query_type="semantic",
                semantic_configuration_name=semantic,
                semantic_error_mode="partial",
            )
    started = time.perf_counter()
    results = [
        {
            "chunk_id": str(row.get("chunk_id") or ""),
            "document_id": str(row.get("document_id") or ""),
            "page_start": row.get("page_start"),
            "title": row.get("heading") or row.get("title"),
        }
        for row in client.search(query, **options)
    ]
    return {"ok": bool(results), "latency_ms": round((time.perf_counter() - started) * 1000, 2), "results": results}


def verify(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    expected_count = args.expected_count if args.expected_count is not None else normalized_count(args.normalized_dir)
    cred = credential(args.api_key)
    index_client = SearchIndexClient(args.endpoint, cred)
    search_client = SearchClient(args.endpoint, args.index_name, cred)
    checks: dict[str, Any] = {}
    try:
        index = index_client.get_index(args.index_name)
        fields = {field.name: field for field in index.fields}
        vector = fields.get(args.vector_field)
        semantic_names = {
            config.name for config in (getattr(getattr(index, "semantic_search", None), "configurations", None) or [])
        }
        stats = index_client.get_index_statistics(args.index_name)
        checks["schema"] = {
            "ok": bool(vector)
            and getattr(vector, "vector_search_dimensions", None) == args.dimensions
            and args.semantic_configuration in semantic_names,
            "vector_field": args.vector_field,
            "dimensions": getattr(vector, "vector_search_dimensions", None),
            "semantic_configurations": sorted(semantic_names),
        }
        checks["coverage"] = {
            "ok": stats.document_count == expected_count,
            "expected_documents": expected_count,
            "indexed_documents": stats.document_count,
            "storage_bytes": stats.storage_size,
            "vector_index_bytes": stats.vector_index_size,
        }
        checks["keyword"] = one_search(search_client, args.query, "keyword", args.vector_field, None)
        checks["hybrid_semantic"] = one_search(
            search_client, args.query, "hybrid", args.vector_field, args.semantic_configuration
        )
    finally:
        search_client.close()
        index_client.close()
        close = getattr(cred, "close", None)
        if callable(close):
            close()
    ok = all(bool(check.get("ok")) for check in checks.values())
    return {
        "ok": ok,
        "endpoint": args.endpoint,
        "index_name": args.index_name,
        "checks": checks,
    }, ok


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Verify REGDOCS Azure AI Search hybrid deployment")
    p.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    p.add_argument("--api-key", default=os.getenv("AZURE_SEARCH_API_KEY") or os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    p.add_argument("--index-name", default=os.getenv("AZURE_SEARCH_HYBRID_INDEX", "regdocs-chunks-hybrid"))
    p.add_argument("--vector-field", default=os.getenv("AZURE_SEARCH_VECTOR_FIELD", "content_vector"))
    p.add_argument("--semantic-configuration", default=os.getenv("AZURE_SEARCH_SEMANTIC_CONFIGURATION", "regdocs-semantic"))
    p.add_argument("--dimensions", type=int, default=int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536")))
    p.add_argument("--normalized-dir", default=stored_path(NORMALIZE_DIR))
    p.add_argument("--expected-count", type=int)
    p.add_argument("--query", default="measures proposed to reduce effects on caribou habitat")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        args.normalized_dir = resolve_stored_path(args.normalized_dir)
        if not args.endpoint:
            raise ValueError("Set AZURE_SEARCH_ENDPOINT or pass --endpoint")
        payload, ok = verify(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if ok else 2
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
