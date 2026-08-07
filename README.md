# REGDOCS Acquisition Pipeline

An auditable document-acquisition pipeline for collecting public regulatory records from the Canada Energy Regulator (CER) REGDOCS registry and preparing them for downstream document intelligence, search, analytics, and Retrieval-Augmented Generation (RAG).

> **Current scope:** the repository currently implements **Stage 1: scout** and **Stage 2: download**. OCR, layout analysis, text extraction, chunking, embeddings, and indexing are downstream stages and are not implemented by the two scripts documented here.

## What is REGDOCS?

REGDOCS is the Canada Energy Regulator's public regulatory document registry. It provides access to publicly available documents filed on the legal record for hearings and other written regulatory proceedings, including material submitted by applicants, the regulator, and participants in regulatory processes.

The CER describes REGDOCS as the collection of publicly accessible documents filed on the legal record for hearings and other written regulatory proceedings.

Official references:

- REGDOCS: https://apps.cer-rec.gc.ca/REGDOCS/
- CER regulatory documents Q&A: https://www.cer-rec.gc.ca/en/applications-hearings/regulatory-document/questions-answers.html

REGDOCS is designed primarily as a public-facing discovery and retrieval system. This project adds a repeatable local acquisition layer around it so that a collection can be refreshed, audited, versioned, processed, and indexed without losing provenance.

## What this project is

This project is best described as a **REGDOCS document acquisition and provenance pipeline**.

Its job is to:

1. discover public REGDOCS records;
2. collect and normalize available metadata;
3. traverse explicit Folder and Compound Document membership;
4. preserve the source HTML used to derive metadata;
5. maintain durable pipeline state in SQLite;
6. download eligible source files;
7. verify and hash downloaded bytes;
8. preserve replaced file versions;
9. optionally emit portable metadata sidecars; and
10. hand a clean, traceable corpus to downstream document-intelligence and RAG tooling.

The pipeline intentionally separates **acquisition** from **document understanding**. The SQLite ledger and source archive remain the authoritative acquisition record; downstream processors can be rerun without re-scouting or re-downloading unchanged material.

## Pipeline

```text
                    Canada Energy Regulator
                           REGDOCS
                              |
                              v
                 +--------------------------+
                 |  1. Scout / Catalogue    |
                 |  regdocs_1_scout.py      |
                 +--------------------------+
                    | metadata + provenance
                    v
                 +--------------------------+
                 |  SQLite pipeline ledger  |
                 |  regdocs.db              |
                 +--------------------------+
                    |                 |
          raw HTML  |                 | eligible file records
                    v                 v
             raw/regdocs/      +--------------------------+
                               |  2. Download / Verify    |
                               |  regdocs_2_download.py   |
                               +--------------------------+
                                          |
                          verified files + hashes + sidecars
                                          v
                                     downloads/
                                          |
                                          v
                 +-----------------------------------------+
                 |  3. Document intelligence — downstream |
                 |  OCR / layout / text / tables / images |
                 +-----------------------------------------+
                                          |
                                          v
                 +-----------------------------------------+
                 |  4. RAG/index export — downstream      |
                 |  chunks / metadata / search / vectors  |
                 +-----------------------------------------+
```

## Current stages

### Stage 1 — Scout

`regdocs_1_scout.py` builds and refreshes the local catalogue.

It:

- searches REGDOCS over a date range;
- parses stable REGDOCS item IDs;
- expands explicit `Folder` and `Compound Document` containers;
- traverses nested containers with depth and item guards;
- discovers the live Advanced Search facet catalogue;
- maps facet values back to scouted records;
- fetches each selected item's own detail page;
- stores normalized metadata in SQLite;
- stores successful source HTML responses in a content-addressed gzip archive;
- records run history, progress, warnings, errors, and retry state.

It deliberately does **not** follow arbitrary project, company, breadcrumb, taxonomy, navigation, or outbound links.

See [README-regdocs_1_scout.md](README-regdocs_1_scout.md).

### Stage 2 — Download

`regdocs_2_download.py` converts eligible catalogue entries into verified local files.

It:

- selects records from the scout ledger where `is_file = 1`;
- skips known HTML records by default;
- excludes folders, compound documents, and synthetic paper-only rows;
- reconciles files already present on disk;
- streams downloads through a partial directory;
- identifies file types from bytes, HTTP headers, filenames, and catalogue hints;
- validates downloaded files;
- calculates SHA-256 hashes;
- atomically commits completed files;
- archives replaced versions;
- records current and historical file facts in SQLite;
- optionally emits deterministic `<document-id>.metadata.json` sidecars.

See [README-regdocs_2_download.md](README-regdocs_2_download.md).

## Why use a ledger?

The project treats SQLite as a **pipeline ledger**, not as the final search engine.

The five user tables are:

| Table | Purpose |
|---|---|
| `documents` | One row per REGDOCS item plus metadata and stage status |
| `runs` | Run history, parameters, heartbeat, progress, counters, summaries |
| `errors` | Structured warnings/errors and resolution state |
| `raw_snapshots` | Provenance pointers to preserved REGDOCS HTML responses |
| `files` | Downloaded file versions, hashes, types, paths, and current-version state |

This gives each document a durable lifecycle:

```text
discovered
   -> scouted
   -> downloaded
   -> processed       # downstream
   -> exported        # downstream
```

The ledger makes interrupted runs recoverable and allows later stages to operate only on work that is new, changed, failed, or incomplete.

## Provenance model

A useful RAG corpus should be able to answer not only "what text did we extract?" but also:

- Which REGDOCS item did it come from?
- What filing/project/company metadata was visible?
- Which source page produced that metadata?
- Which exact file bytes were processed?
- Has the source file changed?
- Which run discovered or downloaded it?
- Were there unresolved acquisition errors?

This project preserves those answers through:

- stable document IDs;
- source URLs;
- raw HTML snapshots;
- SHA-256 content hashes;
- current and historical file records;
- structured run/error history; and
- optional metadata sidecars.

## Suggested repository layout

```text
.
├── regdocs_1_scout.py
├── regdocs_2_download.py
├── regdocs.db
├── raw/
│   └── regdocs/
│       ├── advanced/
│       ├── search/
│       ├── facet/
│       ├── detail/
│       └── container/
├── downloads/
│   ├── <document-id>.pdf
│   ├── <document-id>.docx
│   ├── <document-id>.metadata.json
│   ├── .partial/
│   └── _versions/
│       └── <document-id>/
└── _audit/
    ├── scout.log
    ├── scout-progress.json
    ├── download.log
    ├── download-progress.json
    └── *.lock
```

Paths are configurable. The examples above reflect the scripts' default layout.

## Quick start

### 1. Create an environment

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install httpx beautifulsoup4 tqdm
```

Optional faster HTML parsing for the scout:

```bash
pip install lxml
```

### 3. Scout REGDOCS

```bash
python regdocs_1_scout.py
```

With no date arguments, the scout uses its automatic refresh policy:

- new database: January 1 of the current year through today;
- normal repeat run: newest stored filing date minus seven days through today;
- at least every 30 days: refresh the full current year.

Inspect progress:

```bash
python regdocs_1_scout.py --status
```

Audit the ledger and raw archive:

```bash
python regdocs_1_scout.py --audit
```

### 4. Preview downloads

```bash
python regdocs_2_download.py --dry-run --limit 25
```

### 5. Download source files

```bash
python regdocs_2_download.py
```

Download and write metadata sidecars:

```bash
python regdocs_2_download.py --sidecars
```

### 6. Inspect download status

```bash
python regdocs_2_download.py --status
```

## RAG-ready handoff

The current scripts create a strong **pre-processing corpus**, but "RAG-ready" should mean more than "a folder of PDFs."

A downstream processing stage should normally produce a canonical record such as:

```json
{
  "document_id": "4710492",
  "source_url": "https://apps.cer-rec.gc.ca/REGDOCS/...",
  "sha256": "...",
  "title": "...",
  "filing_number": "...",
  "company": "...",
  "project": "...",
  "document_type": ["..."],
  "text": "...",
  "pages": [],
  "chunks": [
    {
      "chunk_id": "4710492:p12:c03",
      "text": "...",
      "page": 12,
      "section": "...",
      "metadata": {}
    }
  ]
}
```

Recommended downstream responsibilities:

- native text extraction where possible;
- OCR for scanned material;
- page/layout preservation;
- table extraction;
- image or figure interpretation where useful;
- language detection;
- document-level and page-level quality flags;
- deterministic chunk IDs;
- chunk-to-document/page provenance;
- embedding/index export;
- incremental reprocessing keyed by source SHA-256.

The source file and acquisition ledger should remain immutable inputs to that processing layer.

## Operational principles

### Be polite to the public service

Both stages use globally paced request starts and conservative default concurrency. Do not increase concurrency or remove delays merely to maximize throughput.

### Preserve raw evidence

The scout's `raw/regdocs/` archive is not disposable cache. It is the evidence behind the metadata parser and can support future parser improvements or audits.

### Treat hashes as version identity

HTTP metadata such as `ETag` or `Last-Modified` can support efficient change detection, but the source file SHA-256 should remain the definitive local version identity.

### Prefer resumability over cleverness

Pipeline state belongs in the ledger. A failed or interrupted run should leave completed work committed and make unfinished work obvious.

### Keep acquisition separate from indexing

Do not make the vector database, search index, or OCR output the source of truth for acquisition state. They should be reproducible products of the ledger + source archive + downloaded files.

## Status model

The ledger includes stage-level statuses such as:

- `scout_status`
- `download_status`
- `process_status`
- `export_status`
- `detail_status`

This allows later tooling to select work explicitly instead of inferring state from filenames.

## Auditability and reproducibility

Useful checks include:

```bash
python regdocs_1_scout.py --check-schema
python regdocs_1_scout.py --audit
python regdocs_1_scout.py --status-json

python regdocs_2_download.py --verify-existing
python regdocs_2_download.py --status-json
python regdocs_2_download.py --self-test
```

The scout also has an offline self-test:

```bash
python regdocs_1_scout.py --self-test
```

## What this project is not

It is not:

- an official CER API;
- an official CER data product;
- a general-purpose web crawler;
- a replacement for REGDOCS;
- a legal-record management system;
- a completed OCR/RAG/search platform;
- a guarantee that every public REGDOCS record is downloadable as a file.

It is an independent acquisition pipeline built around publicly accessible REGDOCS content.

## Roadmap

A natural continuation is:

1. **Scout** — implemented.
2. **Download** — implemented.
3. **Document intelligence** — OCR, native text, layout, tables, images, language, quality.
4. **Normalize** — canonical document/page/chunk schema.
5. **Export** — JSONL/Parquet/object storage.
6. **Index** — full-text + metadata + vector/hybrid search.
7. **Serve** — retrieval API/RAG applications.
8. **Refresh** — incremental discovery, update detection, selective reprocessing.

The downloader source already anticipates a future lightweight update-check mode based on stored `ETag`/`Last-Modified` validators followed by SHA-256 verification when bytes change.

## Documentation

- [Stage 1 — Scout](README-regdocs_1_scout.md)
- [Stage 2 — Download](README-regdocs_2_download.md)

## Disclaimer

This repository is not affiliated with or endorsed by the Canada Energy Regulator. REGDOCS remains the authoritative public access system for the source regulatory records.
