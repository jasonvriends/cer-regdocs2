# REGDOCS Processing Pipeline

An auditable four-stage pipeline for collecting public regulatory records from
the Canada Energy Regulator (CER) REGDOCS registry and turning them into a
normalized, provenance-preserving corpus.

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
   Azure Content Understanding JSON + Markdown
              |
              v
4. Normalize
   documents + pages + chunks + tables + provenance
```

All four stages share one SQLite pipeline ledger. Source evidence, downloaded
files, analysis artifacts, and normalized projections remain separate so each
stage can be audited and rerun independently.

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
│   ├── regdocs_3_analyze.py
│   ├── regdocs_3_analyze.md
│   ├── regdocs_4_normalize.py
│   └── regdocs_4_normalize.md
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
    │   └── content-understanding/
    │       ├── raw/
    │       └── markdown/
    └── 4_normalize/
        ├── documents.jsonl
        ├── pages.jsonl
        ├── chunks.jsonl
        ├── tables.jsonl
        └── provenance.jsonl
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

`pipeline/regdocs_3_analyze.py` sends current downloaded files to Azure AI
Content Understanding using `prebuilt-layout`, preserves immutable raw JSON and
Markdown, and records analysis status and provenance in SQLite.

Primary outputs:

```text
database/regdocs.db
workspace/3_analyze/content-understanding/raw/
workspace/3_analyze/content-understanding/markdown/
```

Detailed documentation: [Stage 3 — Analyze](pipeline/regdocs_3_analyze.md).

### 4 — Normalize

`pipeline/regdocs_4_normalize.py` locally converts successful layout analyses
into deterministic JSONL projections for documents, pages, chunks, tables, and
chunk-to-source provenance. It makes no network calls.

Primary outputs:

```text
database/regdocs.db
workspace/4_normalize/
```

Detailed documentation: [Stage 4 — Normalize](pipeline/regdocs_4_normalize.md).

## Ledger

SQLite is a pipeline ledger, not the final search database. The acquisition
stages own five base tables:

| Table | Purpose |
|---|---|
| `documents` | REGDOCS identity, metadata, relationships, and stage status |
| `runs` | Run parameters, progress, heartbeat, counters, and summaries |
| `errors` | Structured stage warnings and failures |
| `raw_snapshots` | Hashes and paths for preserved REGDOCS HTML |
| `files` | Downloaded versions, hashes, types, and current-file state |

Downstream stages add:

| Table | Purpose |
|---|---|
| `analyses` | Stage 3 analyzer identity, artifact paths, status, and counts |
| `normalizations` | Stage 4 version/config identity, status, hashes, and counts |

Schema checks require the tables owned by a stage while allowing additive
tables owned by later stages.

## Quick start

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Python 3.10 or newer is required. Install the shared dependencies for all four
stages:

```bash
python -m pip install -r pipeline/requirements.txt
```

The requirements file includes Scout's faster `lxml` parser, progress-bar
support, and the Azure libraries used by Stage 3. Stage 4 itself uses only the
Python standard library.

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

### Run Stage 3

Configure Azure through the shell environment. Do not place an API key in a
command line, script, or documentation.

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"  # omit when using DefaultAzureCredential
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
python pipeline/regdocs_3_analyze.py --limit 10
```

Stage 3 can incur Azure charges and requires one explicit selection scope. Keep
initial runs bounded. After reviewing a complete no-Azure-call preview, run all
remaining eligible files with:

```bash
python pipeline/regdocs_3_analyze.py --all --dry-run
python pipeline/regdocs_3_analyze.py --all
```

See the Stage 3 runbook before a full run, and do not use `--force` unless you
intend to resubmit matching successful analyses.

### Run Stage 4

Normalize one document as a pilot:

```bash
python pipeline/regdocs_4_normalize.py \
  --document-id 2969897 \
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
```

## Operational principles

- Preserve `workspace/1_scout/raw/`; it is source evidence, not cache.
- Treat source-file SHA-256 as the definitive downloaded version identity.
- Keep acquisition, analysis, normalization, and indexing independently
  reproducible.
- Prefer resumable ledger state and atomic filesystem commits.
- Keep credentials out of command lines, files, logs, and version control.
- Run conservatively against the public REGDOCS service.

Future product direction and open decisions are tracked in
[ROADMAP.md](ROADMAP.md).

## Disclaimer

This repository is not affiliated with or endorsed by the Canada Energy
Regulator. REGDOCS remains the authoritative public access system for the
source regulatory records.
