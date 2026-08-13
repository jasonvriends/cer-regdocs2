#!/usr/bin/env python3
"""Stage 5: publish normalized REGDOCS chunks to Azure AI Search.

Canonical implementation now lives in ``regdocs_atlas.stages``. The historical
``pipeline/regdocs_5_index_core.py`` implementation has been retired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from ..paths import INDEX_DIR, NORMALIZE_DIR, resolve_stored_path, stored_path
from ..version import release_version

COMPONENT_VERSION = "5.0.1"
DEFAULT_INDEX_NAME = "regdocs-chunks"
DEFAULT_BATCH_SIZE = 500
DEFAULT_BATCH_BYTES = 12 * 1024 * 1024
MAX_BATCH_DOCUMENTS = 1000


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            yield line_no, value


def input_paths(normalized_dir: Path) -> tuple[Path, Path]:
    chunks = normalized_dir / "chunks.jsonl"
    provenance = normalized_dir / "provenance.jsonl"
    if not chunks.is_file() or not provenance.is_file():
        raise FileNotFoundError(f"Expected chunks.jsonl and provenance.jsonl under {normalized_dir}")
    return chunks, provenance


def iter_pairs(normalized_dir: Path) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    chunks_path, prov_path = input_paths(normalized_dir)
    sentinel = object()
    for pair_no, (chunk_item, prov_item) in enumerate(
        zip_longest(iter_jsonl(chunks_path), iter_jsonl(prov_path), fillvalue=sentinel), 1
    ):
        if chunk_item is sentinel or prov_item is sentinel:
            raise ValueError(f"chunks.jsonl and provenance.jsonl differ in record count near pair {pair_no}")
        chunk_line, chunk = chunk_item
        prov_line, prov = prov_item
        chunk_id = str(chunk.get("id") or "")
        where = f"chunks:{chunk_line}/provenance:{prov_line}"
        if not chunk_id:
            raise ValueError(f"{where}: chunk missing id")
        if prov.get("chunk_id") != chunk_id:
            raise ValueError(f"{where}: {chunk_id}: provenance chunk_id mismatch")
        if str(prov.get("document_id") or "") != str(chunk.get("document_id") or ""):
            raise ValueError(f"{where}: {chunk_id}: provenance document_id mismatch")
        if prov.get("content_index") != chunk.get("content_index"):
            raise ValueError(f"{where}: {chunk_id}: provenance content_index mismatch")
        yield chunk, prov


def string_list(value: Any) -> list[str]:
    return [str(x) for x in value if x not in (None, "")] if isinstance(value, list) else []


def optional_string(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def element_paths(elements: Sequence[Any]) -> tuple[list[str], list[str]]:
    qualified: list[str] = []
    local: list[str] = []

    def walk(items: Sequence[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("element"), str):
                qualified.append(item["element"])
            if isinstance(item.get("local_element"), str):
                local.append(item["local_element"])
            if isinstance(item.get("child_elements"), list):
                walk(item["child_elements"])

    walk(elements)
    return list(dict.fromkeys(qualified)), list(dict.fromkeys(local))


def map_document(chunk: dict[str, Any], prov: dict[str, Any]) -> dict[str, Any]:
    chunk_id = str(chunk["id"])
    qualified, local = element_paths(prov.get("elements") or [])
    return {
        "key": hashlib.sha256(chunk_id.encode()).hexdigest(),
        "chunk_id": chunk_id,
        "document_id": str(chunk.get("document_id") or ""),
        "chunk_index": chunk.get("chunk_index"), "chunk_type": chunk.get("chunk_type"),
        "title": chunk.get("title"), "content": chunk.get("content"), "heading": chunk.get("heading"),
        "section_path": string_list(chunk.get("section_path")),
        "page_start": chunk.get("page_start"), "page_end": chunk.get("page_end"),
        "content_index": chunk.get("content_index"), "word_count": chunk.get("word_count"),
        "filing_date": chunk.get("filing_date"), "submitter": chunk.get("submitter"),
        "company": chunk.get("company"), "project": chunk.get("project"),
        "filing_number": chunk.get("filing_number"), "filing_id": optional_string(chunk.get("filing_id")),
        "application_types": string_list(chunk.get("application_types")),
        "commodities": string_list(chunk.get("commodities")),
        "document_types": string_list(chunk.get("document_types")),
        "file_types": string_list(chunk.get("file_types")), "roles": string_list(chunk.get("roles")),
        "source_url": chunk.get("source_url"), "resolved_url": chunk.get("resolved_url"),
        "file_path": chunk.get("file_path"), "file_sha256": chunk.get("file_sha256"),
        "analyzer_id": chunk.get("analyzer_id"), "api_version": chunk.get("api_version"),
        "normalizer_version": chunk.get("normalizer_version"),
        "table_id": chunk.get("table_id"), "table_part": chunk.get("table_part"),
        "table_row_start": chunk.get("table_row_start"), "table_row_end": chunk.get("table_row_end"),
        "figure_id": chunk.get("figure_id"), "azure_figure_id": chunk.get("azure_figure_id"),
        "element_paths": qualified, "local_element_paths": local,
    }


def matches(chunk: dict[str, Any], document_ids: Optional[set[str]]) -> bool:
    return not document_ids or str(chunk.get("document_id") or "") in document_ids


def scan_inputs(
    normalized_dir: Path,
    document_ids: Optional[set[str]],
    limit: Optional[int],
    batch_size: int,
    max_batch_bytes: int,
) -> dict[str, Any]:
    chunks_path, prov_path = input_paths(normalized_dir)
    selected = total = payload_bytes = 0
    expected_batches = batch_documents = batch_bytes = 0
    docs: set[str] = set()
    types: Counter[str] = Counter()
    versions: set[str] = set()
    for chunk, prov in iter_pairs(normalized_dir):
        total += 1
        if not matches(chunk, document_ids) or (limit is not None and selected >= limit):
            continue
        doc = map_document(chunk, prov)
        doc_bytes = len(stable_json(doc).encode())
        batched_bytes = doc_bytes + 64
        if batched_bytes > max_batch_bytes:
            raise ValueError(f"{doc.get('chunk_id')}: mapped document exceeds --max-batch-bytes")
        if batch_documents and (
            batch_documents >= batch_size or batch_bytes + batched_bytes > max_batch_bytes
        ):
            expected_batches += 1
            batch_documents = batch_bytes = 0
        batch_documents += 1
        batch_bytes += batched_bytes
        selected += 1
        docs.add(doc["document_id"])
        types[str(doc.get("chunk_type") or "unknown")] += 1
        if doc.get("normalizer_version"):
            versions.add(str(doc["normalizer_version"]))
        payload_bytes += doc_bytes
    if not selected:
        raise ValueError("No normalized chunks matched the selection")
    if batch_documents:
        expected_batches += 1
    return {
        "chunks_path": stored_path(chunks_path), "provenance_path": stored_path(prov_path),
        "chunks_sha256": sha256_file(chunks_path), "provenance_sha256": sha256_file(prov_path),
        "normalized_chunk_count": total, "search_document_count": selected,
        "source_document_count": len(docs), "chunk_types": dict(sorted(types.items())),
        "normalizer_versions": sorted(versions), "approx_search_document_bytes": payload_bytes,
        "expected_batch_count": expected_batches,
    }


def iter_documents(normalized_dir: Path, document_ids: Optional[set[str]], limit: Optional[int]) -> Iterator[dict[str, Any]]:
    selected = 0
    for chunk, prov in iter_pairs(normalized_dir):
        if not matches(chunk, document_ids):
            continue
        if limit is not None and selected >= limit:
            return
        selected += 1
        yield map_document(chunk, prov)


def credential(api_key: Optional[str]) -> Any:
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError(
            "Missing Stage 5 dependency. Run: python -m pip install -r regdocs_atlas/requirements.txt"
        ) from exc
    return AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()


def make_index(name: str) -> Any:
    from azure.search.documents.indexes.models import SearchField, SearchFieldDataType, SearchIndex, SearchableField, SimpleField
    S, I, C = SearchFieldDataType.STRING, SearchFieldDataType.INT32, "Collection(Edm.String)"
    fields: list[Any] = [
        SimpleField(name="key", type=S, key=True),
        SimpleField(name="chunk_id", type=S, filterable=True),
        SimpleField(name="document_id", type=S, filterable=True, facetable=True),
        SimpleField(name="chunk_index", type=I, filterable=True, sortable=True),
        SimpleField(name="chunk_type", type=S, filterable=True, facetable=True),
        SearchableField(name="title", type=S), SearchableField(name="content", type=S),
        SearchableField(name="heading", type=S),
        SearchField(name="section_path", type=C, searchable=True, retrievable=True),
        SimpleField(name="page_start", type=I, filterable=True, sortable=True),
        SimpleField(name="page_end", type=I, filterable=True, sortable=True),
        SimpleField(name="content_index", type=I, filterable=True),
        SimpleField(name="word_count", type=I, filterable=True, sortable=True),
        SimpleField(name="filing_date", type=S, filterable=True, sortable=True),
        SearchableField(name="submitter", type=S, filterable=True, facetable=True),
        SearchableField(name="company", type=S, filterable=True, facetable=True),
        SearchableField(name="project", type=S, filterable=True, facetable=True),
        SimpleField(name="filing_number", type=S, filterable=True), SimpleField(name="filing_id", type=S, filterable=True),
    ]
    for field_name in ("application_types", "commodities", "document_types", "file_types", "roles"):
        fields.append(SearchField(name=field_name, type=C, searchable=True, filterable=True, facetable=True, retrievable=True))
    fields.extend([
        SimpleField(name="source_url", type=S), SimpleField(name="resolved_url", type=S),
        SimpleField(name="file_path", type=S), SimpleField(name="file_sha256", type=S, filterable=True),
        SimpleField(name="analyzer_id", type=S, filterable=True), SimpleField(name="api_version", type=S, filterable=True),
        SimpleField(name="normalizer_version", type=S, filterable=True),
        SimpleField(name="table_id", type=S, filterable=True), SimpleField(name="table_part", type=I),
        SimpleField(name="table_row_start", type=I), SimpleField(name="table_row_end", type=I),
        SimpleField(name="figure_id", type=S, filterable=True), SimpleField(name="azure_figure_id", type=S),
        SearchField(name="element_paths", type=C, retrievable=True), SearchField(name="local_element_paths", type=C, retrievable=True),
    ])
    return SearchIndex(name=name, fields=fields)


def ensure_index(client: Any, name: str, recreate: bool) -> str:
    if recreate:
        try:
            client.delete_index(name)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
        created = client.create_index(make_index(name))
        return f"created {created.name}"
    try:
        current = client.get_index(name)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        created = client.create_index(make_index(name))
        return f"created {created.name}"
    expected = {f.name for f in make_index(name).fields}
    missing = sorted(expected - {f.name for f in current.fields})
    if missing:
        raise RuntimeError(f"Existing index is missing fields: {', '.join(missing)}. Use --recreate-index.")
    return f"using existing {name}"


def batches(documents: Iterable[dict[str, Any]], max_docs: int, max_bytes: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    size = 0
    for doc in documents:
        doc_bytes = len(stable_json(doc).encode()) + 64
        if doc_bytes > max_bytes:
            raise ValueError(f"{doc.get('chunk_id')}: mapped document exceeds --max-batch-bytes")
        if batch and (len(batch) >= max_docs or size + doc_bytes > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(doc)
        size += doc_bytes
    if batch:
        yield batch


def upload(client: Any, documents: Iterable[dict[str, Any]], total: int, batch_size: int, max_bytes: int) -> tuple[int, int]:
    uploaded = batch_count = 0
    for batch_count, batch in enumerate(batches(documents, batch_size, max_bytes), 1):
        results = client.merge_or_upload_documents(documents=batch)
        failed = [r for r in results if not bool(getattr(r, "succeeded", False))]
        if failed:
            detail = "; ".join(f"{getattr(r, 'key', '?')}: {getattr(r, 'error_message', '')}" for r in failed[:10])
            raise RuntimeError(f"Azure rejected {len(failed)} document(s) in batch {batch_count}: {detail}")
        uploaded += len(results)
        print(f"Uploaded batch {batch_count}: {len(results)} chunks ({uploaded}/{total})")
    return uploaded, batch_count


def write_manifest(output_dir: Path, args: argparse.Namespace, meta: dict[str, Any], status: str, started: str,
                   elapsed: float, uploaded: int, batch_count: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "last_run.json"
    payload = {
        "stage": "index", "component_version": COMPONENT_VERSION, "status": status,
        "started_at": started, "finished_at": utcnow(), "elapsed_seconds": round(elapsed, 3),
        "endpoint": args.endpoint, "index_name": args.index_name,
        "authentication": "api_key" if args.api_key else "default_azure_credential",
        "selection": {"document_id": args.document_id, "limit": args.limit}, "input": meta,
        "uploaded_documents": uploaded, "batch_count": batch_count,
    }
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def query(endpoint: str, index_name: str, api_key: Optional[str], text: str, top: int, filter_text: Optional[str]) -> int:
    from azure.search.documents import SearchClient
    cred = credential(api_key)
    client = SearchClient(endpoint=endpoint, index_name=index_name, credential=cred)
    try:
        results = client.search(search_text=text, top=top, filter=filter_text,
            select=["chunk_id", "document_id", "title", "heading", "page_start", "page_end", "content", "source_url"])
        count = 0
        for count, result in enumerate(results, 1):
            preview = str(result.get("content") or "").replace("\n", " ")
            preview = preview if len(preview) <= 300 else preview[:297] + "..."
            print(f"[{count}] {result.get('chunk_id')} doc={result.get('document_id')} pages={result.get('page_start')}-{result.get('page_end')}")
            print(f"    {result.get('title') or ''}")
            if result.get("heading"):
                print(f"    heading: {result.get('heading')}")
            print(f"    {preview}")
        return count
    finally:
        client.close()
        close = getattr(cred, "close", None)
        if callable(close):
            close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="REGDOCS Stage 5: publish normalized chunks to Azure AI Search",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--normalized-dir", default=stored_path(NORMALIZE_DIR)); p.add_argument("--output-dir", default=stored_path(INDEX_DIR))
    p.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT")); p.add_argument("--api-key", default=os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    p.add_argument("--index-name", default=os.getenv("AZURE_SEARCH_INDEX_NAME", DEFAULT_INDEX_NAME))
    p.add_argument("--document-id", action="append"); p.add_argument("--limit", type=int)
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE); p.add_argument("--max-batch-bytes", type=int, default=DEFAULT_BATCH_BYTES)
    p.add_argument("--recreate-index", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--query"); p.add_argument("--filter"); p.add_argument("--top", type=int, default=5); p.add_argument("--version", action="store_true")
    return p


def validate(args: argparse.Namespace) -> None:
    args.normalized_dir = resolve_stored_path(args.normalized_dir); args.output_dir = resolve_stored_path(args.output_dir)
    if args.limit is not None and args.limit < 1: raise ValueError("--limit must be >= 1")
    if not 1 <= args.batch_size <= MAX_BATCH_DOCUMENTS: raise ValueError(f"--batch-size must be 1..{MAX_BATCH_DOCUMENTS}")
    if args.max_batch_bytes < 1024: raise ValueError("--max-batch-bytes must be >= 1024")
    if args.top < 1: raise ValueError("--top must be >= 1")
    if not args.query and not args.normalized_dir.is_dir(): raise FileNotFoundError(f"Normalized directory not found: {args.normalized_dir}")
    if args.recreate_index and (args.document_id or args.limit is not None):
        raise ValueError("--recreate-index cannot be combined with --document-id or --limit; use a separate --index-name for a pilot")
    if not args.dry_run and not args.endpoint: raise ValueError("Pass --endpoint or set AZURE_SEARCH_ENDPOINT")
    if args.query and args.recreate_index: raise ValueError("--query cannot be combined with --recreate-index")


def run(args: argparse.Namespace) -> int:
    validate(args)
    if args.query:
        shown = query(args.endpoint, args.index_name, args.api_key, args.query, args.top, args.filter)
        print(f"{shown} result(s) shown.")
        return 0
    document_ids = set(map(str, args.document_id)) if args.document_id else None
    meta = scan_inputs(
        args.normalized_dir,
        document_ids,
        args.limit,
        args.batch_size,
        args.max_batch_bytes,
    )
    print(f"Validated {meta['search_document_count']} selected chunk(s) from {meta['source_document_count']} REGDOCS document(s); "
          f"{meta['normalized_chunk_count']} total normalized chunk/provenance pairs checked.")
    print(f"Chunk types: {meta['chunk_types']}")
    print(f"Mapped payload: {meta['approx_search_document_bytes'] / 1048576:.2f} MiB")
    print(
        f"Upload plan: {meta['expected_batch_count']} batch(es) at up to "
        f"{args.batch_size} chunks / {args.max_batch_bytes / 1048576:.2f} MiB."
    )
    print(f"chunks.jsonl sha256={meta['chunks_sha256']}"); print(f"provenance.jsonl sha256={meta['provenance_sha256']}")
    if args.dry_run:
        print("DRY RUN: Azure was not contacted.")
        return 0

    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    started_at, started = utcnow(), time.monotonic()
    cred = credential(args.api_key)
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=cred)
    search_client = SearchClient(endpoint=args.endpoint, index_name=args.index_name, credential=cred)
    status, uploaded, batch_count = "FAILED", 0, 0
    try:
        print(ensure_index(index_client, args.index_name, args.recreate_index))
        uploaded, batch_count = upload(search_client, iter_documents(args.normalized_dir, document_ids, args.limit),
                                       int(meta["search_document_count"]), args.batch_size, args.max_batch_bytes)
        status = "SUCCEEDED"
        print(f"Indexed {uploaded} chunk(s) into {args.index_name!r} in {batch_count} batch(es).")
        print(f"Run metadata: {write_manifest(args.output_dir, args, meta, status, started_at, time.monotonic()-started, uploaded, batch_count)}")
        return 0
    finally:
        if status != "SUCCEEDED":
            try: write_manifest(args.output_dir, args, meta, status, started_at, time.monotonic()-started, uploaded, batch_count)
            except Exception: pass
        search_client.close(); index_client.close()
        close = getattr(cred, "close", None)
        if callable(close): close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    if args.version:
        print(release_version()); return 0
    try: return run(args)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
