#!/usr/bin/env python3
"""Publish normalized REGDOCS chunks to a versioned hybrid Azure AI Search index.

Embeddings are generated in client-side batches so the published vector is
always produced by the same deployment configured for query vectorization.
A full publication can prune stale keys only after the new corpus uploads
successfully, avoiding an empty production index when embedding/upload fails.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from regdocs_atlas.paths import INDEX_DIR, NORMALIZE_DIR, resolve_stored_path, stored_path
from regdocs_atlas.stages.index import (
    DEFAULT_BATCH_BYTES,
    DEFAULT_INDEX_NAME,
    batches,
    credential,
    iter_documents,
    make_index,
    scan_inputs,
)

DEFAULT_HYBRID_INDEX = "regdocs-chunks-hybrid"
DEFAULT_VECTOR_FIELD = "content_vector"
DEFAULT_SEMANTIC_CONFIGURATION = "regdocs-semantic"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
DEFAULT_CACHE_DB = INDEX_DIR / "embedding-cache.sqlite"


def chunked(items: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    iterator = iter(items)
    while group := list(islice(iterator, size)):
        yield group


def embedding_text(document: dict[str, Any]) -> str:
    parts = [document.get("title"), document.get("heading"), document.get("content")]
    text = "\n\n".join(str(part).strip() for part in parts if part).strip()[:24_000]
    return text or f"Document {document.get('document_id')} passage {document.get('chunk_id')}"


class EmbeddingCache:
    """Durable local embedding cache keyed by exact model input and configuration."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                cache_key TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                resource TEXT NOT NULL,
                deployment TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector_blob BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    @staticmethod
    def key(text: str, resource: str, deployment: str, model: str, dimensions: int) -> str:
        identity = json.dumps(
            {"resource": resource, "deployment": deployment, "model": model, "dimensions": dimensions, "text": text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def get(self, cache_key: str, dimensions: int) -> Optional[list[float]]:
        row = self.connection.execute(
            "SELECT vector_blob FROM embeddings WHERE cache_key = ? AND dimensions = ?", (cache_key, dimensions)
        ).fetchone()
        if not row:
            return None
        packed = array("f")
        packed.frombytes(row[0])
        if sys.byteorder != "little":
            packed.byteswap()
        vector = packed.tolist()
        if len(vector) != dimensions:
            raise RuntimeError(f"Invalid cached vector for {cache_key}")
        return vector

    def put(
        self,
        cache_key: str,
        chunk_id: str,
        resource: str,
        deployment: str,
        model: str,
        dimensions: int,
        vector: list[float],
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO embeddings
                (cache_key, chunk_id, resource, deployment, model, dimensions, vector_blob, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                chunk_id,
                resource,
                deployment,
                model,
                dimensions,
                self._pack(vector),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

    @staticmethod
    def _pack(vector: list[float]) -> bytes:
        packed = array("f", vector)
        if sys.byteorder != "little":
            packed.byteswap()
        return packed.tobytes()

    def commit(self) -> None:
        self.connection.commit()

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


class EmbeddingClient:
    def __init__(self, endpoint: str, deployment: str, dimensions: int, api_key: Optional[str]) -> None:
        self.url = endpoint.rstrip("/") + "/openai/v1/embeddings"
        self.deployment = deployment
        self.dimensions = dimensions
        self.api_key = api_key
        self.identity = None if api_key else credential(None)
        self.client = httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"api-key": self.api_key, "Content-Type": "application/json"}
        token = self.identity.get_token("https://cognitiveservices.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.deployment,
            "input": texts,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        for attempt in range(6):
            response = self.client.post(self.url, headers=self._headers(), json=payload)
            if response.status_code < 400:
                body = response.json()
                rows = sorted(body.get("data", []), key=lambda row: int(row.get("index", 0)))
                vectors = [row.get("embedding") for row in rows]
                if len(vectors) != len(texts) or any(len(vector or []) != self.dimensions for vector in vectors):
                    raise RuntimeError("Embedding response count or dimensions did not match the request")
                return vectors
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == 5:
                detail = response.text[:1000]
                raise RuntimeError(f"Embedding request failed ({response.status_code}): {detail}")
            retry_after = response.headers.get("retry-after")
            delay = min(float(retry_after) if retry_after else 2**attempt, 30.0)
            print(f"Embedding request throttled; retrying in {delay:g}s", file=sys.stderr)
            time.sleep(delay)
        raise RuntimeError("Embedding request failed after retries")

    def close(self) -> None:
        self.client.close()
        close = getattr(self.identity, "close", None)
        if callable(close):
            close()


def hybrid_index(
    name: str,
    vector_field: str,
    dimensions: int,
    embedding_endpoint: str,
    embedding_deployment: str,
    embedding_model: str,
    vectorizer_api_key: Optional[str],
) -> Any:
    from azure.search.documents.indexes.models import (
        AzureOpenAIVectorizer,
        AzureOpenAIVectorizerParameters,
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        VectorSearch,
        VectorSearchProfile,
    )

    index = make_index(name)
    index.fields.append(
        SearchField(
            name=vector_field,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            stored=False,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="regdocs-vector-profile",
        )
    )
    index.vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="regdocs-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="regdocs-vector-profile",
                algorithm_configuration_name="regdocs-hnsw",
                vectorizer_name="regdocs-openai",
            )
        ],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="regdocs-openai",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=embedding_endpoint.rstrip("/"),
                    deployment_name=embedding_deployment,
                    model_name=embedding_model,
                    api_key=vectorizer_api_key,
                ),
            )
        ],
    )
    index.semantic_search = SemanticSearch(
        default_configuration_name=DEFAULT_SEMANTIC_CONFIGURATION,
        configurations=[
            SemanticConfiguration(
                name=DEFAULT_SEMANTIC_CONFIGURATION,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content"), SemanticField(field_name="heading")],
                ),
            )
        ],
    )
    return index


def ensure_index(client: Any, definition: Any, recreate: bool) -> str:
    try:
        current = client.get_index(definition.name)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        created = client.create_index(definition)
        return f"created {created.name}"
    if recreate:
        client.delete_index(definition.name)
        created = client.create_index(definition)
        return f"recreated {created.name}"
    expected = {field.name for field in definition.fields}
    actual = {field.name for field in current.fields}
    missing = sorted(expected - actual)
    if missing:
        raise RuntimeError(f"Existing index is missing fields: {', '.join(missing)}")
    current_vector = next((field for field in current.fields if field.name == definition.fields[-1].name), None)
    if getattr(current_vector, "vector_search_dimensions", None) != getattr(
        definition.fields[-1], "vector_search_dimensions", None
    ):
        raise RuntimeError("Existing vector field dimensions do not match --dimensions")
    return f"using existing {definition.name}"


def with_embeddings(
    documents: Iterable[dict[str, Any]],
    embedding_client: EmbeddingClient,
    cache: EmbeddingCache,
    vector_field: str,
    batch_size: int,
    resource: str,
    deployment: str,
    model: str,
    dimensions: int,
) -> Iterator[dict[str, Any]]:
    completed = generated = cache_hits = 0
    for group in chunked(documents, batch_size):
        texts = [embedding_text(document) for document in group]
        keys = [cache.key(text, resource, deployment, model, dimensions) for text in texts]
        vectors: list[Optional[list[float]]] = [cache.get(key, dimensions) for key in keys]
        missing_positions = [position for position, vector in enumerate(vectors) if vector is None]
        if missing_positions:
            fresh = embedding_client.embed([texts[position] for position in missing_positions])
            for position, vector in zip(missing_positions, fresh):
                vectors[position] = vector
                cache.put(
                    keys[position], str(group[position].get("chunk_id") or ""), resource, deployment, model, dimensions, vector
                )
            cache.commit()
            generated += len(missing_positions)
        cache_hits += len(group) - len(missing_positions)
        for document, vector in zip(group, vectors):
            if vector is None:
                raise RuntimeError(f"No vector generated for {document.get('chunk_id')}")
            document[vector_field] = vector
            yield document
        completed += len(group)
        print(f"Prepared {completed} chunk(s): generated={generated}, cache_hits={cache_hits}")


def upload(client: Any, documents: Iterable[dict[str, Any]], total: int, batch_size: int) -> tuple[int, int]:
    uploaded = batch_count = 0
    for batch_count, batch in enumerate(batches(documents, batch_size, DEFAULT_BATCH_BYTES), 1):
        results = client.merge_or_upload_documents(documents=batch)
        failed = [result for result in results if not bool(getattr(result, "succeeded", False))]
        if failed:
            detail = "; ".join(
                f"{getattr(result, 'key', '?')}: {getattr(result, 'error_message', '')}" for result in failed[:10]
            )
            raise RuntimeError(f"Azure rejected {len(failed)} document(s): {detail}")
        uploaded += len(results)
        print(f"Uploaded batch {batch_count}: {len(results)} chunks ({uploaded}/{total})")
    return uploaded, batch_count


def expected_keys(normalized_dir: Path) -> set[str]:
    return {str(document["key"]) for document in iter_documents(normalized_dir, None, None)}


def prune_stale(client: Any, wanted: set[str], batch_size: int = 1000) -> int:
    stale: list[str] = []
    results = client.search(search_text="*", select=["key"])
    for result in results:
        key = str(result.get("key") or "")
        if key and key not in wanted:
            stale.append(key)
    if not stale:
        print("Stale-key reconciliation: no obsolete Search chunks found.")
        return 0

    deleted = 0
    for start in range(0, len(stale), batch_size):
        batch = [{"key": key} for key in stale[start : start + batch_size]]
        responses = client.delete_documents(documents=batch)
        failed = [result for result in responses if not bool(getattr(result, "succeeded", False))]
        if failed:
            detail = "; ".join(
                f"{getattr(result, 'key', '?')}: {getattr(result, 'error_message', '')}" for result in failed[:10]
            )
            raise RuntimeError(f"Azure rejected deletion of {len(failed)} stale chunk(s): {detail}")
        deleted += len(responses)
    print(f"Stale-key reconciliation removed {deleted} obsolete Search chunk(s).")
    return deleted


def parser() -> argparse.ArgumentParser:
    value = os.getenv
    p = argparse.ArgumentParser(
        description="Publish a versioned REGDOCS hybrid/vector Azure AI Search index",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--normalized-dir", default=stored_path(NORMALIZE_DIR))
    p.add_argument("--endpoint", default=value("AZURE_SEARCH_ENDPOINT"))
    p.add_argument("--api-key", default=value("AZURE_SEARCH_ADMIN_KEY"))
    p.add_argument("--index-name", default=value("AZURE_SEARCH_HYBRID_INDEX", DEFAULT_HYBRID_INDEX))
    p.add_argument("--embedding-endpoint", default=value("AZURE_OPENAI_ENDPOINT"))
    p.add_argument("--embedding-api-key", default=value("AZURE_OPENAI_API_KEY"))
    p.add_argument("--vectorizer-api-key", default=value("AZURE_OPENAI_VECTORIZER_API_KEY"))
    p.add_argument(
        "--embedding-deployment",
        default=value("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", DEFAULT_EMBEDDING_MODEL),
    )
    p.add_argument("--embedding-model", default=value("AZURE_OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    p.add_argument("--dimensions", type=int, default=int(value("AZURE_OPENAI_EMBEDDING_DIMENSIONS", DEFAULT_DIMENSIONS)))
    p.add_argument("--vector-field", default=value("AZURE_SEARCH_VECTOR_FIELD", DEFAULT_VECTOR_FIELD))
    p.add_argument("--document-id", action="append")
    p.add_argument("--limit", type=int)
    p.add_argument("--embedding-batch-size", type=int, default=16)
    p.add_argument("--upload-batch-size", type=int, default=100)
    p.add_argument("--cache-db", default=stored_path(DEFAULT_CACHE_DB))
    p.add_argument("--recreate-index", action="store_true")
    p.add_argument("--prune-stale", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def validate(args: argparse.Namespace) -> None:
    args.normalized_dir = resolve_stored_path(args.normalized_dir)
    args.cache_db = resolve_stored_path(args.cache_db)
    if args.index_name == DEFAULT_INDEX_NAME:
        raise ValueError(f"Refusing to modify the lexical production index {DEFAULT_INDEX_NAME!r}; use a versioned name")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")
    if not 1 <= args.embedding_batch_size <= 128:
        raise ValueError("--embedding-batch-size must be 1..128")
    if not 1 <= args.upload_batch_size <= 1000:
        raise ValueError("--upload-batch-size must be 1..1000")
    if not 1 <= args.dimensions <= 4096:
        raise ValueError("--dimensions must be 1..4096")
    if not args.normalized_dir.is_dir():
        raise FileNotFoundError(f"Normalized directory not found: {args.normalized_dir}")
    if args.recreate_index and (args.document_id or args.limit is not None):
        raise ValueError("--recreate-index cannot be combined with a partial selection")
    if args.prune_stale and (args.document_id or args.limit is not None):
        raise ValueError("--prune-stale cannot be combined with a partial selection")
    if args.dry_run:
        return
    missing = [
        name
        for name, setting in (
            ("--endpoint/AZURE_SEARCH_ENDPOINT", args.endpoint),
            ("--embedding-endpoint/AZURE_OPENAI_ENDPOINT", args.embedding_endpoint),
        )
        if not setting
    ]
    if missing:
        raise ValueError("Missing " + ", ".join(missing))


def run(args: argparse.Namespace) -> int:
    validate(args)
    document_ids = set(map(str, args.document_id)) if args.document_id else None
    meta = scan_inputs(
        args.normalized_dir,
        document_ids,
        args.limit,
        args.upload_batch_size,
        DEFAULT_BATCH_BYTES,
    )
    print(
        f"Validated {meta['search_document_count']} chunk(s) from {meta['source_document_count']} document(s); "
        f"target={args.index_name!r}, vector={args.vector_field}/{args.dimensions}."
    )
    print(f"Embedding cache: {stored_path(args.cache_db)}")
    if args.dry_run:
        print("DRY RUN: no embeddings were generated and Azure was not contacted.")
        return 0

    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    search_credential = credential(args.api_key)
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=search_credential)
    search_client = SearchClient(endpoint=args.endpoint, index_name=args.index_name, credential=search_credential)
    embedding_client = EmbeddingClient(
        args.embedding_endpoint, args.embedding_deployment, args.dimensions, args.embedding_api_key
    )
    cache = EmbeddingCache(args.cache_db)
    try:
        definition = hybrid_index(
            args.index_name,
            args.vector_field,
            args.dimensions,
            args.embedding_endpoint,
            args.embedding_deployment,
            args.embedding_model,
            args.vectorizer_api_key or args.embedding_api_key,
        )
        print(ensure_index(index_client, definition, args.recreate_index))
        documents = iter_documents(args.normalized_dir, document_ids, args.limit)
        print(f"Embedding cache contains {cache.count()} vector(s) before this run.")
        enriched = with_embeddings(
            documents,
            embedding_client,
            cache,
            args.vector_field,
            args.embedding_batch_size,
            args.embedding_endpoint,
            args.embedding_deployment,
            args.embedding_model,
            args.dimensions,
        )
        uploaded, batch_count = upload(
            search_client, enriched, int(meta["search_document_count"]), args.upload_batch_size
        )
        print(f"Published {uploaded} hybrid chunks to {args.index_name!r} in {batch_count} upload batch(es).")
        if args.prune_stale:
            wanted = expected_keys(args.normalized_dir)
            print(f"Reconciling the live index against {len(wanted)} normalized chunk key(s).")
            prune_stale(search_client, wanted)
        return 0
    finally:
        cache.close()
        embedding_client.close()
        search_client.close()
        index_client.close()
        close = getattr(search_credential, "close", None)
        if callable(close):
            close()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parser().parse_args(list(argv) if argv is not None else None))
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
