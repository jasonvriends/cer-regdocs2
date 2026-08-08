# `regdocs_5_index.py`

Stage 5 of the REGDOCS processing pipeline: **publish deterministic Stage 4
chunks into an Azure AI Search index for full-text search, filters, facets, and
later retrieval-augmented generation (RAG)**.

```text
workspace/4_normalize/
  chunks.jsonl + provenance.jsonl
                 |
                 v
       validate one-to-one provenance
                 |
                 v
        map search documents
                 |
                 v
          Azure AI Search
          regdocs-chunks
                 |
          +------+------+
          |             |
     Search Explorer   future UI / RAG
```

Script version documented: **5.0.0**.

## Purpose and boundary

Stage 5 is a **derivative publication stage**. Stages 1–4 remain the auditable
source pipeline. Azure AI Search is a rebuildable retrieval index, not the
source of truth.

Stage 5.0 intentionally starts with:

- keyword/full-text search;
- metadata filters;
- facets;
- exact chunk/document/page identities;
- compact qualified provenance pointers; and
- deterministic push-based indexing.

It does **not** yet create embeddings, vector fields, semantic configurations,
or LLM answers. Those capabilities can be added after basic retrieval quality
is validated without changing the Stage 1–4 artifact contract.

## `.json`, `.jsonl`, and `.md`

These formats serve different purposes in the pipeline.

### Stage 3 raw `.json`

The Stage 3 raw JSON is the canonical Azure Content Understanding result. It
contains machine-readable structure such as:

- pages and original source page numbers;
- paragraphs;
- tables and cells;
- sections and element pointers;
- figures;
- spans and offsets;
- source-region polygons;
- generated Markdown;
- warnings and analyzer metadata; and
- REGDOCS large-PDF chunking metadata when applicable.

This is the authoritative analysis artifact because it preserves structure and
coordinates that Markdown cannot represent reliably.

### Stage 3 `.md`

The Markdown file is a human-readable derivative of the JSON. It is convenient
for inspection, reading, diffing, and debugging, but it is not the primary
machine-processing contract.

Stage 4 validates the Markdown against the Markdown embedded in the raw JSON.
If the separate `.md` file is missing, Stage 4 can reconstruct it from the JSON.

### Stage 4 `.jsonl`

JSON Lines (`.jsonl`) means **one complete JSON object per line**. Stage 4 uses
JSONL so large normalized corpora can be streamed record by record rather than
loaded as one giant JSON array.

For example, `chunks.jsonl` contains one searchable chunk per line and
`provenance.jsonl` contains one provenance record per chunk.

**Stage 5 indexes `chunks.jsonl` plus compact information derived from
`provenance.jsonl`. It does not index the Stage 3 Markdown file directly.**

## Inputs

Default input directory:

```text
workspace/4_normalize/
```

Required files:

```text
chunks.jsonl
provenance.jsonl
```

Stage 5 currently does not require `documents.jsonl`, `pages.jsonl`, or
`tables.jsonl` because the chunk records already inherit the document metadata
needed for the first search index.

Before Azure is contacted, Stage 5 validates:

- every selected chunk has an `id`;
- every selected chunk has exactly one matching provenance record;
- provenance `chunk_id` matches the chunk ID;
- provenance `document_id` matches the chunk document ID; and
- provenance `content_index` matches the chunk content index.

For an unfiltered full-corpus run, orphan provenance records also cause failure.

## Search document identity

Normalized chunk IDs look like:

```text
4647200:chunk:1742
```

Azure AI Search document keys permit a restricted character set, so the colon
form is not used as the Azure key. Stage 5 creates:

```text
key = sha256(chunk_id)
```

The original chunk ID remains stored as `chunk_id` and is the application-level
identity used to reconnect a search hit to Stage 4 provenance.

This gives two identities with separate purposes:

```text
Azure key      safe transport/index key
chunk_id       stable REGDOCS normalized evidence identity
```

## Index schema

The default index name is:

```text
regdocs-chunks
```

Primary searchable text fields:

```text
title
content
heading
section_path
submitter
company
project
application_types
commodities
document_types
file_types
roles
```

Important filterable/facetable fields include:

```text
document_id
chunk_type
company
project
submitter
application_types
commodities
document_types
file_types
roles
```

Source/provenance fields include:

```text
chunk_id
document_id
page_start
page_end
content_index
source_url
resolved_url
file_path
file_sha256
element_paths
local_element_paths
```

Table and figure chunks also retain their table/figure identifiers and table
row ranges where present.

`element_paths` contains the globally qualified Stage 4.1+ provenance pointers,
for example:

```text
/contents/2/paragraphs/12
```

`local_element_paths` preserves the corresponding Azure-local value:

```text
/paragraphs/12
```

Detailed polygons and complete evidence objects remain in
`workspace/4_normalize/provenance.jsonl`. A future application can resolve them
by `chunk_id` instead of inflating every Azure Search document with full
geometry.

## Installation

Install the shared dependencies:

```bash
python -m pip install -r pipeline/requirements.txt
```

Stage 5 uses the stable Azure AI Search Python package:

```text
azure-search-documents==12.0.0
```

## Azure Search service

You need an Azure AI Search service before a non-dry Stage 5 run.

The script expects an endpoint in this form:

```text
https://<service-name>.search.windows.net
```

Set it with:

```bash
export AZURE_SEARCH_ENDPOINT="https://<service-name>.search.windows.net"
```

The index name defaults to `regdocs-chunks`. Override it with:

```bash
export AZURE_SEARCH_INDEX_NAME="regdocs-chunks"
```

or `--index-name`.

## Authentication

Preferred authentication is `DefaultAzureCredential`:

```bash
az login
export AZURE_SEARCH_ENDPOINT="https://<service-name>.search.windows.net"
```

The identity needs sufficient Azure AI Search permissions to create/manage the
index and upload documents. For a production deployment, use least-privilege
role assignments appropriate to index management and Search Index Data
Contributor operations.

An admin key can be supplied through the environment for initial setup:

```bash
export AZURE_SEARCH_ADMIN_KEY="<key>"
```

Do not put keys directly in shell commands or committed files.

## Local dry run

After the final Stage 4 normalize, validate the full normalized corpus without
contacting Azure:

```bash
python pipeline/regdocs_5_index.py --dry-run
```

The command reports:

- mapped chunk count;
- source REGDOCS document count;
- chunk-type counts;
- approximate mapped payload size;
- `chunks.jsonl` SHA-256; and
- `provenance.jsonl` SHA-256.

This is the safest first command after a large normalization.

## Pilot index

Do not use a destructive full-index rebuild for a pilot. Give the pilot its own
index name:

```bash
python pipeline/regdocs_5_index.py \
  --document-id 4647200 \
  --index-name regdocs-chunks-pilot
```

Or limit by chunk count:

```bash
python pipeline/regdocs_5_index.py \
  --limit 1000 \
  --index-name regdocs-chunks-pilot
```

If the named index does not exist, Stage 5 creates it automatically. If it
exists and has the required fields, Stage 5 performs `merge_or_upload` updates.

## Full initial publication

For the first full load:

```bash
python pipeline/regdocs_5_index.py
```

If `regdocs-chunks` does not exist, the script creates it and uploads the whole
normalized corpus.

Azure AI Search push requests are limited to 1,000 documents or 16 MB per
request. Stage 5 defaults to at most 500 records and approximately 12 MiB per
batch to leave headroom.

Override the batch controls only when testing throughput:

```bash
python pipeline/regdocs_5_index.py \
  --batch-size 750 \
  --max-batch-bytes 12582912
```

Per-document Azure indexing failures fail the run rather than being silently
ignored.

## Rebuild semantics and stale documents

`merge_or_upload` updates and inserts current keys, but it does not know which
old chunk keys disappeared from a later normalized snapshot. Therefore an
incremental upload can leave stale search documents if normalization changed
chunk identities or removed chunks.

For a known full snapshot rebuild, explicitly recreate the index:

```bash
python pipeline/regdocs_5_index.py --recreate-index
```

This deletes the named Azure Search index, creates it again with the Stage 5
schema, and uploads the selected full normalized corpus.

For safety, `--recreate-index` is rejected when combined with `--document-id`
or `--limit`. Use a separate pilot index name for subset testing.

Azure Search is intentionally treated as a rebuildable derivative, so deleting
and rebuilding this index does not delete REGDOCS source evidence or Stage 1–4
artifacts.

A future production publication design should use versioned index generations
and an alias/pointer switch rather than destructive in-place rebuilds.

## Local Stage 5 run metadata

A non-query Azure upload writes:

```text
workspace/5_index/last_run.json
```

It records:

- Stage 5 version;
- endpoint and index name;
- authentication mode, but never the key itself;
- selected scope;
- Stage 4 input hashes;
- counts and approximate payload size;
- upload batch count; and
- final status/timestamps.

This file is operational metadata, not the source corpus.

## Smoke-test queries

After indexing, run a keyword query from the command line:

```bash
python pipeline/regdocs_5_index.py \
  --query "caribou habitat"
```

The script prints the matching chunk ID, REGDOCS document ID, page range,
title, heading, and a short text preview.

Filters use Azure AI Search OData syntax. For example:

```bash
python pipeline/regdocs_5_index.py \
  --query "acid rock drainage" \
  --filter "document_id eq '4647200'"
```

For collection fields, Azure's `any(...)` syntax can be used. Example:

```bash
python pipeline/regdocs_5_index.py \
  --query "caribou" \
  --filter "document_types/any(x: x eq 'Environmental Report')"
```

The Azure portal's **Search Explorer** is also useful for inspecting the same
index interactively during development.

## What Stage 5.0 does not yet do

Stage 5.0 deliberately does not yet implement:

- vector embeddings;
- vector fields or HNSW configuration;
- hybrid keyword + vector queries;
- semantic ranker configuration;
- synonym maps;
- index aliases/versioned generations;
- incremental deletion detection;
- a search web UI;
- LLM/RAG answer generation; or
- a SQLite Stage 5 ledger table.

These are next-layer capabilities rather than reasons to weaken the Stage 1–4
provenance contract.

## Recommended progression

After the first full index exists:

1. validate keyword search and filters in Search Explorer and Stage 5 `--query`;
2. create 20–50 representative regulatory research queries with known relevant
   documents/pages;
3. measure the keyword baseline;
4. add Azure semantic ranking and measure whether it improves results;
5. add embeddings and hybrid vector + keyword retrieval;
6. preserve metadata filters in hybrid retrieval;
7. only then connect retrieved chunks to an LLM;
8. require generated claims to cite `chunk_id`, document ID, page range, and
   source URL; and
9. resolve `chunk_id` back to detailed Stage 4 provenance when displaying
   evidence.

This sequence keeps search quality measurable and prevents an LLM from hiding
retrieval problems.

## Why this supports future LLM/RAG work

A future RAG request can retrieve a small set of chunks from Azure AI Search,
then provide those chunks and their source metadata to an LLM. The model does
not need to ingest a 986-page PDF or infer where evidence came from.

A retrieval result already contains enough identity to support a chain like:

```text
user question
   -> Azure AI Search hit
   -> chunk_id
   -> REGDOCS document_id
   -> page_start/page_end
   -> source_url
   -> Stage 4 provenance
   -> /contents/N/paragraphs/N
   -> page polygon
```

That is the core architecture needed for grounded regulatory search and cited
LLM answers.

## CLI reference

`--help` is authoritative for the installed script.

| Option | Effect |
|---|---|
| `--normalized-dir PATH` | Stage 4 JSONL input directory |
| `--output-dir PATH` | Local Stage 5 run-metadata directory |
| `--endpoint URL` | Azure AI Search endpoint |
| `--api-key VALUE` | Admin key override; environment is preferred |
| `--index-name NAME` | Target search index |
| `--document-id ID` | Upload chunks for one document; repeatable |
| `--limit N` | Upload at most N chunks |
| `--batch-size N` | Maximum records per push batch, up to 1,000 |
| `--max-batch-bytes N` | Approximate JSON bytes allowed per batch |
| `--recreate-index` | Delete/rebuild a full target index before upload |
| `--dry-run` | Validate Stage 4 inputs with no Azure calls |
| `--query TEXT` | Run a keyword query instead of uploading |
| `--filter ODATA` | Optional Azure Search filter for `--query` |
| `--top N` | Number of query results shown |
| `--version` | Print Stage 5 version |

Previous: [Stage 4 normalizer](regdocs_4_normalize.md).
