# REGDOCS Atlas — Command Guide

> **Start here if you have never used the pipeline before.**
>
> Every command is run from the repository root and starts with:
>
> ```bash
> python pipeline.py
> ```
>
> You do **not** need to know Python. Treat each command below like a normal terminal command.

---

## 1. Before you run anything

### Open the repository

```bash
cd ~/repos/cer-regdocs2
```

### Activate the Python environment

Linux / WSL:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

If the environment has not been created yet:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

### How to read commands in this guide

When you see:

```text
PATH
ID
N
SECONDS
YYYY-MM-DD
```

replace it with your real value.

For example:

```bash
python pipeline.py download plan --document-id 4657417
```

Do not type angle brackets such as `<ID>`.

---

# 2. Safety first

A stage name by itself is safe. It only prints help.

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
```

The pipeline requires an explicit action such as `plan`, `run`, `publish`, or `repair` before it performs real work.

## Cost / write legend

| Label | Meaning |
|---|---|
| **READ ONLY** | Does not intentionally change pipeline data |
| **NETWORK** | Contacts REGDOCS or Azure |
| **DB WRITE** | Can change `database/regdocs.db` |
| **WORKSPACE WRITE** | Can create or replace files under `workspace/` |
| **AZURE CU COST** | Can submit billable Azure Content Understanding work |
| **AZURE SEARCH** | Contacts Azure AI Search |

> **Important:** preserve `workspace/3_analyze/`. Successful Azure analysis artifacts are expensive to recreate.

---

# 3. The normal workflow

For most runs, use these commands in this order.

## Check what exists

```bash
python pipeline.py status
python pipeline.py scout coverage
```

## Stage 2 — preview downloads

```bash
python pipeline.py download plan
```

## Stage 3 — preview Azure work

```bash
python pipeline.py analyze azure plan --all
```

## Stage 4 — preview normalization

```bash
python pipeline.py normalize plan --provider azure
```

## Stage 5 — preview the search-index payload

```bash
python pipeline.py index plan
```

Then run only the stages that actually have work:

```bash
python pipeline.py scout run --start-date 2026-08-01 --end-date 2026-08-09
python pipeline.py download run
python pipeline.py analyze azure run --all
python pipeline.py normalize run --provider azure --concurrency 4
python pipeline.py index publish
```

---

# 4. Help, version, status, diagnostics, and cost

## Help

```bash
python pipeline.py help
python pipeline.py help scout
python pipeline.py help download
python pipeline.py help analyze azure
python pipeline.py help analyze docling
python pipeline.py help normalize
python pipeline.py help index
```

`-h` and `--help` also work in the equivalent positions.

## Version

```bash
python pipeline.py version
```

The POC release remains `0.0.1` until intentionally changed.

## Overall status

```bash
python pipeline.py status
python pipeline.py status --json
```

| Switch | Meaning |
|---|---|
| `--json` | Print machine-readable JSON instead of the friendly text view |

**Safety:** READ ONLY.

## Diagnostics

```bash
python pipeline.py diagnostics
```

Shows the Python executable, project paths, artifact inventory, migration state, lock paths, and Azure cost configuration.

**Safety:** READ ONLY.

## Azure Content Understanding cost

Latest Azure analysis run:

```bash
python pipeline.py cost azure
```

A specific run:

```bash
python pipeline.py cost azure --run-id 123
```

| Switch | Meaning |
|---|---|
| `--run-id N` | Show the estimate for one recorded Azure run |

Show configured rates:

```bash
python pipeline.py cost rates
```

---

# 5. Azure environment variables

The pipeline reads environment variables from the shell that launches it. It does **not** automatically load a `.env` file.

Never commit keys or secrets to Git.

## Azure Content Understanding

Used by:

```bash
python pipeline.py analyze azure plan ...
python pipeline.py analyze azure run ...
```

| Variable | Required? | Meaning | Default |
|---|---:|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Yes for real Azure work unless `--endpoint` is supplied | Azure Content Understanding endpoint | none |
| `CONTENTUNDERSTANDING_KEY` | No | API key | `DefaultAzureCredential` when omitted |
| `CONTENTUNDERSTANDING_API_VERSION` | No | Content Understanding API version | `2025-11-01` |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | No | Analyzer name | `prebuilt-layout` |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | No | Seconds between polling attempts | `3` |

Example:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://YOUR-RESOURCE.cognitiveservices.azure.com/"
export CONTENTUNDERSTANDING_KEY="YOUR-KEY"
```

If the key is omitted, Azure Identity uses `DefaultAzureCredential`. That can use an authenticated `az login` session or normal service-principal / managed-identity configuration.

### Optional cost-rate variables

These affect **estimates only**. They do not change Azure requests.

```bash
export REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD="..."
export REGDOCS_AZURE_CU_BASIC_PER_1000_USD="..."
export REGDOCS_AZURE_CU_STANDARD_PER_1000_USD="..."
```

Inspect them with:

```bash
python pipeline.py cost rates
```

## Azure AI Search

Used by:

```bash
python pipeline.py index publish
python pipeline.py index query "..."
```

| Variable | Required? | Meaning | Default |
|---|---:|---|---|
| `AZURE_SEARCH_ENDPOINT` | Yes for publish/query unless `--endpoint` is supplied | Search endpoint | none |
| `AZURE_SEARCH_ADMIN_KEY` | No | Search key | `DefaultAzureCredential` when omitted |
| `AZURE_SEARCH_INDEX_NAME` | No | Index name | `regdocs-chunks` |

Example:

```bash
export AZURE_SEARCH_ENDPOINT="https://YOUR-SERVICE.search.windows.net"
export AZURE_SEARCH_ADMIN_KEY="YOUR-KEY"
export AZURE_SEARCH_INDEX_NAME="regdocs-chunks"
```

---

# 6. Stage 1 — Scout REGDOCS metadata

Scout discovers REGDOCS records and preserves raw evidence.

## Quick commands

```bash
python pipeline.py scout coverage
python pipeline.py scout status
python pipeline.py scout probe --start-date 2026-08-09 --end-date 2026-08-09 --limit 5
python pipeline.py scout run --start-date 2026-08-01 --end-date 2026-08-09
```

---

## 6.1 `scout coverage`

Shows and refreshes the durable completed-date watermark.

```bash
python pipeline.py scout coverage
```

Alternate database:

```bash
python pipeline.py scout coverage --db database/other.db
```

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | SQLite ledger used to discover qualifying completed Scout runs | `database/regdocs.db` |

**Safety:** no REGDOCS request; writes only the local coverage manifest.

---

## 6.2 `scout status`

```bash
python pipeline.py scout status
python pipeline.py scout status --json
```

| Switch | Meaning |
|---|---|
| `--json` | Print JSON status |
| `--db PATH` | Use a different SQLite ledger |
| `--progress-file PATH` | Read a different live-progress file |

**Safety:** READ ONLY.

---

## 6.3 `scout audit`

Runs the read-only Scout integrity audit.

```bash
python pipeline.py scout audit
```

It checks SQLite integrity, container relationships, raw snapshot references, gzip sizes, and hashes.

| Switch | Meaning |
|---|---|
| `--db PATH` | Audit another SQLite ledger |

**Safety:** READ ONLY; no REGDOCS request.

---

## 6.4 `scout schema`

Checks that the Scout/base ledger schema is present.

```bash
python pipeline.py scout schema
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Check another SQLite ledger |

**Safety:** READ ONLY.

---

## 6.5 `scout probe`

Use this before a real Scout run when testing a date range.

```bash
python pipeline.py scout probe \
  --start-date 2026-08-09 \
  --end-date 2026-08-09 \
  --limit 5
```

`probe` **does contact REGDOCS**. It parses the selected range without updating the main `documents` records, but run/error/raw-snapshot evidence is still preserved.

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE. No Azure cost.

---

## 6.6 `scout run`

Normal Scout acquisition:

```bash
python pipeline.py scout run \
  --start-date 2026-08-01 \
  --end-date 2026-08-09
```

Both dates are required by the public CLI.

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE. No Azure cost.

### All public Scout run/probe switches

| Switch | Meaning | Default |
|---|---|---|
| `--start-date YYYY-MM-DD` | First filing date to search | required |
| `--end-date YYYY-MM-DD` | Last filing date to search | required |
| `--db PATH` | SQLite ledger | project database |
| `--raw-dir PATH` | Raw REGDOCS evidence directory | Stage 1 workspace |
| `--progress-file PATH` | Live progress JSON | Stage 1 workspace |
| `--log-file PATH` | Scout log | Stage 1 workspace |
| `--lock-file PATH` | Stage 1 exclusive lock | project lock |
| `--page-size 20|50|100|200` | REGDOCS search-page size | `200` |
| `--limit N` | Stop after at most N base search records | unlimited |
| `--facets all` | Collect all available facet categories | `all` |
| `--facets none` | Skip facet enrichment | — |
| `--facets "A,B"` | Collect only named comma-separated facet categories | — |
| `--expand-containers` | Traverse explicit Folder / Compound Document members | on |
| `--no-expand-containers` | Do not traverse containers | — |
| `--expand-compounds` | Alias of `--expand-containers` | — |
| `--no-expand-compounds` | Alias of `--no-expand-containers` | — |
| `--container-max-depth N` | Maximum nested Folder/Compound depth | `20` |
| `--container-max-items N` | Maximum unique containers expanded in one run | `10000` |
| `--details` | Fetch each selected item's own detail page | on |
| `--no-details` | Skip detail-page enrichment | — |
| `--detail-refresh-days N` | Treat existing detail data as fresh for N days | `30` |
| `--refresh-details` | Refresh details even if still considered fresh | off |
| `--concurrency N` | Number of Scout request workers | `1` |
| `--min-delay SECONDS` | Minimum global request-start delay | `2.0` |
| `--max-delay SECONDS` | Maximum global request-start delay | `4.0` |
| `--max-retries N` | Maximum HTTP retries | `4` |
| `--retry-backoff FACTOR` | Retry backoff multiplier | `2.0` |
| `--verbose` | More detailed logging | off |
| `--force-lock` | Remove the Stage 1 lock even if present; use only after confirming Scout is not running | off |

---

## 6.7 `scout repair`

Repairs known Folder and Compound Document records already in SQLite without rerunning the normal date search.

```bash
python pipeline.py scout repair
```

Typical bounded repair:

```bash
python pipeline.py scout repair \
  --container-max-depth 20 \
  --container-max-items 10000
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

### Public repair switches

`repair` uses the same operational controls as Scout, except it does not need a date range or facet-search settings.

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--raw-dir PATH` | Raw evidence directory |
| `--progress-file PATH` | Live progress file |
| `--log-file PATH` | Scout log |
| `--lock-file PATH` | Stage lock |
| `--expand-containers` / `--no-expand-containers` | Enable/disable recursive container traversal; repair requires it enabled |
| `--expand-compounds` / `--no-expand-compounds` | Aliases |
| `--container-max-depth N` | Maximum nesting depth |
| `--container-max-items N` | Maximum containers processed |
| `--details` / `--no-details` | Reuse/fetch container detail information where applicable |
| `--detail-refresh-days N` | Freshness window |
| `--refresh-details` | Force detail refresh |
| `--concurrency N` | Request concurrency |
| `--min-delay SECONDS` | Minimum request delay |
| `--max-delay SECONDS` | Maximum request delay |
| `--max-retries N` | HTTP retry count |
| `--retry-backoff FACTOR` | Retry multiplier |
| `--verbose` | More logs |
| `--force-lock` | Force lock removal |

---

# 7. Stage 2 — Download source files

Stage 2 downloads, validates, hashes, versions, and optionally writes metadata sidecars.

## Quick commands

```bash
python pipeline.py download status
python pipeline.py download plan
python pipeline.py download run
```

---

## 7.1 `download status`

```bash
python pipeline.py download status
python pipeline.py download status --json
```

| Switch | Meaning |
|---|---|
| `--json` | JSON output |
| `--db PATH` | Alternate ledger |

**Safety:** READ ONLY.

---

## 7.2 `download plan`

Preview what would be downloaded.

```bash
python pipeline.py download plan
python pipeline.py download plan --limit 20
python pipeline.py download plan --document-id 4657417
```

No network request is made.

### Selection switches

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--downloads PATH` | Download directory |
| `--output-dir PATH` | Alias of `--downloads` |
| `--document-id ID` | Select one document; repeat for multiple IDs |
| `--limit N` | Select at most N downloads |
| `--include-html` | Include known HTML records |
| `--force` | Include records already marked successful |
| `--retry-failed` | Include records marked final failure |

**Safety:** READ ONLY.

---

## 7.3 `download sidecars`

Writes deterministic `<document-id>.metadata.json` files from the current database and source files.

```bash
python pipeline.py download sidecars
python pipeline.py download sidecars --limit 100
python pipeline.py download sidecars --sidecar-dir /tmp/regdocs-sidecars
```

Preview the sidecar destinations without writing them:

```bash
python pipeline.py download sidecars --dry-run
```

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--downloads PATH` / `--output-dir PATH` | Source-file directory |
| `--document-id ID` | One document; repeatable |
| `--limit N` | Maximum sidecars |
| `--sidecar-dir PATH` | Put sidecars in a separate directory |
| `--dry-run` | Preview only |
| `--force-lock` | Force Stage 2 lock removal after confirming no downloader is active |

**Safety:** WORKSPACE WRITE unless `--dry-run`; no network.

---

## 7.4 `download run`

```bash
python pipeline.py download run
```

Bound the first test:

```bash
python pipeline.py download run --limit 25
```

Retry final failures:

```bash
python pipeline.py download run --retry-failed --limit 25
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

### All public download-run switches

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | SQLite ledger | project DB |
| `--downloads PATH` | Download directory | Stage 2 workspace |
| `--output-dir PATH` | Alias of `--downloads` | — |
| `--document-id ID` | Download only this document; repeatable | all eligible |
| `--limit N` | Maximum selected records | unlimited |
| `--include-html` | Include known HTML documents | off |
| `--force` | Redownload successful/current records | off |
| `--retry-failed` | Retry `FAILED_FINAL` records | off |
| `--attempts N` | Maximum HTTP attempts per document in this run | `4` |
| `--concurrency N` | Active download workers | `1` |
| `--min-delay SECONDS` | Minimum global request-start delay | `3.0` |
| `--max-delay SECONDS` | Maximum global request-start delay | `6.0` |
| `--connect-timeout SECONDS` | Connection timeout | `30.0` |
| `--read-timeout SECONDS` | Response-read timeout | `300.0` |
| `--max-file-size-mb MB` | Refuse responses larger than this | `2048.0` |
| `--reconcile` | Reconcile database records with files already on disk | on |
| `--no-reconcile` | Skip reconciliation | — |
| `--verify-existing` | Re-hash existing files while reconciling | off |
| `--archive-replaced` | Keep replaced versions under `_versions` | on |
| `--no-archive-replaced` | Delete replaced versions instead | — |
| `--sidecars` | Write metadata sidecars | wrapper default for normal run |
| `--write-sidecars` | Alias of `--sidecars` | — |
| `--no-sidecars` | Public wrapper option that suppresses automatic sidecar creation | off |
| `--sidecar-dir PATH` | Separate sidecar output directory | beside source files |
| `--partial-max-age-hours HOURS` | Remove stale `.part` files older than this | `24.0` |
| `--audit-dir PATH` | Stage 2 run/progress/log directory | Stage 2 workspace |
| `--lock-file PATH` | Stage 2 exclusive lock | project lock |
| `--verbose` | More detailed logging | off |
| `--force-lock` | Force lock removal after confirming no downloader is running | off |

---

# 8. Stage 3A — Azure Content Understanding

> **This is the billable stage.**
>
> Always run `plan` before `run`.

The Azure supervisor is intentionally single-threaded and launches one isolated child per document. Automatic application-level Azure resubmission retries are disabled.

---

## 8.1 Choose the scope

Every Azure `plan` or `run` must contain exactly one explicit scope:

```text
--all
--limit N
--document-id ID
```

Examples:

```bash
python pipeline.py analyze azure plan --all
python pipeline.py analyze azure plan --limit 10
python pipeline.py analyze azure plan --document-id 4657417
```

---

## 8.2 `analyze azure plan`

```bash
python pipeline.py analyze azure plan --all
```

Selects candidates and exercises the local dry-run path without submitting Content Understanding analysis.

**Safety:** no billable Azure submission.

---

## 8.3 `analyze azure run`

One document:

```bash
python pipeline.py analyze azure run --document-id 4657417
```

A bounded batch:

```bash
python pipeline.py analyze azure run --limit 10
```

Everything currently eligible:

```bash
python pipeline.py analyze azure run --all
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE, **AZURE CU COST**.

### All public Azure switches

| Switch | Meaning | Default |
|---|---|---|
| `--all` | Select every currently eligible document | — |
| `--limit N` | Select at most N eligible documents | — |
| `--document-id ID` | Select one REGDOCS document | — |
| `--db PATH` | SQLite ledger | project DB |
| `--endpoint URL` | Azure Content Understanding endpoint | `CONTENTUNDERSTANDING_ENDPOINT` |
| `--key KEY` | Azure API key | `CONTENTUNDERSTANDING_KEY`, otherwise Azure Identity |
| `--api-version VERSION` | Content Understanding API version | env, then `2025-11-01` |
| `--polling-interval SECONDS` | Polling interval | env, then `3` |
| `--download-dir PATH` | Stage 2 source-file directory | Stage 2 workspace |
| `--output-dir PATH` | Azure analysis artifact directory | Stage 3 workspace |
| `--lock-file PATH` | Shared Stage 3 lock | project lock |
| `--force-lock` | Force lock removal after confirming no analyzer is running | off |
| `--state-file PATH` | Azure supervisor state file | Stage 3 Azure state |
| `--worker-sleep-seconds SECONDS` | Pause between isolated document workers | `0.25` |
| `--analyzer-id ID` | Azure analyzer | env, then `prebuilt-layout` |
| `--force` | Ignore successful current analysis state and reselect documents | off |
| `--no-reconcile-artifacts` | Do not recover/accept matching local Azure artifacts | off |
| `--no-verify-hash` | Skip source-file SHA-256 verification before analysis | off |

> `--force` can cause billable reanalysis. Use it only when you deliberately want another submission.

Large PDFs are automatically split into Azure Content-Range requests of at most 300 pages, and already completed valid range artifacts can be reused locally.

---

# 9. Stage 3B — Docling

Docling is local analysis. It does not incur Azure Content Understanding charges.

The current Docling supervisor is intentionally single-threaded and crash-isolated.

## 9.1 `analyze docling status`

```bash
python pipeline.py analyze docling status
```

Shows current documents, successful Docling analyses, remaining documents, and quarantine state.

Useful status switches:

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--download-dir PATH` | Source-file directory |
| `--output-dir PATH` | Docling artifact directory |
| `--state-file PATH` | Docling supervisor state |
| `--lock-file PATH` | Shared Stage 3 lock |
| `--analyzer-id ID` | Analyzer identity |

**Safety:** READ ONLY with respect to analysis work.

## 9.2 `analyze docling run`

Test one document first:

```bash
python pipeline.py analyze docling run --max-documents 1
```

Then a larger batch:

```bash
python pipeline.py analyze docling run --max-documents 100
```

Or process all currently selectable documents:

```bash
python pipeline.py analyze docling run
```

### All public Docling switches

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | SQLite ledger | project DB |
| `--download-dir PATH` | Source files | Stage 2 workspace |
| `--output-dir PATH` | Docling analysis output | Stage 3 Docling workspace |
| `--state-file PATH` | Durable Docling supervisor state | Stage 3 Docling state |
| `--lock-file PATH` | Shared Stage 3 lock | project lock |
| `--force-lock` | Force lock removal after confirming no Stage 3 analyzer is active | off |
| `--analyzer-id ID` | Docling analyzer identity | `docling-standard` |
| `--max-attempts N` | Maximum fresh-child attempts per document | `3` |
| `--max-documents N` | Stop after launching N child documents | unlimited |
| `--sleep-seconds SECONDS` | Pause between child launches | `0.25` |
| `--retry-quarantined` | Reset quarantined documents and try them again | off |

There is intentionally no fake `plan` action. Use `status`, then a bounded real local run.

---

# 10. Stage 4 — Normalize

Normalize converts Stage 3 analysis into deterministic JSONL for search and provenance.

It is local and safe to rerun. It does **not** contact Azure Content Understanding.

## Important output rule

A real Normalize run replaces the canonical Stage 4 JSONL with exactly the selected successful documents.

So:

```bash
python pipeline.py normalize run --provider azure --limit 10
```

creates a **10-document canonical corpus** if 10 documents succeed.

For a test, prefer:

```bash
python pipeline.py normalize plan --provider azure --limit 10
```

or use a separate `--output-dir`.

---

## 10.1 `normalize status`

```bash
python pipeline.py normalize status
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Alternate ledger |

---

## 10.2 `normalize plan`

Azure artifacts:

```bash
python pipeline.py normalize plan --provider azure
```

Docling artifacts:

```bash
python pipeline.py normalize plan --provider docling
```

Bounded check:

```bash
python pipeline.py normalize plan --provider azure --limit 100
```

The provider is required.

**Safety:** no external network; does not replace canonical Stage 4 JSONL.

---

## 10.3 `normalize run`

Normal Azure-based corpus:

```bash
python pipeline.py normalize run --provider azure
```

Use local parallelism:

```bash
python pipeline.py normalize run --provider azure --concurrency 4
```

Docling:

```bash
python pipeline.py normalize run --provider docling --concurrency 4
```

### Choosing `--concurrency`

Start with:

```bash
--concurrency 2
```

or:

```bash
--concurrency 4
```

Each worker is still an isolated child process. Final JSONL order remains deterministic even when workers finish out of order.

The safe default remains:

```bash
--concurrency 1
```

### All public Normalize switches

| Switch | Meaning | Default |
|---|---|---|
| `--provider azure|docling` | Stage 3 provider to normalize | required for `plan` / `run` |
| `--db PATH` | SQLite ledger | project DB |
| `--analysis-dir PATH` | Stage 3 analysis root | Stage 3 workspace |
| `--output-dir PATH` | Canonical normalized JSONL directory | Stage 4 workspace |
| `--document-id ID` | Normalize only this document; repeatable | all eligible |
| `--limit N` | Normalize at most N selected documents | unlimited |
| `--target-words N` | Preferred search-chunk size | `800` |
| `--max-words N` | Maximum search-chunk size before structural splitting | `1200` |
| `--concurrency N` | Maximum isolated Normalize workers at once | `1` |
| `--stop-on-error` | Stop after first failed document | off |
| `--lock-file PATH` | Stage 4 lock | project lock |
| `--force-lock` | Force lock removal after confirming Normalize is not running | off |

`--max-words` must be at least `--target-words`.

`--stop-on-error` requires `--concurrency 1` so "first failure" has an unambiguous meaning.

### Performance timing

A real Normalize run now records timing data in the run summary:

- candidate selection time;
- worker wall-clock time;
- sum of worker-process time;
- final JSONL merge time;
- total pipeline wall-clock time.

The final shard merge is streamed instead of loading complete shard files into memory.

---

# 11. Stage 5 — Azure AI Search

Stage 5 maps normalized chunks to Azure AI Search documents.

## 11.1 `index plan`

```bash
python pipeline.py index plan
python pipeline.py index plan --limit 100
python pipeline.py index plan --document-id 4657417
```

Checks the JSONL, chunk/provenance pairing, hashes, mapped payload size, and selected records.

**Safety:** no Azure Search request.

---

## 11.2 `index publish`

```bash
python pipeline.py index publish
```

Use another index for testing:

```bash
python pipeline.py index publish --index-name regdocs-chunks-poc
```

**Safety:** NETWORK, WORKSPACE WRITE, AZURE SEARCH.

### All public plan/publish switches

| Switch | Meaning | Default |
|---|---|---|
| `--normalized-dir PATH` | Stage 4 normalized input | Stage 4 workspace |
| `--output-dir PATH` | Stage 5 run metadata directory | Stage 5 workspace |
| `--endpoint URL` | Azure AI Search endpoint | `AZURE_SEARCH_ENDPOINT` |
| `--api-key KEY` | Azure Search key | `AZURE_SEARCH_ADMIN_KEY`, otherwise Azure Identity |
| `--index-name NAME` | Search index | env, then `regdocs-chunks` |
| `--document-id ID` | Select one source document; repeatable | all |
| `--limit N` | Maximum **chunks**, not source documents | unlimited |
| `--batch-size N` | Search documents per upload batch | `500`, max `1000` |
| `--max-batch-bytes BYTES` | Maximum estimated batch size | `12582912` (12 MiB) |
| `--recreate-index` | Delete and recreate the index before upload | off |

`--recreate-index` cannot be combined with `--document-id` or `--limit`.

---

## 11.3 `index query`

```bash
python pipeline.py index query "pipeline abandonment"
python pipeline.py index query "compressor station" --top 10
python pipeline.py index query "*" --top 1
```

Filter example:

```bash
python pipeline.py index query "pipeline" \
  --filter "document_id eq '4657417'"
```

### Query switches

| Switch | Meaning | Default |
|---|---|---|
| `TEXT` | Search text immediately after `query` | required |
| `--endpoint URL` | Search endpoint | environment |
| `--api-key KEY` | Search key | environment / Azure Identity |
| `--index-name NAME` | Index to query | environment / `regdocs-chunks` |
| `--top N` | Number of results shown | `5` |
| `--filter ODATA` | Azure AI Search OData filter | none |

---

# 12. Database commands

These commands operate on the SQLite ledger.

## 12.1 `db migrate`

Preview:

```bash
python pipeline.py db migrate --plan
```

Apply:

```bash
python pipeline.py db migrate
```

### Switches

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | Database to migrate | active project DB |
| `--plan` | Show what would be done without migrating | off |
| `--no-backup` | Do not create the normal safety backup | off |
| `--backup-dir PATH` | Backup directory | project DB backup directory |
| `--force-lock` | Force pipeline lock removal after confirming nothing is running | off |

A normal migration creates a database backup first.

## 12.2 `db status`

```bash
python pipeline.py db status
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Database to inspect |

## 12.3 `db verify`

```bash
python pipeline.py db verify
```

Checks migration state, schema, SQLite integrity, and foreign keys.

| Switch | Meaning |
|---|---|
| `--db PATH` | Database to verify |

---

# 13. Rebuild and flatten

The recovery boundary is intentionally:

```text
Stage 1 Scout evidence       durable
Stage 2 downloaded files     durable
Stage 3 analyzer artifacts   durable / expensive
SQLite ledger                rebuildable from Stages 1-3
Stage 4 Normalize            locally rebuildable
Stage 5 Search index         republishable
```

These commands do not contact REGDOCS, Azure Content Understanding, Docling, or Azure AI Search.

## 13.1 `rebuild inventory`

```bash
python pipeline.py rebuild inventory
```

No switches.

## 13.2 `rebuild plan`

```bash
python pipeline.py rebuild plan
```

No switches.

## 13.3 `rebuild prepare`

```bash
python pipeline.py rebuild prepare
```

Skip expensive raw verification when deliberately needed:

```bash
python pipeline.py rebuild prepare --no-verify-raw
```

### Switches

| Switch | Meaning |
|---|---|
| `--db PATH` | Source ledger |
| `--no-verify-raw` | Skip full Scout gzip/size/hash verification |
| `--no-verify-analysis` | Skip Stage 3 artifact verification |

## 13.4 `rebuild create`

Normal side-by-side rebuild:

```bash
python pipeline.py rebuild create
```

Explicit output:

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
```

Flattened operational baseline:

```bash
python pipeline.py rebuild create --flat
```

### Switches

| Switch | Meaning |
|---|---|
| `--output PATH` | New database path |
| `--flat` | Remove historical run/error/recovery bookkeeping after exact Stage 1-3 reconstruction |

Defaults:

```text
normal: database/regdocs.rebuilt.db
flat:   database/regdocs.flat.db
```

The active `database/regdocs.db` is not overwritten.

## 13.5 `rebuild verify`

```bash
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Rebuilt database to verify |

## 13.6 `rebuild compare`

```bash
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

| Switch | Meaning |
|---|---|
| `--source PATH` | Original/reference ledger |
| `--rebuilt PATH` | Reconstructed ledger |

The important recovery result is:

```text
source_and_stage3_equivalent: true
```

---

# 14. Scout recovery queue

## 14.1 Show recovery tasks

```bash
python pipeline.py recover scout
```

Only HIGH priority:

```bash
python pipeline.py recover scout --priority HIGH
```

Only IDs:

```bash
python pipeline.py recover scout --ids-only
```

### Queue switches

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--priority HIGH|NORMAL|LOW` | Filter by task priority |
| `--limit N` | Maximum tasks shown |
| `--ids-only` | Print only document IDs |

**Safety:** READ ONLY.

## 14.2 Execute Scout recovery

```bash
python pipeline.py recover scout --execute --priority HIGH --limit 100
```

### Execution switches

| Switch | Meaning | Default |
|---|---|---|
| `--execute` | Required to actually perform recovery requests | required |
| `--db PATH` | SQLite ledger | active project DB |
| `--priority HIGH|NORMAL|LOW` | Filter tasks | all |
| `--limit N` | Maximum tasks executed | unlimited |
| `--timeout SECONDS` | Per-request timeout | `60.0` |
| `--force-lock` | Force pipeline lock removal after confirming no conflicting work is active | off |

**Safety:** NETWORK, DB WRITE.

---

# 15. Common examples

## Process one document end-to-end after Scout

```bash
python pipeline.py download run --document-id 4657417
python pipeline.py analyze azure plan --document-id 4657417
python pipeline.py analyze azure run --document-id 4657417
python pipeline.py normalize plan --provider azure --document-id 4657417
```

Do **not** run a one-document Normalize against the main Stage 4 output unless you intend to replace the canonical corpus with only that selected output. For testing, use another directory:

```bash
python pipeline.py normalize run \
  --provider azure \
  --document-id 4657417 \
  --output-dir /tmp/regdocs-normalize-test
```

## Faster full local normalization

Start conservatively:

```bash
python pipeline.py normalize run --provider azure --concurrency 2
```

Then try:

```bash
python pipeline.py normalize run --provider azure --concurrency 4
```

Compare the timing summary before increasing further.

## Retry only failed downloads

```bash
python pipeline.py download run --retry-failed
```

## Test Azure without accidentally analyzing everything

```bash
python pipeline.py analyze azure plan --limit 5
```

Then, only after reviewing that output:

```bash
python pipeline.py analyze azure run --limit 5
```

---

# 16. What should be backed up?

The important local data is:

```text
workspace/1_scout/       raw REGDOCS evidence
workspace/2_download/    downloaded source files + metadata
workspace/3_analyze/     Azure/Docling analysis artifacts
database/regdocs.db      operational SQLite ledger
```

Stage 4 and Stage 5 can be recreated:

```text
workspace/4_normalize/
workspace/5_index/
```

The SQLite ledger itself can also be reconstructed from the durable Stage 1-3 evidence.

> Do not run `git clean -fdx` in a checkout containing the durable workspace. It can delete ignored `workspace/` and `database/` content.

---

# 17. Final safety checklist

Before a large Azure run:

```bash
python pipeline.py status
python pipeline.py analyze azure plan --all
python pipeline.py cost rates
```

Before replacing the full normalized corpus:

```bash
python pipeline.py normalize plan --provider azure
```

Before publishing search:

```bash
python pipeline.py index plan
```

If a lock exists unexpectedly, do not immediately use `--force-lock`. First confirm that the corresponding pipeline process is no longer running.
