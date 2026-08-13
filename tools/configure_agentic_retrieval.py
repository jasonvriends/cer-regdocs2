#!/usr/bin/env python3
"""Configure and smoke-test GA Azure AI Search agentic retrieval objects.

This wraps the existing hybrid index in a search-index knowledge source and a
knowledge base. It uses the stable 2026-04-01 API and extracted retrieval; the
application can continue using its controlled Foundry synthesis layer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional, Sequence
from urllib.parse import quote

import httpx

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from regdocs_atlas.stages.index import credential

API_VERSION = "2026-04-01"


class SearchManagementClient:
    def __init__(self, endpoint: str, api_key: Optional[str]) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.identity = None if api_key else credential(None)
        self.client = httpx.Client(timeout=60.0)

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json;odata.metadata=minimal"}
        if self.api_key:
            headers["api-key"] = self.api_key
        else:
            token = self.identity.get_token("https://search.azure.com/.default")
            headers["Authorization"] = f"Bearer {token.token}"
        return headers

    def put(self, collection: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = quote(name, safe="")
        url = f"{self.endpoint}/{collection}('{encoded}')?api-version={API_VERSION}"
        response = self.client.put(url, headers={**self.headers(), "Prefer": "return=representation"}, json=payload)
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"{collection} update failed ({response.status_code}): {response.text[:1500]}")
        return response.json()

    def agentic_retrieve(self, knowledge_base: str, source: str, query: str) -> dict[str, Any]:
        """Exercise preview-only message/query planning without making it an app dependency."""
        encoded = quote(knowledge_base, safe="")
        url = f"{self.endpoint}/knowledgebases/{encoded}/retrieve?api-version=2026-05-01-preview"
        payload = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{
                        "type": "text",
                        "text": "Retrieve only public CER REGDOCS evidence. Preserve document and page references. If evidence is missing, say so.",
                    }],
                },
                {"role": "user", "content": [{"type": "text", "text": query}]},
            ],
            "retrievalReasoningEffort": {"kind": "low"},
            "outputMode": "extractedData",
            "includeActivity": True,
            "maxRuntimeInSeconds": 30,
            "maxOutputDocuments": 12,
            "maxOutputSize": 12000,
            "knowledgeSourceParams": [{
                "knowledgeSourceName": source,
                "kind": "searchIndex",
                "includeReferences": True,
                "includeReferenceSourceData": True,
                "failOnError": True,
                "alwaysQuerySource": True,
            }],
        }
        response = self.client.post(url, headers=self.headers(), json=payload)
        if response.status_code not in {200, 206}:
            raise RuntimeError(f"Agentic preview retrieval failed ({response.status_code}): {response.text[:1500]}")
        return response.json()

    def close(self) -> None:
        self.client.close()
        close = getattr(self.identity, "close", None)
        if callable(close):
            close()


def definitions(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    source = {
        "name": args.knowledge_source,
        "kind": "searchIndex",
        "description": "Public CER REGDOCS passages with document, filing, page, and source provenance.",
        "searchIndexParameters": {
            "searchIndexName": args.index_name,
            "semanticConfigurationName": args.semantic_configuration,
            "searchFields": [{"name": field} for field in ("content", "heading", "title")],
            "sourceDataFields": [
                {"name": field}
                for field in (
                    "chunk_id", "document_id", "filing_id", "filing_number", "page_start", "page_end",
                    "title", "heading", "content", "source_url", "company", "project", "filing_date",
                )
            ],
        },
    }
    parameters: dict[str, Any] = {
        "resourceUri": args.model_endpoint.rstrip("/"),
        "deploymentId": args.model_deployment,
        "modelName": args.model_name,
    }
    if args.model_api_key:
        parameters["apiKey"] = args.model_api_key
    knowledge_base = {
        "name": args.knowledge_base,
        "description": "Evidence retrieval across public CER REGDOCS records. Return source data for page-level citation.",
        "knowledgeSources": [{"name": args.knowledge_source}],
        "models": [{"kind": "azureOpenAI", "azureOpenAIParameters": parameters}],
    }
    return source, knowledge_base


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(payload))
    for model in value.get("models", []):
        parameters = model.get("azureOpenAIParameters", {})
        if "apiKey" in parameters:
            parameters["apiKey"] = "<redacted>"
    return value


def retrieve(args: argparse.Namespace, query: str) -> dict[str, Any]:
    from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
    from azure.search.documents.knowledgebases.models import (
        KnowledgeBaseRetrievalRequest,
        KnowledgeRetrievalSemanticIntent,
        SearchIndexKnowledgeSourceParams,
    )

    cred = credential(args.search_api_key)
    client = KnowledgeBaseRetrievalClient(
        endpoint=args.search_endpoint,
        knowledge_base_name=args.knowledge_base,
        credential=cred,
    )
    try:
        request = KnowledgeBaseRetrievalRequest(
            intents=[KnowledgeRetrievalSemanticIntent(search=query)],
            include_activity=True,
            max_runtime_in_seconds=30,
            max_output_size_in_tokens=6000,
            knowledge_source_params=[
                SearchIndexKnowledgeSourceParams(
                    knowledge_source_name=args.knowledge_source,
                    include_references=True,
                    include_reference_source_data=True,
                )
            ],
        )
        response = client.retrieve(request)
        return response.as_dict()
    finally:
        client.close()
        close = getattr(cred, "close", None)
        if callable(close):
            close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Configure Azure AI Search GA agentic retrieval")
    p.add_argument("--search-endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    p.add_argument("--search-api-key", default=os.getenv("AZURE_SEARCH_ADMIN_KEY"))
    p.add_argument("--index-name", default=os.getenv("AZURE_SEARCH_HYBRID_INDEX", "regdocs-chunks-hybrid"))
    p.add_argument("--semantic-configuration", default=os.getenv("AZURE_SEARCH_SEMANTIC_CONFIGURATION", "regdocs-semantic"))
    p.add_argument("--knowledge-source", default=os.getenv("AZURE_SEARCH_KNOWLEDGE_SOURCE", "regdocs-hybrid-ks"))
    p.add_argument("--knowledge-base", default=os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE", "regdocs-agentic-kb"))
    p.add_argument("--model-endpoint", default=os.getenv("FOUNDRY_RESOURCE_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT"))
    p.add_argument("--model-deployment", default=os.getenv("FOUNDRY_MODEL_DEPLOYMENT"))
    p.add_argument("--model-name", default=os.getenv("FOUNDRY_MODEL_NAME"))
    p.add_argument("--model-api-key", default=os.getenv("FOUNDRY_MODEL_API_KEY"))
    p.add_argument("--retrieve", metavar="QUERY")
    p.add_argument("--agentic-query", metavar="QUERY")
    p.add_argument("--retrieve-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def validate(args: argparse.Namespace) -> None:
    required = [
        ("AZURE_SEARCH_ENDPOINT/--search-endpoint", args.search_endpoint),
        ("FOUNDRY_RESOURCE_ENDPOINT/--model-endpoint", args.model_endpoint),
        ("FOUNDRY_MODEL_DEPLOYMENT/--model-deployment", args.model_deployment),
        ("FOUNDRY_MODEL_NAME/--model-name", args.model_name),
    ]
    missing = [name for name, value in required if not value]
    if missing:
        raise ValueError("Missing " + ", ".join(missing))
    if args.retrieve_only and not (args.retrieve or args.agentic_query):
        raise ValueError("--retrieve-only requires --retrieve or --agentic-query")


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        validate(args)
        source, knowledge_base = definitions(args)
        if args.dry_run:
            print(json.dumps({"knowledge_source": source, "knowledge_base": redact(knowledge_base)}, indent=2))
            print("DRY RUN: Azure was not contacted.")
            return 0
        if not args.retrieve_only:
            client = SearchManagementClient(args.search_endpoint, args.search_api_key)
            try:
                created_source = client.put("knowledgesources", args.knowledge_source, source)
                print(f"Configured knowledge source {created_source.get('name')!r}.")
                created_base = client.put("knowledgebases", args.knowledge_base, knowledge_base)
                print(f"Configured knowledge base {created_base.get('name')!r}.")
            finally:
                client.close()
        if args.retrieve:
            result = retrieve(args, args.retrieve)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if args.agentic_query:
            print("WARNING: --agentic-query uses the 2026-05-01-preview message/query-planning API.", file=sys.stderr)
            client = SearchManagementClient(args.search_endpoint, args.search_api_key)
            try:
                result = client.agentic_retrieve(args.knowledge_base, args.knowledge_source, args.agentic_query)
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            finally:
                client.close()
        return 0
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
