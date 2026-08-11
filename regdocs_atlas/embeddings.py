"""Resumable Foundry embedding cache for Stage 5 hybrid search publishing."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingCandidate:
    chunk_id: str
    content: str
    content_sha256: str


class EmbeddingStore:
    def __init__(self, path: Path, *, readonly: bool = False):
        self.readonly = readonly
        if not readonly:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(
            f"file:{path}?mode=ro" if readonly else path,
            timeout=60.0,
            uri=readonly,
        )
        if not readonly:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS embeddings (
                       chunk_id TEXT NOT NULL,
                       content_sha256 TEXT NOT NULL,
                       model TEXT NOT NULL,
                       dimensions INTEGER NOT NULL,
                       vector_json TEXT NOT NULL,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       PRIMARY KEY (chunk_id, model,dimensions)
                   )"""
            )
            self.connection.commit()

    def has(self, candidate: EmbeddingCandidate, model: str, dimensions: int) -> bool:
        row = self.connection.execute(
            """SELECT 1 FROM embeddings
               WHERE chunk_id=? AND content_sha256=? AND model=? AND dimensions=?""",
            (candidate.chunk_id, candidate.content_sha256, model, dimensions),
        ).fetchone()
        return row is not None

    def vector(self, chunk_id: str, content: str, model: str, dimensions: int) -> list[float] | None:
        row = self.connection.execute(
            """SELECT vector_json FROM embeddings
               WHERE chunk_id=? AND content_sha256=? AND model=? AND dimensions=?""",
            (chunk_id, content_sha256(content), model, dimensions),
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if not isinstance(value, list) or len(value) != dimensions:
            raise ValueError(f"{chunk_id}: cached embedding has invalid dimensions")
        return [float(item) for item in value]

    def put_many(
        self,
        candidates: Sequence[EmbeddingCandidate],
        vectors: Sequence[Sequence[float]],
        model: str,
        dimensions: int,
    ) -> None:
        if len(candidates) != len(vectors):
            raise ValueError("Embedding response count does not match the request count")
        rows = []
        for candidate, vector in zip(candidates, vectors):
            if len(vector) != dimensions:
                raise ValueError(
                    f"{candidate.chunk_id}: expected {dimensions} embedding values, received {len(vector)}"
                )
            rows.append(
                (
                    candidate.chunk_id,
                    candidate.content_sha256,
                    model,
                    dimensions,
                    json.dumps(list(vector), separators=(",", ":")),
                )
            )
        self.connection.executemany(
            """INSERT INTO embeddings (chunk_id,content_sha256,model,dimensions,vector_json)
               VALUES (?,?,?,?,?)
               ON CONFLICT(chunk_id,model,dimensions) DO UPDATE SET
                 content_sha256=excluded.content_sha256,
                 vector_json=excluded.vector_json,
                 created_at=CURRENT_TIMESTAMP""",
            rows,
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "EmbeddingStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def iter_candidates(
    chunks_path: Path,
    document_ids: set[str] | None,
    limit: int | None,
) -> Iterator[EmbeddingCandidate]:
    selected = 0
    with chunks_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{chunks_path}:{line_number}: invalid JSON: {exc}") from exc
            chunk_id = str(chunk.get("id") or "").strip()
            document_id = str(chunk.get("document_id") or "").strip()
            content = str(chunk.get("content") or "").strip()
            if not chunk_id or not document_id:
                raise ValueError(f"{chunks_path}:{line_number}: id and document_id are required")
            if document_ids and document_id not in document_ids:
                continue
            if limit is not None and selected >= limit:
                return
            selected += 1
            yield EmbeddingCandidate(chunk_id, content, content_sha256(content))


class FoundryEmbeddingClient:
    def __init__(self, endpoint: str, model: str, dimensions: int, api_key: str | None):
        self.url = endpoint.rstrip("/")
        if not self.url.endswith("/openai/v1"):
            self.url += "/openai/v1"
        self.url += "/embeddings"
        self.model = model
        self.dimensions = dimensions
        self.api_key = api_key
        self.credential: Any | None = None
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Missing httpx. Run: python -m pip install -r requirements.txt") from exc
        self.client = httpx.Client(timeout=120.0)

    def headers(self) -> dict[str, str]:
        if self.api_key:
            return {"api-key": self.api_key, "Content-Type": "application/json"}
        if self.credential is None:
            from azure.identity import DefaultAzureCredential

            self.credential = DefaultAzureCredential()
        token = self.credential.get_token("https://ai.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}

    def embed(self, inputs: Sequence[str], max_attempts: int = 5) -> list[list[float]]:
        for attempt in range(1, max_attempts + 1):
            response = self.client.post(
                self.url,
                headers=self.headers(),
                json={
                    "model": self.model,
                    "input": list(inputs),
                    "dimensions": self.dimensions,
                    "encoding_format": "float",
                },
            )
            if response.status_code < 400:
                payload = response.json()
                data = sorted(payload.get("data") or [], key=lambda item: int(item.get("index", 0)))
                return [[float(value) for value in item["embedding"]] for item in data]
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == max_attempts:
                raise RuntimeError(
                    f"Foundry embeddings request failed ({response.status_code}): {response.text[:1000]}"
                )
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** (attempt - 1), 30)
            time.sleep(delay)
        raise RuntimeError("Foundry embeddings request failed")

    def close(self) -> None:
        self.client.close()
        close = getattr(self.credential, "close", None)
        if callable(close):
            close()


def embedding_plan(
    chunks_path: Path,
    cache_path: Path,
    document_ids: set[str] | None,
    limit: int | None,
    model: str,
    dimensions: int,
    max_input_characters: int,
) -> dict[str, int]:
    total = cached = missing = oversized = empty = characters = 0
    store = EmbeddingStore(cache_path, readonly=True) if cache_path.is_file() else None
    try:
        for candidate in iter_candidates(chunks_path, document_ids, limit):
            total += 1
            characters += len(candidate.content)
            empty += int(not candidate.content)
            oversized += int(len(candidate.content) > max_input_characters)
            if store is not None and store.has(candidate, model, dimensions):
                cached += 1
            else:
                missing += 1
    finally:
        if store is not None:
            store.close()
    return {
        "selected_chunks": total,
        "cached_embeddings": cached,
        "missing_embeddings": missing,
        "empty_chunks": empty,
        "oversized_chunks": oversized,
        "input_characters": characters,
    }


def generate_embeddings(
    chunks_path: Path,
    cache_path: Path,
    document_ids: set[str] | None,
    limit: int | None,
    endpoint: str,
    model: str,
    dimensions: int,
    api_key: str | None,
    batch_size: int,
    max_input_characters: int,
) -> int:
    plan = embedding_plan(
        chunks_path,
        cache_path,
        document_ids,
        limit,
        model,
        dimensions,
        max_input_characters,
    )
    if plan["empty_chunks"] or plan["oversized_chunks"]:
        raise ValueError(
            "Embedding preflight failed: "
            f"empty={plan['empty_chunks']}, oversized={plan['oversized_chunks']}. "
            "Fix or structurally split these chunks before making paid embedding calls."
        )
    client = FoundryEmbeddingClient(endpoint, model, dimensions, api_key)
    generated = 0
    try:
        with EmbeddingStore(cache_path) as store:
            batch: list[EmbeddingCandidate] = []
            for candidate in iter_candidates(chunks_path, document_ids, limit):
                if store.has(candidate, model, dimensions):
                    continue
                batch.append(candidate)
                if len(batch) < batch_size:
                    continue
                vectors = client.embed([item.content for item in batch])
                store.put_many(batch, vectors, model, dimensions)
                generated += len(batch)
                print(f"Embedded {generated}/{plan['missing_embeddings']} missing chunk(s)")
                batch = []
            if batch:
                vectors = client.embed([item.content for item in batch])
                store.put_many(batch, vectors, model, dimensions)
                generated += len(batch)
                print(f"Embedded {generated}/{plan['missing_embeddings']} missing chunk(s)")
    finally:
        client.close()
    return generated
