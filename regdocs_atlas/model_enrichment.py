"""Explicit, resumable Microsoft Foundry extraction for Stage 6 intelligence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from .paths import ENRICH_DIR, NORMALIZE_DIR, resolve_stored_path, stored_path

PROMPT_VERSION = "regdocs-extract-2026-08-11-v1"
SCHEMA_VERSION = "regdocs-intelligence-v1"
ORIGIN = "foundry_model"
OUTPUT_NAMES = ("entities", "relations", "events", "claims", "obligations")


def stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part).strip().casefold() for part in parts if str(part).strip())
    return f"{prefix}:{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            yield value


@dataclass(frozen=True)
class ExtractionBatch:
    request_hash: str
    document_id: str
    metadata: dict[str, Any]
    chunks: tuple[dict[str, Any], ...]
    input_characters: int


def extraction_batches(
    chunks_path: Path,
    document_ids: set[str] | None,
    limit_documents: int | None,
    max_input_characters: int,
    max_chunks: int,
) -> Iterator[ExtractionBatch]:
    current_document = ""
    document_count = 0
    pending: list[dict[str, Any]] = []
    pending_characters = 0
    metadata: dict[str, Any] = {}

    def flush() -> ExtractionBatch | None:
        nonlocal pending, pending_characters
        if not pending:
            return None
        request_input = {
            "document_id": current_document,
            "metadata": metadata,
            "chunks": pending,
            "prompt_version": PROMPT_VERSION,
        }
        batch = ExtractionBatch(
            hashlib.sha256(stable_json(request_input).encode()).hexdigest(),
            current_document,
            dict(metadata),
            tuple(pending),
            pending_characters,
        )
        pending, pending_characters = [], 0
        return batch

    for chunk in iter_jsonl(chunks_path):
        document_id = str(chunk.get("document_id") or "").strip()
        content = str(chunk.get("content") or "").strip()
        if not document_id or not content or (document_ids and document_id not in document_ids):
            continue
        if document_id != current_document:
            previous = flush()
            if previous:
                yield previous
            current_document = document_id
            document_count += 1
            if limit_documents is not None and document_count > limit_documents:
                return
            metadata = {
                key: chunk.get(key)
                for key in (
                    "title", "filing_id", "filing_number", "filing_date", "company", "project",
                    "submitter", "source_url",
                )
            }
        item = {
            "chunk_id": str(chunk.get("id")),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "heading": chunk.get("heading"),
            "content": content,
        }
        item_size = len(stable_json(item))
        if item_size > max_input_characters:
            raise ValueError(
                f"{item['chunk_id']}: extraction input exceeds --model-max-input-characters; "
                "structurally split the normalized chunk first"
            )
        if pending and (len(pending) >= max_chunks or pending_characters + item_size > max_input_characters):
            completed = flush()
            if completed:
                yield completed
        pending.append(item)
        pending_characters += item_size
    final = flush()
    if final:
        yield final


class ExtractionCache:
    def __init__(self, path: Path, readonly: bool):
        self.path = path
        if not readonly:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(f"file:{path}?mode=ro" if readonly else path, uri=readonly)
        if not readonly:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS extraction_results (
                     request_hash TEXT PRIMARY KEY, document_id TEXT NOT NULL, model TEXT NOT NULL,
                     prompt_version TEXT NOT NULL, response_json TEXT NOT NULL,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )
            self.connection.commit()

    def get(self, request_hash: str, model: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT response_json FROM extraction_results WHERE request_hash=? AND model=? AND prompt_version=?",
            (request_hash, model, PROMPT_VERSION),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, batch: ExtractionBatch, model: str, response: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO extraction_results(request_hash,document_id,model,prompt_version,response_json)
               VALUES(?,?,?,?,?) ON CONFLICT(request_hash) DO UPDATE SET
               document_id=excluded.document_id, model=excluded.model,
               prompt_version=excluded.prompt_version, response_json=excluded.response_json,
               created_at=CURRENT_TIMESTAMP""",
            (batch.request_hash, batch.document_id, model, PROMPT_VERSION, stable_json(response)),
        )
        self.connection.commit()

    def all_responses(self, model: str) -> Iterator[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT response_json FROM extraction_results WHERE model=? AND prompt_version=? ORDER BY document_id,request_hash",
            (model, PROMPT_VERSION),
        )
        for row in rows:
            yield json.loads(row[0])

    def close(self) -> None:
        self.connection.close()


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "events": {"type": "array", "items": {"$ref": "#/$defs/event"}},
        "claims": {"type": "array", "items": {"$ref": "#/$defs/claim"}},
        "obligations": {"type": "array", "items": {"$ref": "#/$defs/obligation"}},
        "relationships": {"type": "array", "items": {"$ref": "#/$defs/relationship"}},
    },
    "required": ["events", "claims", "obligations", "relationships"],
    "$defs": {
        "event": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "event_type": {"type": "string"}, "title": {"type": "string"},
                "summary": {"type": "string"}, "date_start": {"type": "string"},
                "date_end": {"type": ["string", "null"]},
                "date_precision": {"type": "string", "enum": ["day", "month", "year", "range", "unknown"]},
                "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["event_type", "title", "summary", "date_start", "date_end", "date_precision", "evidence_chunk_ids", "confidence"],
        },
        "claim": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "claim_type": {"type": "string"}, "statement": {"type": "string"},
                "claimant": {"type": ["string", "null"]}, "subject": {"type": ["string", "null"]},
                "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["claim_type", "statement", "claimant", "subject", "evidence_chunk_ids", "confidence"],
        },
        "obligation": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "obligation_type": {"type": "string"}, "obligated_party": {"type": ["string", "null"]},
                "action": {"type": "string"}, "deadline": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["obligation_type", "obligated_party", "action", "deadline", "status", "evidence_chunk_ids", "confidence"],
        },
        "relationship": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "source_name": {"type": "string"}, "source_type": {"type": "string"},
                "target_name": {"type": "string"}, "target_type": {"type": "string"},
                "relationship_type": {"type": "string"},
                "evidence_chunk_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["source_name", "source_type", "target_name", "target_type", "relationship_type", "evidence_chunk_ids", "confidence"],
        },
    },
}


class FoundryExtractionClient:
    def __init__(self, endpoint: str, model: str, api_key: str | None):
        import httpx

        base = endpoint.rstrip("/")
        self.url = f"{base if base.endswith('/openai/v1') else base + '/openai/v1'}/responses"
        self.model = model
        self.api_key = api_key
        self.credential: Any | None = None
        self.client = httpx.Client(timeout=180.0)

    def headers(self) -> dict[str, str]:
        if self.api_key:
            return {"api-key": self.api_key, "Content-Type": "application/json"}
        if self.credential is None:
            from azure.identity import DefaultAzureCredential

            self.credential = DefaultAzureCredential()
        token = self.credential.get_token("https://ai.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}

    def extract(self, batch: ExtractionBatch) -> dict[str, Any]:
        allowed = [item["chunk_id"] for item in batch.chunks]
        instructions = (
            "Extract only explicit regulatory facts from the supplied REGDOCS chunks. "
            "Do not infer unstated events, claims, obligations, entities, or dates. "
            "Every record must cite one or more exact chunk_id values from the input. "
            "Use occurrence dates for events; filing dates are handled separately. "
            "Return empty arrays when the evidence does not support a category."
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": stable_json({"document": batch.metadata, "allowed_chunk_ids": allowed, "chunks": batch.chunks}),
            "max_output_tokens": 8000,
            "text": {"format": {"type": "json_schema", "name": "regdocs_intelligence", "strict": True, "schema": EXTRACTION_SCHEMA}},
        }
        for attempt in range(1, 6):
            response = self.client.post(self.url, headers=self.headers(), json=payload)
            if response.status_code < 400:
                body = response.json()
                if body.get("status") not in {None, "completed"}:
                    raise RuntimeError(f"Foundry extraction ended with status {body.get('status')!r}")
                refusals = [
                    str(content.get("refusal") or "Model refused the extraction request")
                    for item in body.get("output") or []
                    for content in item.get("content") or []
                    if content.get("type") == "refusal"
                ]
                if refusals:
                    raise RuntimeError(refusals[0])
                output_text = body.get("output_text")
                if not output_text:
                    output_text = "".join(
                        str(content.get("text") or "")
                        for item in body.get("output") or []
                        for content in item.get("content") or []
                        if content.get("type") == "output_text"
                    )
                parsed = json.loads(output_text)
                return validate_response(parsed, batch, self.model)
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == 5:
                raise RuntimeError(f"Foundry extraction failed ({response.status_code}): {response.text[:500]}")
            retry = response.headers.get("retry-after")
            time.sleep(float(retry) if retry and retry.isdigit() else min(2 ** (attempt - 1), 30))
        raise RuntimeError("Foundry extraction failed")

    def close(self) -> None:
        self.client.close()
        close = getattr(self.credential, "close", None)
        if callable(close):
            close()


def validate_response(response: dict[str, Any], batch: ExtractionBatch, model: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("Foundry extraction did not return an object")
    allowed = {str(item["chunk_id"]): item for item in batch.chunks}
    output: dict[str, Any] = {name: [] for name in ("events", "claims", "obligations", "relationships")}
    for kind in output:
        records = response.get(kind)
        if not isinstance(records, list):
            raise ValueError(f"Foundry extraction field {kind!r} is not an array")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"Foundry extraction {kind} contains a non-object")
            citations = list(dict.fromkeys(map(str, record.get("evidence_chunk_ids") or [])))
            if not citations or any(citation not in allowed for citation in citations):
                raise ValueError(f"Foundry extraction {kind} contains missing or invalid chunk evidence")
            confidence = float(record.get("confidence", 0))
            if not 0 <= confidence <= 1:
                raise ValueError(f"Foundry extraction {kind} confidence is outside 0..1")
            required_text = {
                "events": ("event_type", "title", "summary", "date_start"),
                "claims": ("claim_type", "statement"),
                "obligations": ("obligation_type", "action"),
                "relationships": ("source_name", "source_type", "target_name", "target_type", "relationship_type"),
            }[kind]
            if any(not str(record.get(field) or "").strip() for field in required_text):
                raise ValueError(f"Foundry extraction {kind} contains an empty required value")
            if kind == "events":
                date_values = [record.get("date_start"), record.get("date_end")]
                if any(
                    value is not None and not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", str(value))
                    for value in date_values
                ):
                    raise ValueError("Foundry extraction event dates must be YYYY, YYYY-MM, or YYYY-MM-DD")
            output[kind].append({**record, "evidence_chunk_ids": citations, "confidence": confidence})
    return materialize_response(output, batch, model, allowed)


def materialize_response(
    response: dict[str, Any], batch: ExtractionBatch, model: str, evidence: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    metadata = batch.metadata
    common = {
        "document_id": batch.document_id, "filing_id": metadata.get("filing_id"),
        "filing_number": metadata.get("filing_number"), "company": metadata.get("company"),
        "project": metadata.get("project"), "source_url": metadata.get("source_url"),
        "schema_version": SCHEMA_VERSION, "origin": ORIGIN,
        "extractor_version": f"{PROMPT_VERSION}:{model}", "review_status": "unreviewed",
    }
    out: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in OUTPUT_NAMES}

    def evidence_fields(citations: Sequence[str]) -> dict[str, Any]:
        starts = [evidence[c].get("page_start") for c in citations if isinstance(evidence[c].get("page_start"), int)]
        ends = [evidence[c].get("page_end") for c in citations if isinstance(evidence[c].get("page_end"), int)]
        return {"evidence_chunk_ids": list(citations), "evidence_page_start": min(starts) if starts else None, "evidence_page_end": max(ends) if ends else None}

    for item in response["events"]:
        citations = item["evidence_chunk_ids"]
        record_id = stable_id("event", batch.document_id, item["event_type"], item["title"], item["date_start"], *citations)
        record = {"id": record_id, **item, "date_basis": "occurrence_date", "entity_ids": [], "chunk_id": citations[0], "page_start": None, "page_end": None, **common}
        fields = evidence_fields(citations)
        record["page_start"], record["page_end"] = fields["evidence_page_start"], fields["evidence_page_end"]
        out["events"][record_id] = record
    for kind in ("claims", "obligations"):
        for item in response[kind]:
            citations = item["evidence_chunk_ids"]
            identity = item.get("statement") or item.get("action")
            record_id = stable_id(kind[:-1], batch.document_id, identity, *citations)
            out[kind][record_id] = {"id": record_id, **item, **evidence_fields(citations), **common}
    for item in response["relationships"]:
        source_id = stable_id(item["source_type"], item["source_name"])
        target_id = stable_id(item["target_type"], item["target_name"])
        for entity_id, entity_type, name in ((source_id, item["source_type"], item["source_name"]), (target_id, item["target_type"], item["target_name"])):
            out["entities"][entity_id] = {"id": entity_id, "entity_type": entity_type, "name": name, "aliases": [], "external_ids": {}, "source_url": metadata.get("source_url"), "schema_version": SCHEMA_VERSION, "origin": ORIGIN}
        citations = item["evidence_chunk_ids"]
        relation_id = stable_id("relation", source_id, item["relationship_type"], target_id, batch.document_id, *citations)
        out["relations"][relation_id] = {
            "id": relation_id, "source_id": source_id, "target_id": target_id,
            "relationship_type": item["relationship_type"], **evidence_fields(citations),
            "confidence": item["confidence"], **common,
        }
    return {name: list(records.values()) for name, records in out.items()}


def write_outputs(output_dir: Path, responses: Iterator[dict[str, Any]]) -> dict[str, int]:
    aggregate: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in OUTPUT_NAMES}
    for response in responses:
        for name in OUTPUT_NAMES:
            for record in response.get(name) or []:
                aggregate[name][str(record["id"])] = record
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, records in aggregate.items():
        path = output_dir / f"{name}.jsonl"
        partial = path.with_name(path.name + ".partial")
        with partial.open("w", encoding="utf-8") as handle:
            for key in sorted(records):
                handle.write(stable_json(records[key]) + "\n")
        os.replace(partial, path)
        counts[name] = len(records)
    return counts


def run_model_extraction(args: Any) -> int:
    normalized_dir = resolve_stored_path(args.normalized_dir)
    output_dir = resolve_stored_path(args.model_output_dir or (Path(args.output_dir) / "model"))
    chunks_path = normalized_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        raise FileNotFoundError(f"Expected {chunks_path}")
    document_ids = set(map(str, args.document_id)) if args.document_id else None
    if not args.model_deployment:
        raise ValueError("Pass --model-deployment or set FOUNDRY_MODEL_DEPLOYMENT")
    batches = list(extraction_batches(chunks_path, document_ids, args.limit, args.model_max_input_characters, args.model_max_chunks))
    if not batches:
        raise ValueError("No normalized chunks matched the model extraction selection")
    cache_path = resolve_stored_path(args.model_cache or (output_dir / "extraction.sqlite"))
    cache = ExtractionCache(cache_path, True) if cache_path.is_file() else None
    cached = sum(1 for batch in batches if cache and cache.get(batch.request_hash, args.model_deployment))
    if cache:
        cache.close()
    report = {
        "stage": "enrich_model", "prompt_version": PROMPT_VERSION, "model": args.model_deployment,
        "documents": len({batch.document_id for batch in batches}), "requests": len(batches),
        "cached_requests": cached, "missing_requests": len(batches) - cached,
        "input_characters": sum(batch.input_characters for batch in batches),
        "cache": stored_path(cache_path), "output_dir": stored_path(output_dir),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        print("DRY RUN: Foundry was not contacted and the extraction cache/output ledgers were not changed.")
        return 0
    if not args.foundry_endpoint:
        raise ValueError("Pass --foundry-endpoint or set FOUNDRY_PROJECT_ENDPOINT")
    client = FoundryExtractionClient(args.foundry_endpoint, args.model_deployment, args.foundry_api_key)
    store = ExtractionCache(cache_path, False)
    try:
        completed = 0
        for batch in batches:
            if store.get(batch.request_hash, args.model_deployment) is None:
                store.put(batch, args.model_deployment, client.extract(batch))
            completed += 1
            print(f"Extracted/cached request {completed}/{len(batches)}")
        counts = write_outputs(output_dir, store.all_responses(args.model_deployment))
    finally:
        store.close()
        client.close()
    print(json.dumps({"materialized": counts}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
