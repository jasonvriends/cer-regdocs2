# REGDOCS Atlas v1 data contract

This document defines the data the production UI needs. It is intentionally finite: if these inputs and indexes exist, the v1 UI has the data it was designed to use.

The authoritative record remains the Canada Energy Regulator (CER) REGDOCS source. Atlas keeps source document, page, and URL identity wherever REGDOCS provides it.

## 1. Durable Stage 4 package

The personal-computer workflow uploads exactly these normalized files to the configured Blob prefix, normally `workspace/4_normalize/`:

| File | Production use |
|---|---|
| `documents.jsonl` | Stage 6 document metadata and regulatory-intelligence extraction |
| `chunks.jsonl` | Stage 5 Search, Ask retrieval, HTML document viewer, Stage 6 evidence |
| `provenance.jsonl` | Stage 5 source/provenance fields |
| `pages.jsonl` | Durable normalized archive; not read directly by the v1 web runtime |
| `tables.jsonl` | Durable normalized archive; table text is represented in `chunks.jsonl` for the v1 runtime |

`tools/upload_cloud_inputs.py` validates and uploads all five files and writes `source-package.json` with hashes and sizes.

The production UI does **not** require a second copy of the PDF, Markdown, the local SQLite database, or the whole `workspace/` directory in Blob.

## 2. Stage 5 Search index

The configured corpus index is normally:

```text
regdocs-chunks-hybrid
```

It is the source for Search, grounded Ask retrieval, corpus coverage, evidence lookup, and the HTML document viewer.

### Fields required by the v1 UI

The core viewer/search contract is:

```text
chunk_id
document_id
chunk_index
chunk_type
title
heading
content
page_start
page_end
filing_date
company
project
filing_number
filing_id
source_url
```

The index also carries filter/facet and provenance fields such as application types, commodities, document types, file types, roles, hashes, analyzer/version values, table identifiers, figure identifiers, and element paths.

For hybrid retrieval the index also contains `content_vector` and is configured with the semantic configuration named by `AZURE_SEARCH_SEMANTIC_CONFIGURATION`.

### Document viewer contract

A document is reconstructed from all Search chunks with the same `document_id`, ordered by `chunk_index`.

The viewer uses:

- `chunk_index` for document order;
- `page_start` / `page_end` for page grouping and page jumps;
- `chunk_type` to render text, tables, and extracted figure text appropriately;
- `heading` and `content` for readable document structure;
- `chunk_id` to highlight the selected evidence passage;
- `source_url` for **Original in REGDOCS**.

The v1 viewer is an accessible HTML reconstruction of analyzed content. It is deliberately not an embedded PDF viewer. Layout can differ from the original; the source link remains available for authoritative comparison.

## 3. Microsoft Foundry

Foundry is used in two places:

1. **Grounded Ask synthesis** — Azure AI Search retrieves evidence first; Foundry synthesizes an answer from that evidence and returns citations.
2. **Stage 6 structured extraction** — Foundry extracts explicit events, claims, obligations, and relationships from normalized evidence.

The UI shows whether Foundry was used, the configured deployment, retrieval mode, evidence/citation counts, retries, timings, and live corpus coverage for successful Ask answers.

A Stage 6 model record is not accepted unless its evidence chunk IDs came from the exact input batch supplied to the extractor. Model-derived records remain marked `unreviewed` until a separate review process changes that state.

## 4. Stage 6 intelligence indexes

The cloud intelligence job publishes five indexes:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

### `regdocs-entities`

Needed by the relationship graph.

Core fields:

```text
id
entity_type
name
origin
schema_version
```

### `regdocs-relations`

Needed by the relationship graph and evidence navigation.

Core fields:

```text
id
source_id
target_id
relationship_type
document_id
filing_id
filing_number
company
project
evidence_chunk_ids
evidence_page_start
evidence_page_end
source_url
confidence
origin
review_status
```

### `regdocs-events`

Needed by the regulatory timeline.

Core fields:

```text
id
event_type
title
summary
date_start
date_end
date_precision
date_basis
document_id
filing_id
filing_number
company
project
chunk_id
page_start
page_end
source_url
confidence
origin
review_status
```

### `regdocs-claims`

Needed by **Findings & claims**.

Core fields:

```text
id
claim_type
statement
claimant
subject
evidence_chunk_ids
evidence_page_start
evidence_page_end
document_id
filing_id
filing_number
company
project
source_url
confidence
origin
review_status
extractor_version
```

### `regdocs-obligations`

Needed by **Commitments & obligations**.

Core fields:

```text
id
obligation_type
obligated_party
action
deadline
status
evidence_chunk_ids
evidence_page_start
evidence_page_end
document_id
filing_id
filing_number
company
project
source_url
confidence
origin
review_status
extractor_version
```

## 5. Feature-to-data matrix

| UI feature | Required runtime data/service |
|---|---|
| Ask a question | Stage 5 corpus index + Foundry chat deployment |
| Keyword search/retrieval | Stage 5 corpus index |
| Hybrid/semantic retrieval | Stage 5 vector field + semantic configuration + embedding deployment |
| Cited source cards | Stage 5 chunk metadata and page/source fields |
| HTML document viewer | Stage 5 chunks grouped by `document_id` and ordered by `chunk_index` |
| Page jump/highlighting | `page_start`, `page_end`, `chunk_id` |
| Original REGDOCS link | `source_url` |
| Shelf / Shelf-only Ask | Stage 5 chunk IDs held in browser state + Ask retrieval |
| Shelf CSV export | Evidence already saved in the browser shelf |
| Corpus coverage | Stage 5 index count + `filing_date` |
| Regulatory timeline | `regdocs-events` |
| Relationship graph | `regdocs-relations` + `regdocs-entities` |
| Findings & claims | `regdocs-claims` |
| Commitments & obligations | `regdocs-obligations` |
| User error lookup | Container App logs + Log Analytics + diagnostics operator token |
| Live diagnostics | Search + Foundry + document retrieval + five intelligence indexes |

## 6. What is intentionally not a v1 feature

The v1 UI does not claim to provide:

- reviewed Schedule A datasets;
- arbitrary schema/data-product generation;
- an embedded PDF copy of every source;
- saved/shared server-side workspaces;
- legal determination of whether an obligation is satisfied when the extracted record does not explicitly say so.

Those are possible future products, not hidden requirements for completing v1.

## 7. Deployment acceptance condition

The data layer is ready when:

1. `./ui/deploy/deploy.sh --check-data` reports the five-file Stage 4 package complete;
2. Stage 5 completes successfully;
3. Stage 6 completes successfully;
4. `./ui/deploy/deploy.sh --status` shows both jobs succeeded;
5. protected `/diagnostics` passes the corpus, retrieval, document-view, Foundry, and five intelligence-index checks.

There is no Stage 7. After Stage 6, the remaining step is production verification and normal use of the web application.
