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
from ..embeddings import EmbeddingCandidate, EmbeddingStore, content_sha256, embedding_plan, generate_embeddings
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


def map_document(
    chunk: dict[str, Any],
    prov: dict[str, Any],
    *,
    profile: str = "lexical",
    content_vector: list[float] | None = None,
) -> dict[str, Any]:
    chunk_id = str(chunk["id"])
    qualified, local = element_paths(prov.get("elements") or [])
    document = {
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
        "filing_number": chunk.get("filing_number"), "filing_id": chunk.get("filing_id"),
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
    if profile == "hybrid":
        atlas_base_url = os.getenv("ATLAS_PUBLIC_URL", "").strip().rstrip("/")
        document["url"] = (
            f"{atlas_base_url}/evidence/{chunk_id}" if atlas_base_url else chunk.get("source_url")
        )
        document["citation_title"] = chunk.get("heading") or chunk.get("title") or f"Document {chunk.get('document_id')}"
        document["content_vector"] = content_vector
    return document


def matches(chunk: dict[str, Any], document_ids: Optional[set[str]]) -> bool:
    return not document_ids or str(chunk.get("document_id") or "") in document_ids


def scan_inputs(
    normalized_dir: Path,
    document_ids: Optional[set[str]],
    limit: Optional[int],
    *,
    profile: str = "lexical",
    embedding_store: EmbeddingStore | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 0,
) -> dict[str, Any]:
    chunks_path, prov_path = input_paths(normalized_dir)
    selected = total = payload_bytes = 0
    docs: set[str] = set()
    types: Counter[str] = Counter()
    versions: set[str] = set()
    for chunk, prov in iter_pairs(normalized_dir):
        total += 1
        if not matches(chunk, document_ids) or (limit is not None and selected >= limit):
            continue
        if profile == "hybrid":
            assert embedding_store is not None
            candidate = EmbeddingCandidate(
                str(chunk["id"]),
                str(chunk.get("content") or ""),
                content_sha256(str(chunk.get("content") or "")),
            )
            if not embedding_store.has(candidate, embedding_model, embedding_dimensions):
                raise ValueError(f"{candidate.chunk_id}: matching embedding is missing from the cache")
        doc = map_document(chunk, prov, profile=profile)
        selected += 1
        docs.add(doc["document_id"])
        types[str(doc.get("chunk_type") or "unknown")] += 1
        if doc.get("normalizer_version"):
            versions.add(str(doc["normalizer_version"]))
        payload_bytes += len(stable_json(doc).encode())
    if not selected:
        raise ValueError("No normalized chunks matched the selection")
    return {
        "chunks_path": stored_path(chunks_path), "provenance_path": stored_path(prov_path),
        "chunks_sha256": sha256_file(chunks_path), "provenance_sha256": sha256_file(prov_path),
        "normalized_chunk_count": total, "search_document_count": selected,
        "source_document_count": len(docs), "chunk_types": dict(sorted(types.items())),
        "normalizer_versions": sorted(versions), "approx_search_document_bytes": payload_bytes,
        "search_profile": profile,
        "embedding_model": embedding_model if profile == "hybrid" else None,
        "embedding_dimensions": embedding_dimensions if profile == "hybrid" else None,
    }


def iter_documents(
    normalized_dir: Path,
    document_ids: Optional[set[str]],
    limit: Optional[int],
    *,
    profile: str = "lexical",
    embedding_store: EmbeddingStore | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 0,
) -> Iterator[dict[str, Any]]:
    selected = 0
    for chunk, prov in iter_pairs(normalized_dir):
        if not matches(chunk, document_ids):
            continue
        if limit is not None and selected >= limit:
            return
        selected += 1
        vector = None
        if profile == "hybrid":
            assert embedding_store is not None
            vector = embedding_store.vector(
                str(chunk["id"]),
                str(chunk.get("content") or ""),
                embedding_model,
                embedding_dimensions,
            )
            if vector is None:
                raise ValueError(f"{chunk['id']}: matching embedding is missing from the cache")
        yield map_document(chunk, prov, profile=profile, content_vector=vector)


def credential(api_key: Optional[str]) -> Any:
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError("Missing Stage 5 dependency. Run: python -m pip install -r pipeline/requirements.txt") from exc
    return AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()


def make_index(name: str, profile: str = "lexical", embedding_dimensions: int = 1536) -> Any:
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
    if profile == "lexical":
        return SearchIndex(name=name, fields=fields)

    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        VectorSearch,
        VectorSearchProfile,
    )

    fields.extend(
        [
            SimpleField(name="url", type=S),
            SearchableField(name="citation_title", type=S),
            SearchField(
                name="content_vector",
                type="Collection(Edm.Single)",
                searchable=True,
                retrievable=False,
                stored=False,
                vector_search_dimensions=embedding_dimensions,
                vector_search_profile_name="regdocs-vector-profile",
            ),
        ]
    )
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="regdocs-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="regdocs-vector-profile",
                algorithm_configuration_name="regdocs-hnsw",
            )
        ],
    )
    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="regdocs-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="citation_title"),
                    content_fields=[
                        SemanticField(field_name="content"),
                        SemanticField(field_name="heading"),
                    ],
                    keywords_fields=[SemanticField(field_name="section_path")],
                ),
            )
        ]
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search, semantic_search=semantic_search)


def ensure_index(client: Any, name: str, recreate: bool, profile: str = "lexical", embedding_dimensions: int = 1536) -> str:
    if recreate:
        try:
            client.delete_index(name)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
        created = client.create_index(make_index(name, profile, embedding_dimensions))
        return f"created {created.name}"
    try:
        current = client.get_index(name)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        created = client.create_index(make_index(name, profile, embedding_dimensions))
        return f"created {created.name}"
    expected = {f.name for f in make_index(name, profile, embedding_dimensions).fields}
    missing = sorted(expected - {f.name for f in current.fields})
    if missing:
        raise RuntimeError(f"Existing index is missing fields: {', '.join(missing)}. Use --recreate-index.")
    if profile == "hybrid":
        vector_field = next((field for field in current.fields if field.name == "content_vector"), None)
        vector_profiles = {
            item.name for item in (getattr(getattr(current, "vector_search", None), "profiles", None) or [])
        }
        semantic_configs = {
            item.name for item in (getattr(getattr(current, "semantic_search", None), "configurations", None) or [])
        }
        if (
            vector_field is None
            or getattr(vector_field, "vector_search_dimensions", None) != embedding_dimensions
            or getattr(vector_field, "vector_search_profile_name", None) != "regdocs-vector-profile"
            or "regdocs-vector-profile" not in vector_profiles
            or "regdocs-semantic" not in semantic_configs
        ):
            raise RuntimeError(
                "Existing hybrid index has incompatible vector or semantic configuration. "
                "Publish to a new physical index name or use --recreate-index."
            )
    return f"using existing {name}"


def promote_alias(client: Any, alias_name: str, index_name: str) -> str:
    """Atomically point a Search alias at a fully uploaded physical index."""
    from azure.search.documents.indexes.models import SearchAlias

    alias = client.create_or_update_alias(SearchAlias(name=alias_name, indexes=[index_name]))
    return f"promoted alias {alias.name} -> {index_name}"


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
        "alias": {
            "name": args.alias_name,
            "promotion_requested": bool(args.promote_alias),
            "promoted": bool(args.promote_alias and status == "SUCCEEDED"),
        },
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
    p.add_argument("--profile", choices=("lexical", "hybrid"), default="lexical")
    p.add_argument("--document-id", action="append"); p.add_argument("--limit", type=int)
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE); p.add_argument("--max-batch-bytes", type=int, default=DEFAULT_BATCH_BYTES)
    p.add_argument("--recreate-index", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--alias-name", default=os.getenv("AZURE_SEARCH_ALIAS_NAME"))
    p.add_argument("--promote-alias", action="store_true")
    p.add_argument("--embed", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--embedding-cache", default=os.getenv("REGDOCS_EMBEDDING_CACHE", stored_path(INDEX_DIR / "embeddings.sqlite")))
    p.add_argument("--foundry-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    p.add_argument("--foundry-api-key", default=os.getenv("FOUNDRY_API_KEY"))
    p.add_argument("--embedding-model", default=os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"))
    p.add_argument("--embedding-dimensions", type=int, default=int(os.getenv("FOUNDRY_EMBEDDING_DIMENSIONS", "1536")))
    p.add_argument("--embedding-batch-size", type=int, default=16)
    p.add_argument("--max-embedding-input-characters", type=int, default=24000)
    p.add_argument("--query"); p.add_argument("--filter"); p.add_argument("--top", type=int, default=5); p.add_argument("--version", action="store_true")
    return p


def validate(args: argparse.Namespace) -> None:
    args.normalized_dir = resolve_stored_path(args.normalized_dir); args.output_dir = resolve_stored_path(args.output_dir)
    args.embedding_cache = resolve_stored_path(args.embedding_cache)
    if args.profile == "hybrid" and args.index_name == DEFAULT_INDEX_NAME and not os.getenv("AZURE_SEARCH_INDEX_NAME"):
        args.index_name = "regdocs-chunks-v2"
    if args.limit is not None and args.limit < 1: raise ValueError("--limit must be >= 1")
    if not 1 <= args.batch_size <= MAX_BATCH_DOCUMENTS: raise ValueError(f"--batch-size must be 1..{MAX_BATCH_DOCUMENTS}")
    if args.max_batch_bytes < 1024: raise ValueError("--max-batch-bytes must be >= 1024")
    if args.top < 1: raise ValueError("--top must be >= 1")
    if args.embedding_dimensions < 1: raise ValueError("--embedding-dimensions must be >= 1")
    if not 1 <= args.embedding_batch_size <= 2048: raise ValueError("--embedding-batch-size must be 1..2048")
    if args.max_embedding_input_characters < 1000: raise ValueError("--max-embedding-input-characters must be >= 1000")
    if not args.query and not args.normalized_dir.is_dir(): raise FileNotFoundError(f"Normalized directory not found: {args.normalized_dir}")
    if args.recreate_index and (args.document_id or args.limit is not None):
        raise ValueError("--recreate-index cannot be combined with --document-id or --limit; use a separate --index-name for a pilot")
    if not args.dry_run and not args.embed and not args.endpoint: raise ValueError("Pass --endpoint or set AZURE_SEARCH_ENDPOINT")
    if args.embed and not args.dry_run and not args.foundry_endpoint:
        raise ValueError("Pass --foundry-endpoint or set FOUNDRY_PROJECT_ENDPOINT")
    if args.query and args.recreate_index: raise ValueError("--query cannot be combined with --recreate-index")
    if args.promote_alias and not args.alias_name:
        raise ValueError("--promote-alias requires --alias-name or AZURE_SEARCH_ALIAS_NAME")
    if args.alias_name and not args.promote_alias:
        raise ValueError("--alias-name is only used with the explicit --promote-alias action")
    if args.promote_alias and (args.document_id or args.limit is not None):
        raise ValueError("Alias promotion requires a complete publish; remove --document-id and --limit")
    if args.promote_alias and args.alias_name == args.index_name:
        raise ValueError("The alias name must differ from the physical index name")
    if args.query and args.promote_alias:
        raise ValueError("--query cannot be combined with --promote-alias")
    if args.dry_run and args.promote_alias:
        raise ValueError("--promote-alias cannot be combined with --dry-run")
    if args.all and (args.document_id or args.limit is not None):
        raise ValueError("--all cannot be combined with --document-id or --limit")
    if args.all and not args.embed:
        raise ValueError("--all is only used by the explicit index embed action")


def run(args: argparse.Namespace) -> int:
    validate(args)
    if args.embed:
        chunks_path = args.normalized_dir / "chunks.jsonl"
        if not chunks_path.is_file():
            raise FileNotFoundError(f"Expected {chunks_path}")
        document_ids = set(map(str, args.document_id)) if args.document_id else None
        plan = embedding_plan(
            chunks_path,
            args.embedding_cache,
            document_ids,
            args.limit,
            args.embedding_model,
            args.embedding_dimensions,
            args.max_embedding_input_characters,
        )
        report = {
            "stage": "index_embeddings",
            "model": args.embedding_model,
            "dimensions": args.embedding_dimensions,
            "cache": stored_path(args.embedding_cache),
            "selection": {"all": args.all, "document_id": args.document_id, "limit": args.limit},
            **plan,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if args.dry_run:
            print("DRY RUN: Foundry was not contacted and the embedding cache was not changed.")
            return 0
        generated = generate_embeddings(
            chunks_path,
            args.embedding_cache,
            document_ids,
            args.limit,
            args.foundry_endpoint,
            args.embedding_model,
            args.embedding_dimensions,
            args.foundry_api_key,
            args.embedding_batch_size,
            args.max_embedding_input_characters,
        )
        print(f"Generated {generated} embedding(s); cache={stored_path(args.embedding_cache)}")
        return 0
    if args.query:
        shown = query(args.endpoint, args.index_name, args.api_key, args.query, args.top, args.filter)
        print(f"{shown} result(s) shown.")
        return 0
    document_ids = set(map(str, args.document_id)) if args.document_id else None
    embedding_store = None
    if args.profile == "hybrid":
        if not args.embedding_cache.is_file():
            raise FileNotFoundError(
                f"Embedding cache not found: {args.embedding_cache}. Run: pipeline.py index embed plan"
            )
        embedding_store = EmbeddingStore(args.embedding_cache, readonly=args.dry_run)
    try:
        meta = scan_inputs(
            args.normalized_dir,
            document_ids,
            args.limit,
            profile=args.profile,
            embedding_store=embedding_store,
            embedding_model=args.embedding_model,
            embedding_dimensions=args.embedding_dimensions,
        )
    except Exception:
        if embedding_store is not None:
            embedding_store.close()
        raise
    print(f"Validated {meta['search_document_count']} selected chunk(s) from {meta['source_document_count']} REGDOCS document(s); "
          f"{meta['normalized_chunk_count']} total normalized chunk/provenance pairs checked.")
    print(f"Chunk types: {meta['chunk_types']}")
    print(f"Mapped payload: {meta['approx_search_document_bytes'] / 1048576:.2f} MiB")
    print(f"chunks.jsonl sha256={meta['chunks_sha256']}"); print(f"provenance.jsonl sha256={meta['provenance_sha256']}")
    if args.dry_run:
        print("DRY RUN: Azure was not contacted.")
        if embedding_store is not None:
            embedding_store.close()
        return 0

    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    started_at, started = utcnow(), time.monotonic()
    cred = credential(args.api_key)
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=cred)
    search_client = SearchClient(endpoint=args.endpoint, index_name=args.index_name, credential=cred)
    status, uploaded, batch_count = "FAILED", 0, 0
    try:
        print(ensure_index(index_client, args.index_name, args.recreate_index, args.profile, args.embedding_dimensions))
        uploaded, batch_count = upload(search_client, iter_documents(
                                       args.normalized_dir, document_ids, args.limit,
                                       profile=args.profile,
                                       embedding_store=embedding_store,
                                       embedding_model=args.embedding_model,
                                       embedding_dimensions=args.embedding_dimensions),
                                       int(meta["search_document_count"]), args.batch_size, args.max_batch_bytes)
        if args.promote_alias:
            remote_count = search_client.get_document_count()
            if remote_count != uploaded:
                raise RuntimeError(
                    f"Alias promotion refused: physical index contains {remote_count} documents "
                    f"after uploading the expected {uploaded}. Recreate or use a fresh physical index."
                )
            print(f"Verified physical index document count: {remote_count}")
            print(promote_alias(index_client, args.alias_name, args.index_name))
        status = "SUCCEEDED"
        print(f"Indexed {uploaded} chunk(s) into {args.index_name!r} in {batch_count} batch(es).")
        print(f"Run metadata: {write_manifest(args.output_dir, args, meta, status, started_at, time.monotonic()-started, uploaded, batch_count)}")
        return 0
    finally:
        if status != "SUCCEEDED":
            try: write_manifest(args.output_dir, args, meta, status, started_at, time.monotonic()-started, uploaded, batch_count)
            except Exception: pass
        search_client.close(); index_client.close()
        if embedding_store is not None: embedding_store.close()
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
