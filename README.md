# REGDOCS Atlas

REGDOCS Atlas is a pilot pipeline for collecting public regulatory records from the Canada Energy Regulator (CER) REGDOCS registry, preserving source evidence, downloading source files, extracting document structure, normalizing provenance, and publishing searchable chunks to Azure AI Search.

## Use one command

Use the root CLI for normal operation:

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
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db

python pipeline.py cost rates
python pipeline.py cost azure
```

## Pipeline

```text
CER REGDOCS
    |
    v
1. Scout
   metadata + raw HTML evidence + durable manifests
    |
    v
2. Download
   source files + SHA-256 + metadata sidecars
    |
    v
3. Analyze
   Azure Content Understanding or local Docling
   provider artifacts + durable analysis manifests
    |
    v
4. Normalize
   documents + pages + chunks + tables + provenance
    |
    v
5. Index
   Azure AI Search
```

Stages 1–4 use one SQLite operational ledger. Durable filesystem artifacts are kept independently so losing SQLite does not require another REGDOCS crawl, source-file download, or billable Azure analysis when the corresponding evidence survives.

Normalize is a local rebuildable transformation from Stage 3 artifacts. Azure AI Search is a rebuildable publication layer from Stage 4; neither is treated as authoritative source evidence.

## Repository layout

```text
.
├── pipeline.py
│   public command/router
│
├── regdocs_atlas/
│   ├── cli.py
│   ├── db/                  SQLite connection, migrations, safety
│   ├── runtime/             locks, logging helpers, hashing, atomic writes
│   ├── artifacts/           artifact inventory and recovery planning
│   ├── stages/
│   │   ├── index.py         canonical Stage 5 implementation
│   │   └── legacy/          proven Stage 1-4 cores/workers moved intact
│   ├── scout_manifests.py
│   ├── analysis_manifests.py
│   ├── rebuild*.py
│   └── ...
│
├── pipeline/
│   thin compatibility launchers only
│   ├── regdocs_1_scout.py
│   ├── regdocs_2_download.py
│   ├── regdocs_3_azure.py
│   ├── regdocs_3_docling.py
│   ├── regdocs_4_normalize.py
│   ├── regdocs_5_index.py
│   ├── regdocs_entrypoint.py
│   ├── requirements.txt
│   └── requirements-docling.txt
│
├── docs/
│   ├── DATABASE_RECOVERY.md
│   └── stages/
│       ├── regdocs_1_scout.md
│       ├── regdocs_2_download.md
│       ├── regdocs_3_azure.md
│       ├── regdocs_3_docling.md
│       ├── regdocs_4_normalize.md
│       └── regdocs_5_index.md
│
├── database/                ignored operational SQLite state
└── workspace/               ignored durable corpus artifacts
```

### Why `pipeline/` still exists

`regdocs_atlas/` is now the application home. The large stage cores and workers no longer live in `pipeline/`.

The remaining `pipeline/*.py` files are small compatibility launchers because the current unified orchestration still executes stages as subprocesses through those entry points. This preserves the pilot's existing process isolation, canonical logging, stage banners, locks, and public `--version`/`--diagnostics` behavior while the internals are cleaned up.

Do not add new application logic to `pipeline/`. New code belongs in `regdocs_atlas/`.

The final cleanup step will be to route `regdocs_atlas.cli` directly to packaged stage entry points and then delete the compatibility launchers and `pipeline/` directory entirely. That should be treated as an orchestration change, not mixed into a file-move cleanup.

## Install

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r pipeline/requirements.txt
```

Python 3.10 or newer is required.

`pipeline/requirements.txt` is currently the single dependency set. `requirements-docling.txt` is only a compatibility redirect for older Docling messages and contains no independent versions.

## Persistent state

Preserve these during normal operation and recovery work:

```text
database/regdocs.db
workspace/1_scout/
workspace/2_download/
workspace/3_analyze/
workspace/4_normalize/
```

The canonical orchestration log is:

```text
workspace/pipeline.log
```

The preferred orchestration lock is:

```text
database/locks/pipeline.lock
```

Stage-specific locks remain inside the unchanged legacy cores as defense-in-depth until those implementations are refactored into normal package modules.

## Database and recovery

SQLite schema evolution is owned by `regdocs_atlas/db/migrations.py` and tracked in `schema_migrations`.

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify
```

A migration of an existing database creates a consistent backup by default and verifies schema, SQLite integrity, and foreign keys afterward.

Prepare durable recovery manifests while the healthy ledger exists:

```bash
python pipeline.py rebuild prepare
```

Then inspect the corpus and build a second database:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

The pilot has proven exact source-through-Stage-3 recovery for the current corpus: document identities, current source-file SHA identities, Scout snapshot identities, container relationships, core document metadata, and successful Stage 3 identities can be reconstructed from the durable evidence without contacting REGDOCS or Azure again.

Stage 4 normalization rows are intentionally not reconstructed from SQLite recovery metadata. Normalize is local and can be rerun from the recovered Stage 3 artifacts. Stage 5 can then be republished from Stage 4.

See [docs/DATABASE_RECOVERY.md](docs/DATABASE_RECOVERY.md).

## Stage runbooks

- [Scout](docs/stages/regdocs_1_scout.md)
- [Download](docs/stages/regdocs_2_download.md)
- [Azure Content Understanding](docs/stages/regdocs_3_azure.md)
- [Docling](docs/stages/regdocs_3_docling.md)
- [Normalize](docs/stages/regdocs_4_normalize.md)
- [Index](docs/stages/regdocs_5_index.md)

## Azure Content Understanding cost visibility

REGDOCS reads usage meters from saved Azure results rather than estimating from guessed page counts. Dollar estimates require the rates applicable to the subscription:

```text
REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD
REGDOCS_AZURE_CU_BASIC_PER_1000_USD
REGDOCS_AZURE_CU_STANDARD_PER_1000_USD
```

Inspect them with:

```bash
python pipeline.py cost rates
python pipeline.py cost azure
```

Docling is local compute and reports Azure service cost as `n/a`.

## Pilot verification

This pilot intentionally has no GitHub Actions CI workflow or dedicated repository test suite. Use the real operational checks instead:

```bash
python pipeline.py db verify
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare --source database/regdocs.db --rebuilt database/regdocs.rebuilt.db
python pipeline.py status
```

For structural cleanup, smoke-test the public stage entry points with their read-only modes (`--version`, `--diagnostics`, `--status`, or `--dry-run` as appropriate) before starting a long or billable run.

## Principles

- preserve public REGDOCS evidence and source-document identity;
- use SHA-256 as the definitive downloaded-file version identity;
- preserve expensive Stage 3 artifacts and avoid accidental Azure rebilling;
- keep Normalize and Azure AI Search rebuildable rather than over-engineering recovery for cheap derivatives;
- never fabricate missing recovery facts;
- keep credentials out of command lines, logs, and version control;
- keep the pilot simple and only add complexity when it proves useful.

Future work is tracked in [ROADMAP.md](ROADMAP.md) and the focused plans under `roadmap/`.

## Disclaimer

This repository is not affiliated with or endorsed by the Canada Energy Regulator. REGDOCS remains the authoritative public access system for the source regulatory records.
