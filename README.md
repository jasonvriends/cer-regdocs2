# REGDOCS Atlas

REGDOCS Atlas is a pilot pipeline for collecting public regulatory records from the Canada Energy Regulator (CER) REGDOCS registry, preserving source evidence, downloading and hashing source files, extracting document structure, normalizing provenance, and publishing searchable chunks to Azure AI Search.

## Preferred command

Use the root command for normal operation:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize --provider azure
python pipeline.py normalize --provider docling
python pipeline.py index
```

Operational commands are on the same surface:

```bash
python pipeline.py version
python pipeline.py status
python pipeline.py diagnostics

python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify

python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild prepare
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare --source database/regdocs.db --rebuilt database/regdocs.rebuilt.db

python pipeline.py cost rates
python pipeline.py cost azure
```

## Pipeline

```text
CER REGDOCS
    |
    v
1. Scout
   metadata + raw HTML evidence + durable Scout manifests
    |
    v
2. Download
   source files + SHA-256 + metadata sidecars
    |
    v
3. Analyze
   Azure Content Understanding or local Docling
    |
    v
4. Normalize
   documents + pages + chunks + tables + provenance
    |
    v
5. Index
   Azure AI Search
```

Stages 1–4 share one SQLite operational ledger. Durable filesystem artifacts are intentionally kept independently of SQLite so the database can be reconstructed without re-downloading source files or rerunning expensive Stage 3 analysis when sufficient evidence survives.

Azure AI Search is a rebuildable publication layer, not an authoritative source of corpus evidence.

## Repository layout

```text
.
├── pipeline.py              # one public command/router
├── regdocs_atlas/           # current shared application package
│   ├── cli.py
│   ├── db/                  # SQLite connection, migrations, safety, run helpers
│   ├── runtime/             # locks, presentation, hashing, atomic writes
│   ├── artifacts/           # inventory and recovery planning
│   ├── rebuild.py
│   ├── rebuild_compare.py
│   ├── scout_manifests.py
│   └── ...
│
├── pipeline/                # temporary legacy stage implementations + runbooks
│   ├── requirements.txt
│   ├── regdocs_1_scout.py
│   ├── regdocs_1_scout_core.py
│   ├── regdocs_2_download.py
│   ├── regdocs_2_download_core.py
│   ├── regdocs_3_azure.py
│   ├── regdocs_3_azure_core.py
│   ├── regdocs_3_azure_worker.py
│   ├── regdocs_3_docling.py
│   ├── regdocs_3_docling_core.py
│   ├── regdocs_3_docling_worker.py
│   ├── regdocs_3_docling_worker_core.py
│   ├── regdocs_4_normalize.py
│   ├── regdocs_4_normalize_core.py
│   ├── regdocs_4_normalize_worker.py
│   ├── regdocs_5_index.py
│   └── regdocs_5_index_core.py
│
├── docs/
├── roadmap/
├── database/                # ignored operational SQLite state
└── workspace/               # ignored durable corpus artifacts
```

### Why both `pipeline/` and `regdocs_atlas/` exist today

`regdocs_atlas/` is the target home for the application. The root `pipeline.py` already owns the public command surface, database migrations, recovery tooling, shared locking/logging, cost reporting, and other new infrastructure.

The older stage implementations are still physically in `pipeline/`. The unified CLI intentionally delegates Scout, Download, Azure, Docling, Normalize, and Index to those proven implementations while they are moved incrementally into package modules.

So the split is transitional:

```text
TODAY
pipeline.py -> regdocs_atlas.cli -> pipeline/regdocs_* implementation

TARGET
pipeline.py -> regdocs_atlas.cli -> regdocs_atlas/stages/*
                                  -> no legacy pipeline/ implementation directory
```

Do not add new application logic to `pipeline/` unless it is required to maintain a legacy implementation during the transition. New shared code belongs in `regdocs_atlas/`.

## Persistent state

The repository treats these as important durable state rather than disposable cache:

```text
database/regdocs.db
workspace/1_scout/
workspace/2_download/
workspace/3_analyze/
workspace/4_normalize/
```

Stage 1 preserves raw REGDOCS HTML. Stage 2 preserves source files and deterministic metadata sidecars. Stage 3 preserves provider-native analysis artifacts. Stage 4 preserves normalized JSONL projections.

The canonical project log is:

```text
workspace/pipeline.log
```

The preferred orchestration lock is:

```text
database/locks/pipeline.lock
```

Legacy stage-specific locks/logs remain temporarily while the old stage implementations are still supported.

## Install

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the single shared dependency set:

```bash
python -m pip install -r pipeline/requirements.txt
```

Python 3.10 or newer is required.

## Database migrations

SQLite schema evolution is owned by `regdocs_atlas/db/migrations.py` and tracked in the `schema_migrations` table.

Preview and apply migrations with:

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify
```

For an existing database, migration creates a consistent SQLite backup by default before applying pending changes and then runs schema, integrity, and foreign-key verification.

## Disaster recovery

Prepare self-describing Scout manifests while the healthy ledger exists:

```bash
python pipeline.py rebuild prepare
```

Then inspect what survives:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
```

Build a second database rather than overwriting the working one:

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

The rebuild path restores only facts supported by surviving evidence. Missing Scout facts are represented as recovery state/tasks rather than being invented.

See [docs/DATABASE_RECOVERY.md](docs/DATABASE_RECOVERY.md).

## Azure Content Understanding cost visibility

REGDOCS reads metered usage from saved Azure Content Understanding results. It does not hard-code service prices because rates can vary by region, currency, and offer.

Configure the rates applicable to the Azure subscription when dollar estimates are desired:

```text
REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD
REGDOCS_AZURE_CU_BASIC_PER_1000_USD
REGDOCS_AZURE_CU_STANDARD_PER_1000_USD
```

Inspect configuration and historical usage with:

```bash
python pipeline.py cost rates
python pipeline.py cost azure
```

Docling is local compute and reports Azure service cost as `n/a`.

## Pilot verification

This pilot intentionally has no GitHub Actions CI workflow or dedicated repository test suite. Verification is operational and corpus-oriented:

```bash
python pipeline.py db verify
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare --source database/regdocs.db --rebuilt database/regdocs.rebuilt.db
python pipeline.py status
```

Individual stage runbooks under `pipeline/*.md` document additional stage-specific checks while those legacy implementations remain in place.

## Principles

- preserve public REGDOCS evidence and source-document identity;
- use SHA-256 as the definitive downloaded-file version identity;
- keep acquisition, analysis, normalization, and indexing independently rebuildable;
- avoid rerunning expensive Azure analysis when matching durable artifacts already exist;
- never fabricate missing recovery facts;
- keep credentials out of command lines, logs, and version control;
- treat Azure AI Search as a derivative retrieval layer;
- keep the pilot simple and move complexity into the product only when it proves useful.

Future work is tracked in [ROADMAP.md](ROADMAP.md) and the focused plans under `roadmap/`.

## Disclaimer

This repository is not affiliated with or endorsed by the Canada Energy Regulator. REGDOCS remains the authoritative public access system for the source regulatory records.
