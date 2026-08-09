# REGDOCS Processing Pipeline

An auditable five-stage pipeline for collecting public regulatory records from
the Canada Energy Regulator (CER) REGDOCS registry, turning them into a
normalized provenance-preserving corpus, and publishing searchable chunks to
Azure AI Search.

Current integrated release: **0.0.1**. See [VERSIONING.md](VERSIONING.md) and
[RELEASE_NOTES.md](RELEASE_NOTES.md). Every primary stage command reports the
same repository release with `--version`; use `--diagnostics` for component,
parser, schema, provider/API, and implementation-hash detail.

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
├── VERSION
├── VERSIONING.md
├── RELEASE_NOTES.md
├── README.md
├── ROADMAP.md
├── pipeline/
│   ├── requirements.txt
│   ├── regdocs_paths.py
│   ├── regdocs_entrypoint.py
│   ├── regdocs_release.py
│   ├── regdocs_1_scout.py
│   ├── regdocs_1_scout_core.py
│   ├── regdocs_1_scout.md
│   ├── regdocs_2_download.py
│   ├── regdocs_2_download_core.py
│   ├── regdocs_2_download.md
│   ├── regdocs_3_azure.py
│   ├── regdocs_3_azure_core.py
│   ├── regdocs_3_azure_worker.py
│   ├── regdocs_3_azure.md
│   ├── regdocs_3_docling.py
│   ├── regdocs_3_docling_core.py
│   ├── regdocs_3_docling_worker.py
│   ├── regdocs_3_docling.md
│   ├── regdocs_4_normalize.py
│   ├── regdocs_4_normalize_core.py
│   ├── regdocs_4_normalize_worker.py
│   ├── regdocs_4_normalize.md
│   ├── regdocs_5_index.py
│   ├── regdocs_5_index_core.py
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

The primary stage files are stable public entry points. They handle the shared
release `--version` and `--diagnostics` contract, then delegate normal execution
to the adjacent internal `_core.py` implementation. Existing durable component
identities inside those cores are preserved for artifact/run provenance and are
not product release numbers.

Each stage script keeps only a short purpose, invocation syntax, and pointer in
its module header. Operational policy, examples, recovery behavior, and known
limitations live in the adjacent same-named Markdown file.

SQLite may create `regdocs.db-wal`, `regdocs.db-shm`, or
`regdocs.db-journal` beside the main database while it is in use.

## Release and diagnostics commands

All primary stage version commands report the same release:

```bash
python pipeline/regdocs_1_scout.py --version
python pipeline/regdocs_2_download.py --version
python pipeline/regdocs_3_azure.py --version
python pipeline/regdocs_3_docling.py --version
python pipeline/regdocs_4_normalize.py --version
python pipeline/regdocs_5_index.py --version
```

Use `--diagnostics` when troubleshooting or recording exact implementation
identity:

```bash
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_4_normalize.py --diagnostics
```

The diagnostics output includes the repository release, component version,
implementation SHA-256, and relevant parser/schema/provider/API identifiers.

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

`pipeline/regdocs_4_normalize.py` is the public resilient Stage 4 supervisor. It
selects Azure or Docling input, launches one normalization child at a time, and
assembles deterministic JSONL projections for documents, pages, chunks, tables,
and chunk-to-source provenance. It makes no network calls. Stage 4 processes
each Azure `contents[]` entry independently, including the multiple entries
produced when Stage 3 recombines a ranged large-PDF analysis.

The normalizer qualifies every paragraph/table/figure provenance pointer with
its `content_index`, for example `/contents/2/paragraphs/12`, while retaining
the original provider-local pointer. This makes provenance unambiguous across
large PDFs without losing original page numbers or polygon geometry.

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

Stage 5 starts with full-text search plus metadata filters and facets. It keeps
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

The pipeline intentionally uses several representations for different purposes:

- **JSON** for structured native analyzer results, manifests, and state;
- **JSONL** for large normalized record collections that can be streamed and
  indexed one record at a time; and
- **Markdown** as an analyzer-produced readable text representation alongside
  richer structured JSON.

## Persistence rule

Preserve source evidence and expensive analysis results. Treat search indexes
and clearly reproducible transient work as rebuildable. See the roadmap for the
planned SQLite ledger rebuild/disaster-recovery path and future lifecycle policy.
