#!/usr/bin/env python3
"""Stage 6: derive evidence-backed regulatory intelligence artifacts.

The first enrichment layer is deliberately deterministic. It turns normalized
document metadata into reusable entities, relationships, and filing-activity
events without making model calls. Later model extraction writes to the same
versioned schemas and must retain chunk/page evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..paths import ENRICH_DIR, NORMALIZE_DIR, resolve_stored_path, stored_path

COMPONENT_VERSION = "6.0.0"
SCHEMA_VERSION = "regdocs-intelligence-v1"
ORIGIN = "deterministic_metadata"
OUTPUT_NAMES = ("entities", "relations", "events", "claims", "obligations")
DEFAULT_BATCH_SIZE = 500


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part).strip().casefold() for part in parts if str(part).strip())
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def text(value: object) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield line_number, record


def merge_model_artifacts(
    artifacts: DerivedArtifacts,
    model_dir: Path,
    document_ids: set[str] | None,
) -> dict[str, int]:
    """Merge explicitly requested, reviewable model ledgers into publish artifacts."""
    paths = {name: model_dir / f"{name}.jsonl" for name in OUTPUT_NAMES}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Model enrichment directory is incomplete: {', '.join(missing)}")
    selected_relations: list[dict[str, Any]] = []
    used_entity_ids: set[str] = set()
    counts: dict[str, int] = {}
    for _, record in iter_jsonl(paths["relations"]):
        if document_ids and str(record.get("document_id") or "") not in document_ids:
            continue
        selected_relations.append(record)
        used_entity_ids.update((str(record.get("source_id") or ""), str(record.get("target_id") or "")))
    for _, record in iter_jsonl(paths["entities"]):
        if document_ids and str(record.get("id") or "") not in used_entity_ids:
            continue
        artifacts.entities[str(record["id"])] = record
    for record in selected_relations:
        artifacts.relations[str(record["id"])] = record
    counts["entities"] = len(used_entity_ids) if document_ids else sum(1 for _ in iter_jsonl(paths["entities"]))
    counts["relations"] = len(selected_relations)
    for name in ("events", "claims", "obligations"):
        target = getattr(artifacts, name)
        added = 0
        for _, record in iter_jsonl(paths[name]):
            if document_ids and str(record.get("document_id") or "") not in document_ids:
                continue
            target[str(record["id"])] = record
            added += 1
        counts[name] = added
    return counts


def selected_documents(
    path: Path,
    document_ids: set[str] | None,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    selected = 0
    for line_number, document in iter_jsonl(path):
        document_id = text(document.get("document_id"))
        if not document_id:
            raise ValueError(f"{path}:{line_number}: document_id is required")
        if document_ids and document_id not in document_ids:
            continue
        if limit is not None and selected >= limit:
            return
        selected += 1
        yield document


@dataclass
class DerivedArtifacts:
    entities: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    events: dict[str, dict[str, Any]]
    claims: dict[str, dict[str, Any]]
    obligations: dict[str, dict[str, Any]]
    documents_processed: int = 0

    @classmethod
    def empty(cls) -> "DerivedArtifacts":
        return cls({}, {}, {}, {}, {})

    def records(self, name: str) -> list[dict[str, Any]]:
        values = getattr(self, name)
        return [values[key] for key in sorted(values)]


def entity(
    entity_type: str,
    name: str,
    *,
    direct_id: str | None = None,
    aliases: Sequence[str] = (),
    external_ids: dict[str, str] | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    entity_id = direct_id or stable_id(entity_type, name)
    return {
        "id": entity_id,
        "entity_type": entity_type,
        "name": name,
        "aliases": list(dict.fromkeys(alias for alias in aliases if alias and alias != name)),
        "external_ids": external_ids or {},
        "source_url": source_url,
        "schema_version": SCHEMA_VERSION,
        "origin": ORIGIN,
    }


def remember_entity(artifacts: DerivedArtifacts, record: dict[str, Any]) -> str:
    entity_id = str(record["id"])
    current = artifacts.entities.get(entity_id)
    if current is None:
        artifacts.entities[entity_id] = record
        return entity_id
    current["aliases"] = sorted(set(string_list(current.get("aliases"))) | set(string_list(record.get("aliases"))))
    current_ids = current.get("external_ids") if isinstance(current.get("external_ids"), dict) else {}
    new_ids = record.get("external_ids") if isinstance(record.get("external_ids"), dict) else {}
    current["external_ids"] = {**current_ids, **new_ids}
    if not current.get("source_url") and record.get("source_url"):
        current["source_url"] = record["source_url"]
    return entity_id


def add_relation(
    artifacts: DerivedArtifacts,
    source_id: str,
    target_id: str,
    relationship_type: str,
    document: dict[str, Any],
) -> None:
    document_id = str(document["document_id"])
    relation_id = stable_id("relation", source_id, relationship_type, target_id, document_id)
    artifacts.relations[relation_id] = {
        "id": relation_id,
        "source_id": source_id,
        "target_id": target_id,
        "relationship_type": relationship_type,
        "document_id": document_id,
        "filing_id": text(document.get("filing_id")),
        "filing_number": text(document.get("filing_number")),
        "company": text(document.get("company")),
        "project": text(document.get("project")),
        "evidence_chunk_ids": [],
        "evidence_page_start": None,
        "evidence_page_end": None,
        "source_url": text(document.get("source_url")),
        "confidence": 1.0,
        "schema_version": SCHEMA_VERSION,
        "origin": ORIGIN,
        "extractor_version": COMPONENT_VERSION,
        "review_status": "not_required",
    }


def add_filing_event(
    artifacts: DerivedArtifacts,
    document: dict[str, Any],
    entity_ids: Sequence[str],
) -> None:
    filing_date = text(document.get("filing_date"))
    if not filing_date:
        return
    document_id = str(document["document_id"])
    event_id = stable_id("event", "filing_activity", document_id, filing_date)
    title = text(document.get("title")) or f"Document {document_id} filed"
    artifacts.events[event_id] = {
        "id": event_id,
        "event_type": "filing_activity",
        "title": title,
        "summary": f"{title} was filed on {filing_date}.",
        "date_start": filing_date,
        "date_end": filing_date,
        "date_precision": "day",
        "date_basis": "filing_date",
        "entity_ids": list(dict.fromkeys(entity_ids)),
        "document_id": document_id,
        "filing_id": text(document.get("filing_id")),
        "filing_number": text(document.get("filing_number")),
        "company": text(document.get("company")),
        "project": text(document.get("project")),
        "chunk_id": None,
        "page_start": None,
        "page_end": None,
        "source_url": text(document.get("source_url")),
        "confidence": 1.0,
        "schema_version": SCHEMA_VERSION,
        "origin": ORIGIN,
        "extractor_version": COMPONENT_VERSION,
        "review_status": "not_required",
    }


def derive_document(artifacts: DerivedArtifacts, document: dict[str, Any]) -> None:
    document_id = str(document["document_id"])
    source_url = text(document.get("source_url"))
    document_entity = remember_entity(
        artifacts,
        entity(
            "document",
            text(document.get("title")) or f"Document {document_id}",
            direct_id=f"document:{document_id}",
            external_ids={"document_id": document_id},
            source_url=source_url,
        ),
    )
    event_entities = [document_entity]

    filing_id = text(document.get("filing_id"))
    filing_number = text(document.get("filing_number"))
    filing_key = filing_id or filing_number
    filing_entity: str | None = None
    if filing_key:
        filing_entity = remember_entity(
            artifacts,
            entity(
                "filing",
                filing_number or f"Filing {filing_id}",
                direct_id=f"filing:{filing_id}" if filing_id else stable_id("filing", filing_number),
                aliases=[f"Filing {filing_id}" if filing_id else ""],
                external_ids={
                    key: value
                    for key, value in (("filing_id", filing_id), ("filing_number", filing_number))
                    if value
                },
                source_url=source_url,
            ),
        )
        event_entities.append(filing_entity)
        add_relation(artifacts, filing_entity, document_entity, "CONTAINS", document)

    company = text(document.get("company"))
    if company:
        company_external_ids = {}
        company_id = text(document.get("company_id"))
        if company_id:
            company_external_ids["company_id"] = company_id
        company_entity = remember_entity(
            artifacts,
            entity("organization", company, external_ids=company_external_ids),
        )
        event_entities.append(company_entity)
        add_relation(artifacts, document_entity, company_entity, "RELATES_TO", document)
        if filing_entity:
            add_relation(artifacts, filing_entity, company_entity, "RELATES_TO", document)

    submitter = text(document.get("submitter"))
    if submitter:
        submitter_entity = remember_entity(artifacts, entity("organization", submitter))
        event_entities.append(submitter_entity)
        add_relation(artifacts, submitter_entity, document_entity, "SUBMITTED", document)

    project = text(document.get("project"))
    if project:
        project_entity = remember_entity(artifacts, entity("project", project))
        event_entities.append(project_entity)
        add_relation(artifacts, document_entity, project_entity, "CONCERNS", document)
        if filing_entity:
            add_relation(artifacts, filing_entity, project_entity, "CONCERNS", document)

    add_filing_event(artifacts, document, event_entities)
    artifacts.documents_processed += 1


def derive(documents: Iterable[dict[str, Any]]) -> DerivedArtifacts:
    artifacts = DerivedArtifacts.empty()
    for document in documents:
        derive_document(artifacts, document)
    return artifacts


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl_atomic(path: Path, records: Sequence[dict[str, Any]]) -> str:
    partial = path.with_name(path.name + ".partial")
    digest = hashlib.sha256()
    with partial.open("wb") as handle:
        for record in records:
            line = (stable_json(record) + "\n").encode("utf-8")
            handle.write(line)
            digest.update(line)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    return digest.hexdigest()


def azure_credential(api_key: str | None) -> Any:
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError("Missing Azure dependencies. Run: python -m pip install -r requirements.txt") from exc
    return AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()


def search_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def intelligence_indexes(names: dict[str, str]) -> dict[str, Any]:
    from azure.search.documents.indexes.models import (
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SearchableField,
        SimpleField,
    )

    string = SearchFieldDataType.STRING
    strings = "Collection(Edm.String)"
    entities = SearchIndex(
        name=names["entities"],
        fields=[
            SimpleField(name="key", type=string, key=True),
            SimpleField(name="id", type=string, filterable=True),
            SimpleField(name="entity_type", type=string, filterable=True, facetable=True),
            SearchableField(name="name", type=string, filterable=True),
            SearchField(name="aliases", type=strings, searchable=True, filterable=True, retrievable=True),
            SimpleField(name="source_url", type=string),
            SimpleField(name="origin", type=string, filterable=True, facetable=True),
            SimpleField(name="schema_version", type=string, filterable=True),
        ],
    )
    relations = SearchIndex(
        name=names["relations"],
        fields=[
            SimpleField(name="key", type=string, key=True),
            SimpleField(name="id", type=string, filterable=True),
            SimpleField(name="source_id", type=string, filterable=True),
            SimpleField(name="target_id", type=string, filterable=True),
            SimpleField(name="relationship_type", type=string, filterable=True, facetable=True),
            SimpleField(name="document_id", type=string, filterable=True),
            SimpleField(name="filing_id", type=string, filterable=True),
            SimpleField(name="filing_number", type=string, filterable=True),
            SearchableField(name="company", type=string, filterable=True, facetable=True),
            SearchableField(name="project", type=string, filterable=True, facetable=True),
            SearchField(name="evidence_chunk_ids", type=strings, filterable=True, retrievable=True),
            SimpleField(name="evidence_page_start", type=SearchFieldDataType.INT32, filterable=True),
            SimpleField(name="evidence_page_end", type=SearchFieldDataType.INT32, filterable=True),
            SimpleField(name="source_url", type=string),
            SimpleField(name="confidence", type=SearchFieldDataType.DOUBLE, filterable=True),
            SimpleField(name="origin", type=string, filterable=True, facetable=True),
            SimpleField(name="schema_version", type=string, filterable=True),
            SimpleField(name="extractor_version", type=string, filterable=True),
            SimpleField(name="review_status", type=string, filterable=True, facetable=True),
        ],
    )
    events = SearchIndex(
        name=names["events"],
        fields=[
            SimpleField(name="key", type=string, key=True),
            SimpleField(name="id", type=string, filterable=True),
            SimpleField(name="event_type", type=string, filterable=True, facetable=True),
            SearchableField(name="title", type=string),
            SearchableField(name="summary", type=string),
            SimpleField(name="date_start", type=string, filterable=True, sortable=True),
            SimpleField(name="date_end", type=string, filterable=True, sortable=True),
            SimpleField(name="date_precision", type=string, filterable=True, facetable=True),
            SimpleField(name="date_basis", type=string, filterable=True, facetable=True),
            SearchField(name="entity_ids", type=strings, filterable=True, retrievable=True),
            SearchField(name="evidence_chunk_ids", type=strings, filterable=True, retrievable=True),
            SimpleField(name="document_id", type=string, filterable=True),
            SimpleField(name="filing_id", type=string, filterable=True),
            SimpleField(name="filing_number", type=string, filterable=True),
            SearchableField(name="company", type=string, filterable=True, facetable=True),
            SearchableField(name="project", type=string, filterable=True, facetable=True),
            SimpleField(name="chunk_id", type=string, filterable=True),
            SimpleField(name="page_start", type=SearchFieldDataType.INT32, filterable=True),
            SimpleField(name="page_end", type=SearchFieldDataType.INT32, filterable=True),
            SimpleField(name="source_url", type=string),
            SimpleField(name="confidence", type=SearchFieldDataType.DOUBLE, filterable=True),
            SimpleField(name="origin", type=string, filterable=True, facetable=True),
            SimpleField(name="schema_version", type=string, filterable=True),
            SimpleField(name="extractor_version", type=string, filterable=True),
            SimpleField(name="review_status", type=string, filterable=True, facetable=True),
        ],
    )
    return {"entities": entities, "relations": relations, "events": events}


def ensure_intelligence_indexes(client: Any, names: dict[str, str], recreate: bool) -> None:
    for kind, expected in intelligence_indexes(names).items():
        if recreate:
            try:
                client.delete_index(expected.name)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
        try:
            current = client.get_index(expected.name)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            client.create_index(expected)
            print(f"Created {kind} index {expected.name}")
            continue
        missing = sorted({field.name for field in expected.fields} - {field.name for field in current.fields})
        if missing:
            raise RuntimeError(
                f"Existing {kind} index {expected.name!r} is missing fields: {', '.join(missing)}. "
                "Use --recreate-indexes after confirming the target names."
            )
        print(f"Using existing {kind} index {expected.name}")


def upload_records(client: Any, records: Sequence[dict[str, Any]], batch_size: int) -> int:
    uploaded = 0
    for start in range(0, len(records), batch_size):
        batch = []
        for item in records[start : start + batch_size]:
            # external_ids remains in the durable entity ledger; the v1 Search
            # projection intentionally omits the arbitrary map until it has a
            # stable query contract.
            projected = {key: value for key, value in item.items() if key != "external_ids"}
            batch.append({"key": search_key(str(item["id"])), **projected})
        results = client.merge_or_upload_documents(documents=batch)
        failures = [result for result in results if not bool(getattr(result, "succeeded", False))]
        if failures:
            details = "; ".join(
                f"{getattr(item, 'key', '?')}: {getattr(item, 'error_message', '')}" for item in failures[:10]
            )
            raise RuntimeError(f"Azure rejected {len(failures)} intelligence record(s): {details}")
        uploaded += len(results)
    return uploaded


def publish_intelligence(
    endpoint: str,
    api_key: str | None,
    names: dict[str, str],
    artifacts: DerivedArtifacts,
    batch_size: int,
    recreate: bool,
) -> dict[str, int]:
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    credential = azure_credential(api_key)
    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    clients: list[Any] = []
    try:
        ensure_intelligence_indexes(index_client, names, recreate)
        published: dict[str, int] = {}
        for kind in ("entities", "relations", "events"):
            client = SearchClient(endpoint=endpoint, index_name=names[kind], credential=credential)
            clients.append(client)
            published[kind] = upload_records(client, artifacts.records(kind), batch_size)
            print(f"Published {published[kind]} record(s) to {names[kind]}")
        return published
    finally:
        for client in clients:
            client.close()
        index_client.close()
        close = getattr(credential, "close", None)
        if callable(close):
            close()


def summary(artifacts: DerivedArtifacts) -> dict[str, Any]:
    return {
        "documents_processed": artifacts.documents_processed,
        "entities": len(artifacts.entities),
        "entity_types": dict(sorted(Counter(item["entity_type"] for item in artifacts.entities.values()).items())),
        "relations": len(artifacts.relations),
        "relationship_types": dict(
            sorted(Counter(item["relationship_type"] for item in artifacts.relations.values()).items())
        ),
        "events": len(artifacts.events),
        "event_types": dict(sorted(Counter(item["event_type"] for item in artifacts.events.values()).items())),
        "claims": len(artifacts.claims),
        "obligations": len(artifacts.obligations),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="REGDOCS Stage 6: deterministic regulatory intelligence derivation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    value.add_argument("--normalized-dir", default=stored_path(NORMALIZE_DIR))
    value.add_argument("--output-dir", default=stored_path(ENRICH_DIR))
    value.add_argument("--document-id", action="append")
    value.add_argument("--limit", type=int)
    value.add_argument("--all", action="store_true")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--publish", action="store_true")
    value.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    value.add_argument("--api-key", default=os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    value.add_argument("--entities-index", default=os.getenv("AZURE_SEARCH_ENTITIES_INDEX", "regdocs-entities"))
    value.add_argument("--relations-index", default=os.getenv("AZURE_SEARCH_RELATIONS_INDEX", "regdocs-relations"))
    value.add_argument("--events-index", default=os.getenv("AZURE_SEARCH_EVENTS_INDEX", "regdocs-events"))
    value.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    value.add_argument("--recreate-indexes", action="store_true")
    value.add_argument("--include-model-dir")
    value.add_argument("--model-extract", action="store_true")
    value.add_argument("--model-output-dir")
    value.add_argument("--model-cache")
    value.add_argument("--foundry-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    value.add_argument("--foundry-api-key", default=os.getenv("FOUNDRY_API_KEY"))
    value.add_argument("--model-deployment", default=os.getenv("FOUNDRY_MODEL_DEPLOYMENT"))
    value.add_argument("--model-max-input-characters", type=int, default=60000)
    value.add_argument("--model-max-chunks", type=int, default=16)
    value.add_argument("--version", action="store_true")
    return value


def run(args: argparse.Namespace) -> int:
    if args.model_extract:
        from ..model_enrichment import run_model_extraction

        if args.all and (args.document_id or args.limit is not None):
            raise ValueError("--all cannot be combined with --document-id or --limit")
        if not 1 <= args.model_max_chunks <= 100:
            raise ValueError("--model-max-chunks must be 1..100")
        if args.model_max_input_characters < 1000:
            raise ValueError("--model-max-input-characters must be >= 1000")
        return run_model_extraction(args)
    if args.all:
        raise ValueError("--all is only used by the explicit enrich extract action")
    normalized_dir = resolve_stored_path(args.normalized_dir)
    output_dir = resolve_stored_path(args.output_dir)
    documents_path = normalized_dir / "documents.jsonl"
    if not documents_path.is_file():
        raise FileNotFoundError(f"Expected {documents_path}")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")
    if not 1 <= args.batch_size <= 1000:
        raise ValueError("--batch-size must be 1..1000")
    if args.publish and not args.endpoint:
        raise ValueError("Pass --endpoint or set AZURE_SEARCH_ENDPOINT before publishing")
    if args.recreate_indexes and not args.publish:
        raise ValueError("--recreate-indexes requires --publish")
    document_ids = set(map(str, args.document_id)) if args.document_id else None
    artifacts = derive(selected_documents(documents_path, document_ids, args.limit))
    if not artifacts.documents_processed:
        raise ValueError("No normalized documents matched the selection")

    model_merge = None
    if args.include_model_dir:
        model_merge = merge_model_artifacts(
            artifacts,
            resolve_stored_path(args.include_model_dir),
            document_ids,
        )
    result = summary(artifacts)
    result.update(
        {
            "stage": "enrich",
            "component_version": COMPONENT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "input": stored_path(documents_path),
            "input_sha256": sha256_file(documents_path),
            "selection": {"document_id": args.document_id, "limit": args.limit},
            "model_artifacts_merged": model_merge,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        print("DRY RUN: derived artifacts were not written and Foundry was not contacted.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in OUTPUT_NAMES:
        hashes[name] = write_jsonl_atomic(output_dir / f"{name}.jsonl", artifacts.records(name))
    manifest = {
        **result,
        "status": "SUCCEEDED",
        "finished_at": utcnow(),
        "output_dir": stored_path(output_dir),
        "output_sha256": hashes,
    }
    if args.publish:
        index_names = {
            "entities": args.entities_index,
            "relations": args.relations_index,
            "events": args.events_index,
        }
        manifest["azure_search"] = {
            "endpoint": args.endpoint,
            "indexes": index_names,
            "authentication": "api_key" if args.api_key else "default_azure_credential",
            "published": publish_intelligence(
                args.endpoint,
                args.api_key,
                index_names,
                artifacts,
                args.batch_size,
                args.recreate_indexes,
            ),
        }
    manifest_path = output_dir / "last_run.json"
    partial_manifest = manifest_path.with_name(manifest_path.name + ".partial")
    partial_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial_manifest, manifest_path)
    print(f"Wrote deterministic intelligence artifacts to {stored_path(output_dir)}")
    return 0


def main() -> int:
    args = parser().parse_args()
    if args.version:
        print(COMPONENT_VERSION)
        return 0
    try:
        return run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
