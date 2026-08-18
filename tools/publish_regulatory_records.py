#!/usr/bin/env python3
"""Publish Stage 6 claims and obligations to dedicated Azure AI Search indexes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator, Sequence

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
)

DEFAULT_CLAIMS_INDEX = "regdocs-claims"
DEFAULT_OBLIGATIONS_INDEX = "regdocs-obligations"


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Expected {path}")
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            if not str(value.get("id") or "").strip():
                raise ValueError(f"{path}:{line_number}: id is required")
            yield value


def search_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def common_fields() -> list[Any]:
    string = SearchFieldDataType.STRING
    strings = "Collection(Edm.String)"
    return [
        SimpleField(name="key", type=string, key=True),
        SimpleField(name="id", type=string, filterable=True),
        SearchField(name="evidence_chunk_ids", type=strings, filterable=True, retrievable=True),
        SimpleField(name="evidence_page_start", type=SearchFieldDataType.INT32, filterable=True),
        SimpleField(name="evidence_page_end", type=SearchFieldDataType.INT32, filterable=True),
        SimpleField(name="document_id", type=string, filterable=True),
        SimpleField(name="filing_id", type=string, filterable=True),
        SimpleField(name="filing_number", type=string, filterable=True),
        SearchableField(name="company", type=string, filterable=True, facetable=True),
        SearchableField(name="project", type=string, filterable=True, facetable=True),
        SimpleField(name="source_url", type=string),
        SimpleField(name="confidence", type=SearchFieldDataType.DOUBLE, filterable=True),
        SimpleField(name="origin", type=string, filterable=True, facetable=True),
        SimpleField(name="schema_version", type=string, filterable=True),
        SimpleField(name="extractor_version", type=string, filterable=True),
        SimpleField(name="review_status", type=string, filterable=True, facetable=True),
    ]


def claims_index(name: str) -> SearchIndex:
    string = SearchFieldDataType.STRING
    fields = common_fields() + [
        SimpleField(name="claim_type", type=string, filterable=True, facetable=True),
        SearchableField(name="statement", type=string),
        SearchableField(name="claimant", type=string, filterable=True, facetable=True),
        SearchableField(name="subject", type=string, filterable=True, facetable=True),
    ]
    return SearchIndex(name=name, fields=fields)


def obligations_index(name: str) -> SearchIndex:
    string = SearchFieldDataType.STRING
    fields = common_fields() + [
        SimpleField(name="obligation_type", type=string, filterable=True, facetable=True),
        SearchableField(name="obligated_party", type=string, filterable=True, facetable=True),
        SearchableField(name="action", type=string),
        SimpleField(name="deadline", type=string, filterable=True, sortable=True),
        SearchableField(name="status", type=string, filterable=True, facetable=True),
    ]
    return SearchIndex(name=name, fields=fields)


def ensure_index(client: SearchIndexClient, expected: SearchIndex, recreate: bool) -> None:
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
        print(f"Created {expected.name}", flush=True)
        return
    missing = sorted({field.name for field in expected.fields} - {field.name for field in current.fields})
    if missing:
        raise RuntimeError(
            f"Existing index {expected.name!r} is missing fields: {', '.join(missing)}. "
            "Use --recreate-indexes after confirming the target names."
        )
    print(f"Using existing {expected.name}", flush=True)


def batches(records: Sequence[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(records), size):
        yield list(records[start : start + size])


def upload(client: SearchClient, records: Sequence[dict[str, Any]], batch_size: int) -> int:
    uploaded = 0
    for batch_number, batch in enumerate(batches(records, batch_size), 1):
        documents = [{"key": search_key(str(item["id"])), **item} for item in batch]
        results = client.merge_or_upload_documents(documents=documents)
        failures = [result for result in results if not bool(getattr(result, "succeeded", False))]
        if failures:
            detail = "; ".join(
                f"{getattr(result, 'key', '?')}: {getattr(result, 'error_message', '')}" for result in failures[:10]
            )
            raise RuntimeError(f"Azure rejected {len(failures)} record(s): {detail}")
        uploaded += len(results)
        print(f"Uploaded batch {batch_number}: {uploaded}/{len(records)}", flush=True)
    return uploaded


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--input-dir", default="workspace/6_enrich")
    value.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    value.add_argument("--claims-index", default=os.getenv("AZURE_SEARCH_CLAIMS_INDEX", DEFAULT_CLAIMS_INDEX))
    value.add_argument(
        "--obligations-index",
        default=os.getenv("AZURE_SEARCH_OBLIGATIONS_INDEX", DEFAULT_OBLIGATIONS_INDEX),
    )
    value.add_argument("--batch-size", type=int, default=500)
    value.add_argument("--recreate-indexes", action="store_true")
    return value


def run(args: argparse.Namespace) -> int:
    if not args.endpoint:
        raise ValueError("Pass --endpoint or set AZURE_SEARCH_ENDPOINT")
    if not 1 <= args.batch_size <= 1000:
        raise ValueError("--batch-size must be 1..1000")

    input_dir = Path(args.input_dir)
    claims = list(iter_jsonl(input_dir / "claims.jsonl"))
    obligations = list(iter_jsonl(input_dir / "obligations.jsonl"))
    credential = DefaultAzureCredential()
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=credential)
    search_clients: list[SearchClient] = []
    try:
        definitions = {
            "claims": claims_index(args.claims_index),
            "obligations": obligations_index(args.obligations_index),
        }
        for definition in definitions.values():
            ensure_index(index_client, definition, args.recreate_indexes)

        published: dict[str, int] = {}
        for kind, records, index_name in (
            ("claims", claims, args.claims_index),
            ("obligations", obligations, args.obligations_index),
        ):
            client = SearchClient(endpoint=args.endpoint, index_name=index_name, credential=credential)
            search_clients.append(client)
            published[kind] = upload(client, records, args.batch_size)
            print(f"Published {published[kind]} {kind} to {index_name}", flush=True)
        print(json.dumps({"published": published}, sort_keys=True), flush=True)
        return 0
    finally:
        for client in search_clients:
            client.close()
        index_client.close()
        credential.close()


def main() -> int:
    try:
        return run(parser().parse_args())
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
