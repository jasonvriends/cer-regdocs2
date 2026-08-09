# REGDOCS Atlas

REGDOCS Atlas is a proof-of-concept pipeline for collecting public Canada Energy Regulator REGDOCS records, preserving source evidence, downloading source files, analyzing documents, normalizing them, and publishing searchable chunks.

Everything in this repository is POC work until explicitly declared otherwise. The project version stays at **0.0.1** until it is intentionally changed.

## The important thing: preserve `workspace/`

The expensive and authoritative artifacts are deliberately outside Git:

```text
workspace/1_scout/       raw REGDOCS evidence + recovery manifests
workspace/2_download/    source files + metadata sidecars
workspace/3_analyze/     Azure/Docling analysis artifacts + Stage 3 manifests
workspace/4_normalize/   local normalized JSONL
workspace/5_index/       index run metadata

database/regdocs.db      operational SQLite ledger
```

`/workspace/` and `/database/` are ignored by Git. Normal commits, pulls, and repository refactors do not version or replace those files.

**Do not use `git clean -fdx` in this checkout.** That command deletes ignored files and can destroy the expensive workspace artifacts. Do not `rm -rf workspace` as part of code cleanup.

The recovery boundary for the POC is intentionally:

```text
Stage 1 Scout evidence       durable
Stage 2 downloaded files     durable
Stage 3 analyzer artifacts   durable / expensive to reproduce
SQLite ledger                rebuildable from Stages 1-3
Stage 4 Normalize            rerun locally from Stage 3
Stage 5 Azure AI Search      republish from Stage 4
```

The current corpus has been side-by-side rebuilt and compared successfully through Stage 3 without another REGDOCS crawl, download pass, or Azure analysis request.

## Repository layout

```text
.
├── pipeline.py              one public CLI
├── regdocs_atlas/           all application code
│   ├── cli.py
│   ├── db/
│   ├── runtime/
│   ├── artifacts/
│   ├── stages/
│   ├── rebuild*.py
│   ├── scout_manifests.py
│   ├── analysis_manifests.py
│   └── ...
├── requirements.txt
├── VERSION
├── RELEASE_NOTES.md
├── database/                ignored local state
└── workspace/               ignored durable local artifacts
```

There is no second `pipeline/` implementation tree and no compatibility-launcher layer. `pipeline.py` launches the packaged stage implementations directly while retaining subprocess isolation for the long-running/crash-prone stages.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Main commands

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize --provider azure
python pipeline.py normalize --provider docling
python pipeline.py index
```

Useful read-only checks:

```bash
python pipeline.py version
python pipeline.py status
python pipeline.py diagnostics
python pipeline.py db verify

python pipeline.py download --dry-run --limit 1
python pipeline.py analyze azure --dry-run --limit 1
python pipeline.py normalize --provider azure --dry-run --limit 1
python pipeline.py index --dry-run --limit 1
```

Public `--version` output is always the repository POC version (`0.0.1`). Internal parser/analyzer identifiers remain embedded in artifacts where they are required for reproducibility; they are not independent product releases.

## Database and recovery

Schema migration:

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify
```

Prepare/verify durable recovery manifests:

```bash
python pipeline.py rebuild prepare
```

After a previous full Scout verification, a faster refresh is:

```bash
python pipeline.py rebuild prepare --no-verify-raw
```

Inspect and rebuild beside the working database:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan

python pipeline.py rebuild create \
  --output database/regdocs.rebuilt.db

python pipeline.py rebuild verify \
  --db database/regdocs.rebuilt.db

python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

A successful Stage 1-3 comparison is the disaster-recovery target. Stage 4 normalization rows are intentionally not reconstructed from SQLite recovery; Normalize is local and can be rerun from the preserved Stage 3 artifacts. Azure AI Search is likewise a derivative publication layer.

## Azure cost protection

Before any Azure rerun, use the dry run and inspect the ledger/artifact state:

```bash
python pipeline.py analyze azure --dry-run --limit 10
python pipeline.py cost azure
```

The analyzer uses current source SHA identity plus successful analysis state/artifacts to avoid unnecessarily resubmitting work. Preserve `workspace/3_analyze/` because it contains the expensive provider outputs.

Azure Content Understanding rates are configured rather than hard-coded:

```text
REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD
REGDOCS_AZURE_CU_BASIC_PER_1000_USD
REGDOCS_AZURE_CU_STANDARD_PER_1000_USD
```

```bash
python pipeline.py cost rates
python pipeline.py cost azure
```

## POC operating rules

- one public command: `python pipeline.py ...`;
- one package: `regdocs_atlas/`;
- one dependency file: `requirements.txt`;
- one documentation source: this README;
- no GitHub Actions CI and no dedicated test suite for the POC;
- no version bump unless explicitly requested;
- preserve acquisition evidence, downloaded source files, and Stage 3 analyzer artifacts;
- never fabricate missing recovery facts;
- treat Normalize and Azure AI Search as rebuildable derivatives.

`RELEASE_NOTES.md` is a single consolidated description of the current 0.0.1 POC, not a history of internal iteration.
