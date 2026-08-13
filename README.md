# REGDOCS Atlas

REGDOCS Atlas is a Python pipeline for collecting public records from the Canada Energy Regulator (CER) REGDOCS system and turning them into data that can be searched, analyzed, and reused.

You do **not** need to be a Python programmer to operate it. Most work is done with commands that start with:

```bash
python pipeline.py
```

If you are new to the project, read this file first. When you need every command and every switch, use **[SYNTAX.md](SYNTAX.md)**.

---

## What does this project do?

Think of REGDOCS Atlas as a six-step assembly line:

```text
CER REGDOCS website
        │
        ▼
1. SCOUT       Find records and save evidence about what REGDOCS returned
        │
        ▼
2. DOWNLOAD    Download the source files, such as PDFs
        │
        ▼
3. ANALYZE     Read the files with Azure Content Understanding or Docling
        │
        ▼
4. NORMALIZE   Turn analyzer output into one consistent local format
        │
        ▼
5. INDEX       Publish search-ready chunks to Azure AI Search
        │
        ▼
6. ENRICH      Build evidence-backed graph and chronology artifacts
```

The project also keeps a local SQLite database at `database/regdocs.db`. That database is the pipeline's **ledger**: it records what has been found, downloaded, analyzed, normalized, and processed.

### The six stages in plain language

| Stage | Name | What it does | Uses the internet? | Can cost money? |
|---|---|---|---:|---:|
| 1 | Scout | Finds REGDOCS records and saves source evidence | Yes | No |
| 2 | Download | Downloads the actual source files | Yes | No |
| 3 | Analyze | Extracts document structure and text | Azure: yes; Docling: no | **Azure can** |
| 4 | Normalize | Converts analysis into consistent JSONL files | No | No |
| 5 | Index | Publishes searchable chunks to Azure AI Search | Yes | Azure Search charges may apply |
| 6 | Enrich | Derives and publishes regulatory entities, relationships, and events | Publish only | Azure Search charges may apply |

---

# The most important safety rule

## Protect `workspace/3_analyze/`

Azure document analysis can be expensive to reproduce. Successful Stage 3 results are saved under:

```text
workspace/3_analyze/
```

Do not delete that directory just because it is not stored in Git.

Also do **not** run this command in a working REGDOCS Atlas checkout:

```bash
git clean -fdx
```

That command can delete ignored files, including the local `workspace/` and `database/` directories.

The important local data is:

```text
workspace/1_scout/       REGDOCS source evidence and Scout manifests
workspace/2_download/    downloaded source files and metadata
workspace/3_analyze/     Azure and Docling analysis artifacts
database/regdocs.db      local SQLite pipeline ledger
```

Stages 4 through 6 are designed to be recreated from earlier results:

```text
workspace/4_normalize/
workspace/5_index/
workspace/6_enrich/
```

The SQLite ledger can also be rebuilt from the durable Stage 1-3 artifacts.

---

# Install the project

These examples assume Linux or WSL. Run them from the repository root.

## 1. Create a Python virtual environment

```bash
python -m venv .venv
```

A virtual environment gives this project its own Python packages instead of mixing them with packages used by other projects.

## 2. Activate it

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

## 3. Install the required packages

```bash
python -m pip install -r regdocs_atlas/requirements.txt
```

## 4. Check that the command works

```bash
python pipeline.py version
python pipeline.py help
python pipeline.py status
```

The current project version is stored in the `VERSION` file.

---

# How the command line is designed

REGDOCS Atlas is intentionally cautious.

A stage name by itself does **not** start work. It shows help instead:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
python pipeline.py enrich
```

To perform work, you must choose an action such as `run`, `plan`, `publish`, `probe`, or `repair`.

For example:

```bash
python pipeline.py download plan
```

previews downloads, while:

```bash
python pipeline.py download run
```

actually downloads files.

This distinction is especially important for Azure analysis because `run` can submit billable work.

---

# A beginner's first workflow

The normal order is:

```text
Scout → Download → Analyze → Normalize → Index → Enrich
```

You do not always need to run every stage. If a stage is already complete, its planning or status command should show that there is little or nothing left to do.

## Before doing anything large

Start with:

```bash
python pipeline.py status
python pipeline.py scout coverage
```

`status` shows the overall pipeline state. `scout coverage` shows which filing dates have already been successfully collected.

---

## Stage 1 — Scout

Scout finds REGDOCS records and preserves evidence from the REGDOCS website.

A normal Scout run requires a start date and an end date:

```bash
python pipeline.py scout run \
  --start-date 2026-08-01 \
  --end-date 2026-08-09
```

If you want to test a date range first, use `probe` with a small limit:

```bash
python pipeline.py scout probe \
  --start-date 2026-08-09 \
  --end-date 2026-08-09 \
  --limit 5
```

`probe` still contacts REGDOCS and still saves run/error/raw-snapshot evidence, but it does not update the main document records in the same way as a normal acquisition run.

Useful Scout checks:

```bash
python pipeline.py scout coverage
python pipeline.py scout status
python pipeline.py scout audit
```

---

## Stage 2 — Download

First preview what would be downloaded:

```bash
python pipeline.py download plan
```

Then download the eligible files:

```bash
python pipeline.py download run
```

For a small test:

```bash
python pipeline.py download run --limit 25
```

For one known document:

```bash
python pipeline.py download run --document-id 4657417
```

Downloaded files are stored under `workspace/2_download/`.

---

## Stage 3 — Analyze

Stage 3 has two providers.

### Option A: Azure Content Understanding

Azure analysis is the billable path. **Always preview the selection first.**

Preview all currently eligible documents:

```bash
python pipeline.py analyze azure plan --all
```

Preview only ten:

```bash
python pipeline.py analyze azure plan --limit 10
```

Analyze one document:

```bash
python pipeline.py analyze azure run --document-id 4657417
```

Analyze every currently eligible document:

```bash
python pipeline.py analyze azure run --all
```

Large PDFs are automatically handled in page ranges small enough for the Azure Content Understanding request limit. Existing valid range artifacts can be reused instead of being submitted again.

### Option B: Docling

Docling runs locally and does not create Azure Content Understanding charges.

Check status:

```bash
python pipeline.py analyze docling status
```

Test one document:

```bash
python pipeline.py analyze docling run --max-documents 1
```

Run more:

```bash
python pipeline.py analyze docling run --max-documents 100
```

Both Azure and Docling use isolated child processes so a document-level crash is less likely to bring down the whole batch. The Docling supervisor also terminates a child that runs longer than 20 minutes, retries it in a fresh process, and quarantines the document after the configured maximum attempts. Override the limit with `--document-timeout-seconds` when a known large document needs more time.

---

## Stage 4 — Normalize

Different analyzers produce different output. Normalize converts that output into one consistent structure for pages, chunks, tables, and provenance.

Preview Azure normalization:

```bash
python pipeline.py normalize plan --provider azure
```

Run it:

```bash
python pipeline.py normalize run --provider azure
```

Use several local workers:

```bash
python pipeline.py normalize run --provider azure --concurrency 4
```

Use Docling results instead:

```bash
python pipeline.py normalize run --provider docling --concurrency 4
```

### Important Normalize warning

A real Normalize run replaces the canonical Stage 4 output with the documents selected for that run.

For example:

```bash
python pipeline.py normalize run --provider azure --limit 10
```

can produce a canonical Stage 4 corpus containing only those selected documents.

For testing, use `plan` or send the output to another directory:

```bash
python pipeline.py normalize run \
  --provider azure \
  --document-id 4657417 \
  --output-dir /tmp/regdocs-normalize-test
```

---

## Stage 5 — Index and search

Stage 5 converts normalized chunks into Azure AI Search documents.

Preview first:

```bash
python pipeline.py index plan
```

For the hybrid v2 index, plan and generate the resumable embedding cache with an explicit scope:

```bash
python pipeline.py index embed plan --all
python pipeline.py index embed run --all
```

Publish:

```bash
python pipeline.py index publish --profile hybrid --index-name regdocs-chunks-v2
```

After a complete upload, optionally switch a stable Search alias atomically:

```bash
python pipeline.py index publish --profile hybrid \
  --index-name regdocs-chunks-v2 \
  --promote-alias --alias-name regdocs-current
```

Search the published index:

```bash
python pipeline.py index query "compressor station" --top 10
```

For testing, you can publish to another index name:

```bash
python pipeline.py index publish --index-name regdocs-chunks-test
```

## Stage 6 — Enrich regulatory intelligence

Preview deterministic entities, relationships, and filing chronology locally:

```bash
python pipeline.py enrich plan
```

Build the local JSONL artifacts, then explicitly publish their three Search indexes:

```bash
python pipeline.py enrich run
python pipeline.py enrich publish
```

Run the model layer as a small, explicit pilot before expanding it:

```bash
python pipeline.py enrich extract plan --limit 10
python pipeline.py enrich extract run --limit 10
python pipeline.py enrich publish --include-model-dir workspace/6_enrich/model
```

Extraction is resumable and model records are marked `unreviewed`. The deterministic publish excludes them unless `--include-model-dir` is supplied.

Every derived relationship and event carries its origin, schema/extractor version, review state, and available document/chunk/page evidence. Deterministic filing activity is kept distinct from later model-extracted occurrence chronology.

---

# Azure setup

REGDOCS Atlas reads Azure settings from environment variables in the shell that launches the command. It does not automatically load a `.env` file.

Never commit secrets or API keys to Git.

## Azure Content Understanding

At minimum, provide an endpoint:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://YOUR-RESOURCE.cognitiveservices.azure.com/"
```

You can also provide a key:

```bash
export CONTENTUNDERSTANDING_KEY="YOUR-KEY"
```

If the key is not supplied, the project uses Azure Identity and `DefaultAzureCredential`.

Common settings:

```bash
export CONTENTUNDERSTANDING_API_VERSION="2025-11-01"
export CONTENTUNDERSTANDING_ANALYZER_ID="prebuilt-layout"
export CONTENTUNDERSTANDING_POLLING_INTERVAL="3"
```

## Azure AI Search

```bash
export AZURE_SEARCH_ENDPOINT="https://YOUR-SERVICE.search.windows.net"
export AZURE_SEARCH_ADMIN_KEY="YOUR-KEY"
export AZURE_SEARCH_INDEX_NAME="regdocs-chunks"
export AZURE_SEARCH_ALIAS_NAME="regdocs-current"
export FOUNDRY_PROJECT_ENDPOINT="https://YOUR-RESOURCE.services.ai.azure.com/api/projects/YOUR-PROJECT"
export FOUNDRY_EMBEDDING_DEPLOYMENT="YOUR-EMBEDDING-DEPLOYMENT"
```

To add hybrid retrieval without changing that lexical index, publish to the
versioned hybrid index with the same embedding deployment used at query time:

```bash
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com"
export AZURE_OPENAI_API_KEY="YOUR-EMBEDDING-KEY"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT="text-embedding-3-small"

python tools/publish_hybrid_index.py --dry-run --limit 100
python tools/publish_hybrid_index.py --limit 100
# After pilot evaluation, omit --limit to publish the complete corpus.
```

The Next.js workbench configuration for Azure AI Search hybrid retrieval and
Microsoft Foundry cited answers is documented in [`ui/README.md`](ui/README.md).
After uploading `workspace/` and `database/` to an existing private Blob
container with SAS, use [`ui/deploy/`](ui/deploy/) in Azure Cloud Shell to
provision Search, Foundry, and App Service, publish the index, and deploy the UI.

See [SYNTAX.md](SYNTAX.md#azure-environment-variables) for the full environment-variable reference.

---

# Where files are stored

The main local layout is:

```text
.
├── pipeline.py              public command entry point
├── regdocs_atlas/           application code
├── ui/                      Next.js Azure AI Search workbench
├── README.md                beginner guide and architecture overview
├── SYNTAX.md                complete command reference
├── VERSION                  project version
├── database/                local database, backups, and locks; ignored by Git
└── workspace/               downloaded and generated artifacts; ignored by Git
    ├── 1_scout/
    ├── 2_download/
    ├── 3_analyze/
    ├── 4_normalize/
    └── 5_index/
```

Inside the Python package:

```text
regdocs_atlas/
├── requirements.txt         Python dependencies for every pipeline stage
├── requirements-deploy.txt  minimal Cloud Shell publishing dependencies
├── cli.py                   shared command-line routing
├── paths.py                 standard project paths
├── version.py               reads VERSION
├── costs.py                 Azure usage/cost reporting
├── db/                      SQLite schema, migrations, and helpers
├── runtime/                 locks, logging, console output, atomic I/O
├── artifacts/               artifact inventory and recovery planning
├── stages/                  executable Stage 1-5 code and workers
├── scout_*.py               Scout manifests, coverage, and recovery helpers
├── analysis_manifests.py    durable Stage 3 manifest support
└── rebuild*.py, flatten.py  database recovery and clean rebuild tools
```

---

# Database and recovery

The active database is:

```text
database/regdocs.db
```

It is useful, but it is not the only copy of important information. The project deliberately saves durable artifacts to disk so the ledger can be reconstructed.

Check what recovery material exists:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild prepare
```

Create a rebuilt database beside the active one:

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
```

Verify it:

```bash
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

Compare it with the active database:

```bash
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

The key Stage 1-3 recovery result is:

```text
source_and_stage3_equivalent: true
```

A clean operational database can also be built with:

```bash
python pipeline.py rebuild create --flat
```

That rebuilds from durable Stage 1-3 evidence and then removes historical run/error/recovery bookkeeping from the new database. It does not overwrite the active database and does not contact REGDOCS or Azure.

---

# Logs and locks

State-changing root commands share a global pipeline lock:

```text
database/locks/pipeline.lock
```

Stage-specific locks also exist as extra protection.

The current state-changing run is logged to:

```text
workspace/pipeline.log
```

When another state-changing stage starts, the previous log is compressed under:

```text
workspace/logs/
```

The project keeps a bounded set of recent log archives instead of allowing that directory to grow forever.

If a command reports an unexpected lock, first make sure another pipeline process is not still running. Do not use `--force-lock` just to make the error disappear.

---

# Check Azure analysis cost information

Show configured estimate rates:

```bash
python pipeline.py cost rates
```

Show the latest Azure analysis cost snapshot:

```bash
python pipeline.py cost azure
```

Show one recorded run:

```bash
python pipeline.py cost azure --run-id 123
```

These are estimates based on saved usage and configured rates. Azure billing is the final source for actual charges.

---

# Useful words in this repository

| Word | Plain-language meaning |
|---|---|
| **CLI** | Command-line interface: a program you control by typing commands |
| **Ledger** | The SQLite database that records pipeline state |
| **Artifact** | A file produced or preserved by a pipeline stage |
| **Manifest** | A structured list describing saved artifacts and their identities |
| **Hash / SHA-256** | A fingerprint used to check whether a file is the same file as before |
| **JSONL** | A text format where each line is one JSON record |
| **Chunk** | A smaller piece of a document used for search |
| **Provenance** | Information showing where extracted content came from |
| **Canonical output** | The main official local output used by the next stage |
| **Provider** | The document-analysis engine: Azure or Docling |
| **Worker** | A process that handles a piece of work, usually one document |

---

# Documentation map

Use the files this way:

- **README.md** — understand the project, install it, and learn the normal workflow.
- **SYNTAX.md** — look up exact commands, switches, environment variables, safety notes, and troubleshooting examples.
- **VERSION** — the current release number used by `python pipeline.py version`.

Published release notes are kept on the repository's GitHub Releases page.

If you are unsure what a command will do, use its help or planning action before running it:

```bash
python pipeline.py help
python pipeline.py help analyze azure
python pipeline.py analyze azure plan --limit 5
```
