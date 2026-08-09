# REGDOCS Processing Pipeline

An auditable five-stage pipeline for collecting public regulatory records from
the Canada Energy Regulator (CER) REGDOCS registry, turning them into a
normalized provenance-preserving corpus, and publishing searchable chunks to
Azure AI Search.

## Pipeline

```text
Canada Energy Regulator REGDOCS
              |
              v
1. Scout / catalogue
   metadata + raw HTML evidence
              |
              v
2. Download / verify
   source files + hashes + versions
              |
              v
3. Analyze
   Azure Content Understanding or local Docling
   provider-native artifacts + durable per-document workers
              |
              v
4. Normalize
   documents + pages + chunks + tables + provenance
              |
              v
5. Index
   Azure AI Search chunk index
   full-text + filters/facets, later semantic/vector/RAG
```

Stages 1–4 share one SQLite pipeline ledger. Stage 5 treats Azure AI Search as a
rebuildable derivative retrieval index rather than a new source of truth.
Source evidence, downloaded files, analysis artifacts, normalized projections,
and search publication remain separate so each layer can be audited and rebuilt
independently.

## Repository layout

```text
.
├── README.md
├── ROADMAP.md
├── pipeline/
│   ├── requirements.txt
│   ├── regdocs_paths.py
│   ├── regdocs_1_scout.py
│   ├── regdocs_1_scout.md
│   ├── regdocs_2_download.py
│   ├── regdocs_2_download.md
│   ├── regdocs_3_azure.py
│   ├── regdocs_3_azure_worker.py
│   ├── regdocs_3_azure.md
│   ├── regdocs_3_docling.py
│   ├── regdocs_3_docling_worker.py
│   ├── regdocs_3_docling.md
│   ├── regdocs_4_normalize.py
│   ├── regdocs_4_normalize.md
│   ├── regdocs_5_index.py
│   └── regdocs_5_index.md
├── database/
│   ├── regdocs.db
│   ├── locks/
│   └── backups/
└── workspace/
    ├── 1_scout/
    │   ├── raw/regdocs/
    │   └── run/
    ├── 2_download/
    │   ├── files/
    │   │   ├── .partial/
    │   │   └── _versions/
    │   └── run/
    ├── 3_analyze/
    │   ├── content-understanding/
    │   │   ├── raw/
    │   │   └── markdown/
    │   └── docling/
    ├── 4_normalize/
    │   ├── documents.jsonl
    │   ├── pages.jsonl
    │   ├── chunks.jsonl
    │   ├── tables.jsonl
    │   └── provenance.jsonl
    └── 5_index/
        └── last_run.json
```

`pipeline/` is version-controlled implementation and documentation.
`database/` and `workspace/` are persistent local operational state and are
ignored by Git. They are not disposable caches.

Each stage script keeps only a short purpose, invocation syntax, and pointer in
its module header. Operational policy, examples, recovery behavior, and known
limitations live in the adjacent same-named Markdown file.

SQLite may create `regdocs.db-wal`, `regdocs.db-shm`, or
`regdocs.db-journal` beside the main database while it is in use.

## Stages

### 1 — Scout

`pipeline/regdocs_1_scout.py` searches REGDOCS, expands explicit Folder and
Compound Document membership, collects facets and detail metadata, and
preserves successful source HTML responses by SHA-256.

Primary outputs:

```text
database/regdocs.db
workspace/1_scout/raw/regdocs/
workspace/1_scout/run/
```

Detailed documentation: [Stage 1 — Scout](pipeline/regdocs_1_scout.md).

### 2 — Download

`pipeline/regdocs_2_download.py` selects eligible files from the ledger,
downloads and validates their bytes, records hashes and detected types,
reconciles existing files, and archives replaced versions.

Primary outputs:

```text
database/regdocs.db
workspace/2_download/files/
workspace/2_download/run/
```

Detailed documentation: [Stage 2 — Download](pipeline/regdocs_2_download.md).

### 3 — Analyze

Stage 3 has provider-specific analyzers.

`pipeline/regdocs_3_azure.py` is the durable Azure AI Content Understanding
supervisor. It launches exactly one `regdocs_3_azure_worker.py` child per
document, preserves raw JSON and Markdown, and records analysis status and
provenance in SQLite. If a worker segfaults or is OOM-killed, the supervisor can
retry that document in a fresh process and quarantine repeated crash cases
without terminating the remaining queue.

PDFs over 300 pages are counted locally and analyzed automatically in inclusive
`Content-Range` requests of at most 300 pages. The original PDF is not
physically split. Each successful range is saved independently so an interrupted
large document can normally resume without rebilling completed ranges.

`pipeline/regdocs_3_docling.py` is the local Docling supervisor and similarly
isolates each document in `regdocs_3_docling_worker.py`.

Primary outputs:

```text
database/regdocs.db
workspace/3_analyze/content-understanding/
workspace/3_analyze/docling/
```

Large Azure PDFs additionally create per-range raw JSON and metadata below the
canonical raw JSON's `<sha256>.parts/` directory.

Detailed documentation:

- [Stage 3 — Azure](pipeline/regdocs_3_azure.md)
- [Stage 3 — Docling](pipeline/regdocs_3_docling.md)

### 4 — Normalize

`pipeline/regdocs_4_normalize.py` locally converts successful layout analyses
into deterministic JSONL projections for documents, pages, chunks, tables, and
chunk-to-source provenance. It makes no network calls. Stage 4 processes each
Azure `contents[]` entry independently, including the multiple entries produced
when Stage 3 recombines a ranged large-PDF analysis.

Stage 4.1.0 qualifies every paragraph/table/figure provenance pointer with its
`content_index`, for example `/contents/2/paragraphs/12`, while retaining the
original Azure-local pointer as `local_element`. This makes provenance
unambiguous across large PDFs without losing original page numbers or polygon
geometry.

Primary outputs:

```text
database/regdocs.db
workspace/4_normalize/
```

Detailed documentation: [Stage 4 — Normalize](pipeline/regdocs_4_normalize.md).

### 5 — Index

`pipeline/regdocs_5_index.py` validates `chunks.jsonl` against
`provenance.jsonl`, maps each chunk into an Azure AI Search document, creates the
search index when needed, and pushes documents in bounded batches.

Stage 5.0 starts with full-text search plus metadata filters and facets. It keeps
stable REGDOCS `chunk_id`, document/page metadata, source URLs, source hashes,
and compact globally qualified element paths in the search index. Detailed
polygons remain in Stage 4 provenance and can be resolved by `chunk_id`.

Primary outputs:

```text
Azure AI Search index: regdocs-chunks
workspace/5_index/last_run.json
```

Azure AI Search is a derivative index. Rebuilding it does not replace or delete
Stages 1–4 source evidence.

Detailed documentation: [Stage 5 — Index](pipeline/regdocs_5_index.md).

## JSON, JSONL, and Markdown

Stage 3 Azure raw `.json` is the canonical machine-readable Azure analysis
result. It contains pages, paragraphs, tables, sections, spans, source
regions/polygons, Markdown, warnings, and analyzer metadata.

Stage 3 Azure `.md` is a human-readable derivative of that JSON. It is
convenient for reading and inspection but does not preserve the full
structural/coordinate contract.

Stage 4 `.jsonl` files contain one JSON record per line. `chunks.jsonl` is the
primary Stage 5 search input, joined with compact identities from
`provenance.jsonl`. Stage 5 does not directly index the Stage 3 Markdown file.

## Ledger

SQLite is the Stage 1–4 processing ledger, not the final search database. The
acquisition stages own five base tables:

| Table | Purpose |
|---|---|
| `documents` | REGDOCS identity, metadata, relationships, and stage status |
| `runs` | Run parameters, progress, heartbeat, counters, and summaries |
| `errors` | Structured stage warnings and failures |
| `raw_snapshots` | Hashes and paths for preserved REGDOCS HTML |
| `files` | Downloaded versions, hashes, types, and current-file state |

Downstream processing stages add:

| Table | Purpose |
|---|---|
| `analyses` | Stage 3 analyzer identity, artifact paths, status, and counts |
| `normalizations` | Stage 4 version/config identity, status, hashes, and counts |

Stage 5 currently writes a local `last_run.json` publication manifest rather
than adding a SQLite table. Azure Search remains rebuildable from Stage 4.

## Quick start

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Python 3.10 or newer is required. Install the shared dependencies for all five
stages:

```bash
python -m pip install -r pipeline/requirements.txt
```

The requirements file includes Scout's parser/progress dependencies, Azure
Content Understanding and identity libraries for Stage 3, `pypdf` for Stage 3
PDF page counting, and `azure-search-documents` for Stage 5.

### Run Stage 1

```bash
python pipeline/regdocs_1_scout.py
```

Useful read-only commands:

```bash
python pipeline/regdocs_1_scout.py --status
python pipeline/regdocs_1_scout.py --status-json
python pipeline/regdocs_1_scout.py --check-schema
python pipeline/regdocs_1_scout.py --audit
```

### Run Stage 2

Preview selection before downloading:

```bash
python pipeline/regdocs_2_download.py --dry-run --limit 25
```

Download eligible files:

```bash
python pipeline/regdocs_2_download.py
```

### Run Stage 3 — Azure

Configure Azure through the shell environment. Do not place an API key in a
command line, script, or documentation.

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"  # omit when using DefaultAzureCredential
python pipeline/regdocs_3_azure.py --dry-run --limit 10
python pipeline/regdocs_3_azure.py --limit 10
```

No special command is required for a large PDF. For example:

```bash
python pipeline/regdocs_3_azure.py --document-id 4647200
```

A 986-page PDF is automatically submitted as `1-300`, `301-600`, `601-900`,
and `901-986`, then recombined into the normal canonical Stage 3 JSON and
Markdown artifacts.

Stage 3 Azure can incur charges and requires one explicit selection scope. Keep
initial runs bounded. After reviewing a complete no-Azure-call preview, run all
remaining eligible files with:

```bash
python pipeline/regdocs_3_azure.py --all --dry-run
python pipeline/regdocs_3_azure.py --all
```

If a document repeatedly crashes the worker, it is quarantined so the queue can
continue. Retry quarantined documents explicitly with:

```bash
python pipeline/regdocs_3_azure.py --limit 1000 --retry-quarantined
```

See the Azure Stage 3 runbook before a full run, and do not use `--force` unless
you intend to resubmit matching successful analyses. For a large PDF, `--force`
also bypasses saved range-part recovery and resubmits every range.

### Run Stage 3 — Docling

The local Docling provider uses the same one-document-per-child durability
pattern:

```bash
python pipeline/regdocs_3_docling.py --max-documents 10
```

See [Stage 3 — Docling](pipeline/regdocs_3_docling.md) for its installation and
runtime requirements.

### Run Stage 4

Normalize one document as a pilot:

```bash
python pipeline/regdocs_4_normalize.py \
  --document-id 4647200 \
  --output-dir workspace/4_normalize/pilot
```

Normalize all current successful analyses:

```bash
python pipeline/regdocs_4_normalize.py
```

Stage 4 replaces all five JSONL files in its selected output directory. Always
send a filtered `--document-id` or `--limit` run to a separate pilot directory.

Inspect the latest normalization run:

```bash
python pipeline/regdocs_4_normalize.py --status
```

### Run Stage 5

Validate the final normalized corpus without contacting Azure Search:

```bash
python pipeline/regdocs_5_index.py --dry-run
```

Configure Azure AI Search:

```bash
export AZURE_SEARCH_ENDPOINT="https://<service-name>.search.windows.net"
az login
```

For initial key-based setup, `AZURE_SEARCH_ADMIN_KEY` is also supported through
the environment. Do not put the key in a command line.

Create/update the full default index:

```bash
python pipeline/regdocs_5_index.py
```

Use a different index name for a pilot:

```bash
python pipeline/regdocs_5_index.py \
  --document-id 4647200 \
  --index-name regdocs-chunks-pilot
```

Run a keyword smoke test:

```bash
python pipeline/regdocs_5_index.py --query "caribou habitat"
```

Run a query with a filter:

```bash
python pipeline/regdocs_5_index.py \
  --query "acid rock drainage" \
  --filter "document_id eq '4647200'"
```

For a known full rebuild that must remove stale chunk keys:

```bash
python pipeline/regdocs_5_index.py --recreate-index
```

`--recreate-index` cannot be combined with `--document-id` or `--limit`; use a
separate pilot index for subsets.

## Verification

Stages 1 and 2 include offline self-tests:

```bash
python pipeline/regdocs_1_scout.py --self-test
python pipeline/regdocs_2_download.py --self-test
```

Useful operational checks include:

```bash
python pipeline/regdocs_1_scout.py --audit
python pipeline/regdocs_2_download.py --status-json
python pipeline/regdocs_4_normalize.py --status
python pipeline/regdocs_5_index.py --dry-run
```

For a large-PDF Azure Stage 3 run, verify the reported combined page count
matches the source PDF page count before proceeding to Stage 4. The Azure
worker enforces this invariant before publishing a ranged canonical artifact.

For a Stage 4 ranged-PDF pilot, inspect a provenance record with
`content_index > 0` and verify its evidence pointer begins with the same
`/contents/<content_index>/` value. The Stage 4 runbook includes a ready-to-run
verification snippet.

After Stage 5 publication, use the CLI `--query` mode or Azure AI Search Search
Explorer to confirm that results retain `chunk_id`, original document ID, page
range, and source URL.

## Operational principles

- Preserve `workspace/1_scout/raw/`; it is source evidence, not cache.
- Treat source-file SHA-256 as the definitive downloaded version identity.
- Keep acquisition, analysis, normalization, indexing, and later AI layers
  independently reproducible.
- Prefer resumable ledger and artifact state, including large-PDF range parts,
  and atomic filesystem commits.
- Keep source-document page/geometry provenance and analyzer-element provenance
  explicit and independently traceable.
- Treat Azure AI Search as a rebuildable retrieval layer, not authoritative
  evidence storage.
- Measure keyword/filter retrieval before adding semantic, vector, or LLM
  complexity.
- Keep credentials out of command lines, files, logs, and version control.
- Run conservatively against the public REGDOCS service and billable cloud
  services.

Future product direction and open decisions are tracked in
[ROADMAP.md](ROADMAP.md).

## Disclaimer

This repository is not affiliated with or endorsed by the Canada Energy
Regulator. REGDOCS remains the authoritative public access system for the
source regulatory records.