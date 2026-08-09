# REGDOCS Atlas

REGDOCS Atlas is a proof-of-concept pipeline for collecting public Canada Energy Regulator REGDOCS records, preserving source evidence, downloading source files, analyzing documents, normalizing them, and publishing searchable chunks.

Everything in this repository is POC work until explicitly declared otherwise. The project version stays at **0.0.1** until it is intentionally changed.

For the complete CLI syntax, every public switch, Azure environment variables, safety labels, and examples, see **[SYNTAX.md](SYNTAX.md)**.

## Preserve the durable workspace

The expensive and authoritative artifacts are deliberately outside Git:

```text
workspace/1_scout/       raw REGDOCS evidence + recovery manifests + coverage
workspace/2_download/    source files + metadata sidecars
workspace/3_analyze/     Azure/Docling analysis artifacts + Stage 3 manifests
workspace/4_normalize/   local normalized JSONL
workspace/5_index/       index run metadata

database/regdocs.db      operational SQLite ledger
```

`/workspace/` and `/database/` are ignored by Git. Normal commits, pulls, and repository refactors do not version or replace those files.

**Do not use `git clean -fdx` in this checkout.** It deletes ignored files and can destroy the expensive Azure analysis artifacts. Do not remove `workspace/` as part of repository cleanup.

The POC recovery boundary is intentionally:

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
├── pipeline.py              one public action-oriented CLI
├── regdocs_atlas/           all application code
├── requirements.txt         one Python dependency set
├── README.md                architecture / POC operating rules
├── SYNTAX.md                complete command, ENV, and switch reference
├── RELEASE_NOTES.md         consolidated 0.0.1 POC baseline
├── VERSION                  stays 0.0.1 until explicitly changed
├── database/                ignored local ledger/backups
└── workspace/               ignored durable artifacts
```

Inside `regdocs_atlas/`, the split is intentional:

```text
regdocs_atlas/
├── cli.py, paths.py, version.py, costs.py   shared application infrastructure
├── db/                                      SQLite schema/migrations/ledger helpers
├── runtime/                                 locks, atomic I/O, presentation helpers
├── artifacts/                               artifact inventory/recovery planning
├── stages/                                  executable Stage 1-5 implementations/workers
├── scout_*.py                               Scout manifests/coverage/recovery helpers
├── analysis_manifests.py                    Stage 3 durable analysis ledger export
├── rebuild*.py, flatten.py                  disk -> SQLite recovery/flattening
└── ...                                      other cross-stage orchestration helpers
```

`stages/` is therefore for code that **executes a pipeline stage**. Files at the package root are shared infrastructure or recovery/provenance logic used across stage boundaries. A Scout-specific recovery helper can live at the package root because it is part of the recovery system, not the normal Stage 1 crawler process.

There is no second `pipeline/` implementation tree and no compatibility-launcher layer. `pipeline.py` launches the packaged stage implementations while retaining subprocess isolation for long-running/crash-prone work.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Azure configuration is documented in [SYNTAX.md](SYNTAX.md#azure-environment-variables). The pipeline reads the process environment directly; it does not automatically load a `.env` file.

## Versioning during the POC

`VERSION` is the only project/release version and remains `0.0.1` until explicitly changed.

Do not confuse that with compatibility identities embedded in durable artifacts. Values such as Scout/Download parser identities, Azure analyzer/API identity, Docling projection identity, and Normalize parser/config identity must remain specific enough to tell whether an existing artifact is compatible with current code. Those identities should not be reset merely to make display versions look uniform.

The public CLI always reports the project version:

```bash
python pipeline.py version
# 0.0.1
```

## CLI safety model

A bare stage name never performs work. It prints help and exits:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
```

Actual work requires an explicit action:

```bash
python pipeline.py scout run --start-date YYYY-MM-DD --end-date YYYY-MM-DD
python pipeline.py download run
python pipeline.py analyze azure run --all
python pipeline.py analyze docling run --max-documents 1
python pipeline.py normalize run --provider azure
python pipeline.py index publish
```

Safe planning/status actions are explicit too:

```bash
python pipeline.py scout coverage
python pipeline.py download plan
python pipeline.py analyze azure plan --all
python pipeline.py analyze docling status
python pipeline.py normalize plan --provider azure --limit 100
python pipeline.py index plan
```

Scout uses `probe` rather than `plan` because its preview mode still contacts REGDOCS and preserves request evidence:

```bash
python pipeline.py scout probe \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --limit 5
```

See [SYNTAX.md](SYNTAX.md) before running unfamiliar actions.

## Scout date coverage

Completed Scout date ranges are durable independently of SQLite run history:

```text
workspace/1_scout/manifests/coverage.json
```

Show or refresh the watermark with:

```bash
python pipeline.py scout coverage
```

Normal Scout acquisition never chooses a date range automatically. Both `--start-date` and `--end-date` are required for `scout run` and `scout probe`.

Coverage advances only from real successful Scout acquisition runs that completed the base search, had zero failed base-search pages, and passed the post-run audit. `rebuild prepare` also refreshes the coverage manifest.

## Database recovery and flattening

The durable artifacts can reconstruct a new SQLite ledger without re-crawling REGDOCS, re-downloading source files, or resubmitting successful Azure Content Understanding analyses.

Useful recovery checks:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild prepare
```

A normal side-by-side rebuild:

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

The Stage 1-3 disaster-recovery target is:

```text
source_and_stage3_equivalent: true
```

For a clean POC operational baseline with historical runs/errors/recovery bookkeeping removed:

```bash
python pipeline.py rebuild create --flat
```

Flat mode first performs the same manifest-backed Stage 1-3 reconstruction. It only strips history after an exact successful rebuild, never overwrites the active database, and never contacts REGDOCS or Azure. Stage 4 normalization rows are intentionally absent because Normalize is locally rebuildable.

## Azure cost protection

`workspace/3_analyze/` is the expensive boundary. Preserve it.

Before any Azure Content Understanding run:

```bash
python pipeline.py analyze azure plan --all
```

Only this explicit action permits billable Stage 3 work:

```bash
python pipeline.py analyze azure run --all
```

The analyzer uses current source SHA identity plus successful analysis state/artifacts to avoid unnecessarily resubmitting work. Azure endpoints, authentication, API/analyzer defaults, Azure AI Search settings, and optional cost-rate variables are documented in [SYNTAX.md](SYNTAX.md#azure-environment-variables).

Azure rates remain configurable via environment variables and can be inspected with:

```bash
python pipeline.py cost rates
python pipeline.py cost azure
```

## Documentation roles

- `README.md` — architecture, package layout, recovery boundary, POC operating rules.
- `SYNTAX.md` — authoritative public CLI syntax, environment variables, switches, safety labels, and examples.
- `RELEASE_NOTES.md` — one consolidated description of the current `0.0.1` POC, not a history of internal iteration.

No additional docs/roadmap tree is needed unless the project stops being a POC.

## POC operating rules

- one public command: `python pipeline.py ...`;
- bare stage names never run work;
- explicit actions are required for network/mutating stage operations;
- one application package: `regdocs_atlas/`;
- one dependency file: `requirements.txt`;
- no GitHub Actions CI and no dedicated automated test suite for the POC;
- no version bump unless explicitly requested;
- preserve Scout evidence, downloaded source files, and Stage 3 analyzer artifacts;
- never fabricate missing recovery facts;
- treat Normalize and Azure AI Search as rebuildable derivatives.
