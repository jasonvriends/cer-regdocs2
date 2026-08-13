#!/usr/bin/env python3
"""Compare lexical and hybrid Azure AI Search retrieval on a CER query set.

The tool never calls an LLM. It records latency, top results, overlap, and
known-document metrics, and creates a review CSV for human relevance judgments.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from regdocs_atlas.paths import INDEX_DIR, resolve_stored_path, stored_path
from regdocs_atlas.stages.index import credential

DEFAULT_QUERIES = REPOSITORY_ROOT / "evaluation" / "search-queries.jsonl"
DEFAULT_OUTPUT = INDEX_DIR / "search-evaluation"
SELECT = ["chunk_id", "document_id", "title", "heading", "page_start", "page_end", "content", "source_url"]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[position]


def load_queries(path: Path) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not str(value.get("id") or "").strip() or not str(value.get("query") or "").strip():
                raise ValueError(f"{path}:{line_number}: each row needs non-empty id and query")
            value["expected_document_ids"] = [str(item) for item in value.get("expected_document_ids", [])]
            queries.append(value)
    if not queries:
        raise ValueError(f"No evaluation queries found in {path}")
    return queries


def search(client: Any, query: str, mode: str, top: int, vector_field: str, semantic: Optional[str]) -> dict[str, Any]:
    from azure.search.documents.models import VectorizableTextQuery

    options: dict[str, Any] = {
        "top": top,
        "select": SELECT,
        "include_total_count": True,
        "query_type": "simple",
    }
    if mode == "hybrid":
        options["vector_queries"] = [
            VectorizableTextQuery(text=query, fields=vector_field, k_nearest_neighbors=50)
        ]
        options["vector_filter_mode"] = "preFilter"
        if semantic:
            options.update(
                query_type="semantic",
                semantic_configuration_name=semantic,
                query_caption="extractive",
                semantic_error_mode="partial",
            )
    started = time.perf_counter()
    response = client.search(query, **options)
    results = []
    for rank, row in enumerate(response, 1):
        content = str(row.get("content") or "").replace("\n", " ").strip()
        results.append(
            {
                "rank": rank,
                "score": row.get("@search.score"),
                "reranker_score": row.get("@search.reranker_score"),
                "chunk_id": str(row.get("chunk_id") or ""),
                "document_id": str(row.get("document_id") or ""),
                "title": row.get("heading") or row.get("title"),
                "page_start": row.get("page_start"),
                "page_end": row.get("page_end"),
                "excerpt": content[:500],
                "source_url": row.get("source_url"),
            }
        )
    return {
        "mode": mode,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "total_count": response.get_count(),
        "results": results,
    }


def reciprocal_rank(results: list[dict[str, Any]], expected: list[str]) -> Optional[float]:
    if not expected:
        return None
    expected_set = set(expected)
    for result in results:
        if result["document_id"] in expected_set:
            return 1.0 / int(result["rank"])
    return 0.0


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    from azure.search.documents import SearchClient

    queries = load_queries(args.queries)
    cred = credential(args.api_key)
    client = SearchClient(args.endpoint, args.index_name, cred)
    rows = []
    try:
        for position, item in enumerate(queries, 1):
            print(f"[{position}/{len(queries)}] {item['id']}: {item['query']}")
            keyword = search(client, item["query"], "keyword", args.top, args.vector_field, None)
            hybrid = search(client, item["query"], "hybrid", args.top, args.vector_field, args.semantic_configuration)
            keyword_ids = {result["chunk_id"] for result in keyword["results"]}
            hybrid_ids = {result["chunk_id"] for result in hybrid["results"]}
            union = keyword_ids | hybrid_ids
            expected = item["expected_document_ids"]
            rows.append(
                {
                    **item,
                    "keyword": keyword,
                    "hybrid": hybrid,
                    "top_overlap": round(len(keyword_ids & hybrid_ids) / len(union), 4) if union else 1.0,
                    "keyword_reciprocal_rank": reciprocal_rank(keyword["results"], expected),
                    "hybrid_reciprocal_rank": reciprocal_rank(hybrid["results"], expected),
                }
            )
    finally:
        client.close()
        close = getattr(cred, "close", None)
        if callable(close):
            close()

    labeled = [row for row in rows if row["expected_document_ids"]]
    keyword_latencies = [row["keyword"]["latency_ms"] for row in rows]
    hybrid_latencies = [row["hybrid"]["latency_ms"] for row in rows]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": args.endpoint,
        "index_name": args.index_name,
        "query_count": len(rows),
        "labeled_query_count": len(labeled),
        "top": args.top,
        "summary": {
            "keyword_median_ms": round(statistics.median(keyword_latencies), 2),
            "keyword_p95_ms": round(percentile(keyword_latencies, 0.95), 2),
            "hybrid_median_ms": round(statistics.median(hybrid_latencies), 2),
            "hybrid_p95_ms": round(percentile(hybrid_latencies, 0.95), 2),
            "mean_top_overlap": round(statistics.mean(row["top_overlap"] for row in rows), 4),
            "keyword_mrr": round(statistics.mean(row["keyword_reciprocal_rank"] for row in labeled), 4) if labeled else None,
            "hybrid_mrr": round(statistics.mean(row["hybrid_reciprocal_rank"] for row in labeled), 4) if labeled else None,
        },
        "queries": rows,
    }


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "review.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = [
            "query_id", "category", "query", "mode", "rank", "relevance_0_to_3", "document_id", "chunk_id",
            "page_start", "title", "excerpt", "source_url",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for query in payload["queries"]:
            for mode in ("keyword", "hybrid"):
                for result in query[mode]["results"]:
                    writer.writerow(
                        {
                            "query_id": query["id"], "category": query.get("category"), "query": query["query"],
                            "mode": mode, "rank": result["rank"], "relevance_0_to_3": "",
                            "document_id": result["document_id"], "chunk_id": result["chunk_id"],
                            "page_start": result["page_start"], "title": result["title"], "excerpt": result["excerpt"],
                            "source_url": result["source_url"],
                        }
                    )
    summary = payload["summary"]
    report = [
        "# Azure AI Search retrieval evaluation", "", f"Index: `{payload['index_name']}`  ",
        f"Generated: {payload['generated_at']}  ", f"Queries: {payload['query_count']}  ", "",
        "| Metric | Keyword | Hybrid |", "|---|---:|---:|",
        f"| Median latency | {summary['keyword_median_ms']} ms | {summary['hybrid_median_ms']} ms |",
        f"| P95 latency | {summary['keyword_p95_ms']} ms | {summary['hybrid_p95_ms']} ms |",
        f"| MRR on labeled queries | {summary['keyword_mrr']} | {summary['hybrid_mrr']} |", "",
        f"Mean top-result overlap: {summary['mean_top_overlap']}", "",
        "Score every row in `review.csv` from 0 (irrelevant) to 3 (fully relevant) before choosing the default mode.", "",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare keyword and hybrid CER retrieval")
    p.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    p.add_argument("--api-key", default=os.getenv("AZURE_SEARCH_API_KEY") or os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    p.add_argument("--index-name", default=os.getenv("AZURE_SEARCH_HYBRID_INDEX", "regdocs-chunks-hybrid"))
    p.add_argument("--vector-field", default=os.getenv("AZURE_SEARCH_VECTOR_FIELD", "content_vector"))
    p.add_argument("--semantic-configuration", default=os.getenv("AZURE_SEARCH_SEMANTIC_CONFIGURATION", "regdocs-semantic"))
    p.add_argument("--queries", default=stored_path(DEFAULT_QUERIES))
    p.add_argument("--output-dir", default=stored_path(DEFAULT_OUTPUT))
    p.add_argument("--top", type=int, default=10)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        args.queries = resolve_stored_path(args.queries)
        args.output_dir = resolve_stored_path(args.output_dir)
        if not args.endpoint:
            raise ValueError("Set AZURE_SEARCH_ENDPOINT or pass --endpoint")
        if not 1 <= args.top <= 50:
            raise ValueError("--top must be 1..50")
        payload = evaluate(args)
        write_outputs(payload, args.output_dir)
        print(json.dumps(payload["summary"], indent=2))
        print(f"Review package: {stored_path(args.output_dir)}")
        return 0
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
