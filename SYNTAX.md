# REGDOCS Atlas — Command Guide

This is the detailed command reference for REGDOCS Atlas.

If you are trying to understand what the project does, start with **[README.md](README.md)**. Come back here when you need an exact command, switch, environment variable, or safety note.

Every public command starts with:

```bash
python pipeline.py
```

You do not need to know Python to use the pipeline.

---

# 1. Before you run a command

## Open the repository

Example:

```bash
cd ~/repos/cer-regdocs2
```

## Activate the Python environment

Linux / WSL:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

If the environment does not exist yet:

```bash
python -m venv .venv
python -m pip install -r regdocs_atlas/requirements.txt
```

## How to read placeholders

When this guide shows:

```text
ID
N
PATH
SECONDS
YYYY-MM-DD
```

replace it with your real value.

For example:

```bash
python pipeline.py download plan --document-id 4657417
```

Do not type angle brackets around the value.

---

# 2. Safety labels

The guide uses these labels:

| Label | Meaning |
|---|---|
| **READ ONLY** | Does not intentionally change pipeline data |
| **NETWORK** | Contacts REGDOCS or an Azure service |
| **DB WRITE** | Can change the SQLite ledger |
| **WORKSPACE WRITE** | Can create or replace files under `workspace/` |
| **AZURE CU COST** | Can submit billable Azure Content Understanding work |
| **AZURE SEARCH** | Contacts Azure AI Search |

## Important safety rules

1. Preserve `workspace/3_analyze/`. Azure analysis can be expensive to recreate.
2. Do not run `git clean -fdx` in a checkout that contains the working `workspace/` or `database/` directories.
3. Use planning commands before large runs.
4. Do not use `--force-lock` until you have confirmed the related process is no longer running.
5. Be especially careful with `analyze azure run`, `normalize run`, and `index publish` because they can create cost or replace important output.

---

# 3. The normal workflow

Most operators will use the stages in this order:

```text
Scout → Download → Analyze → Normalize → Index
```

A safe sequence is:

```bash
python pipeline.py status
python pipeline.py scout coverage
python pipeline.py download plan
python pipeline.py analyze azure plan --all
python pipeline.py normalize plan --provider azure
python pipeline.py index plan
```

Then run only the stages that actually need work:

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

`-h` and `--help` also work in equivalent positions.

## Version

```bash
python pipeline.py version
```

The value comes from the repository `VERSION` file.

## Overall status

Friendly text:

```bash
python pipeline.py status
```

Machine-readable JSON:

```bash
python pipeline.py status --json
```

| Switch | Meaning |
|---|---|
| `--json` | Print JSON instead of the friendly text view |

**Safety:** READ ONLY.

## Diagnostics

```bash
python pipeline.py diagnostics
```

Shows information such as:

- Python executable and version;
- project paths;
- artifact inventory;
- database migration state;
- lock paths; and
- Azure cost-rate configuration.

**Safety:** READ ONLY.

## Azure Content Understanding cost information

Latest Azure analysis run:

```bash
python pipeline.py cost azure
```

Specific recorded run:

```bash
python pipeline.py cost azure --run-id 123
```

Show the configured estimate rates:

```bash
python pipeline.py cost rates
```

| Switch | Meaning |
|---|---|
| `--run-id N` | Show the cost snapshot for one recorded Azure run |

These commands report estimates from recorded usage and configured rates. Azure billing remains the final source for actual charges.

---

# 5. Azure environment variables

REGDOCS Atlas reads environment variables from the shell that launches it. It does **not** automatically load a `.env` file.

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
| `CONTENTUNDERSTANDING_KEY` | No | Azure API key | `DefaultAzureCredential` when omitted |
| `CONTENTUNDERSTANDING_API_VERSION` | No | Content Understanding API version | `2025-11-01` |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | No | Analyzer name | `prebuilt-layout` |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | No | Seconds between polling attempts | `3` |

Example:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://YOUR-RESOURCE.cognitiveservices.azure.com/"
export CONTENTUNDERSTANDING_KEY="YOUR-KEY"
```

If `CONTENTUNDERSTANDING_KEY` is not set, Azure Identity uses `DefaultAzureCredential`. That can use methods such as an authenticated `az login`, a service principal, or managed identity.

### Optional Azure cost-rate variables

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
| `AZURE_SEARCH_INDEX_NAME` | No | Search index name | `regdocs-chunks` |

Example:

```bash
export AZURE_SEARCH_ENDPOINT="https://YOUR-SERVICE.search.windows.net"
export AZURE_SEARCH_ADMIN_KEY="YOUR-KEY"
export AZURE_SEARCH_INDEX_NAME="regdocs-chunks"
```

---

# 6. Stage 1 — Scout

Scout searches the public REGDOCS site, records document information, and preserves raw evidence.

## Common Scout commands

```bash
python pipeline.py scout coverage
python pipeline.py scout status
python pipeline.py scout audit
python pipeline.py scout probe --start-date 2026-08-09 --end-date 2026-08-09 --limit 5
python pipeline.py scout run --start-date 2026-08-01 --end-date 2026-08-09
```

## `scout coverage`

Shows and refreshes the durable completed-date watermark:

```bash
python pipeline.py scout coverage
```

Use another database:

```bash
python pipeline.py scout coverage --db database/other.db
```

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | SQLite ledger used to find qualifying completed Scout runs | `database/regdocs.db` |

**Safety:** no REGDOCS request; writes only the local coverage manifest.

## `scout status`

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

## `scout audit`

```bash
python pipeline.py scout audit
```

Checks items such as:

- SQLite integrity;
- container relationships;
- raw snapshot references;
- gzip sizes; and
- hashes.

| Switch | Meaning |
|---|---|
| `--db PATH` | Audit another SQLite ledger |

**Safety:** READ ONLY; no REGDOCS request.

## `scout schema`

```bash
python pipeline.py scout schema
```

Checks that the Scout/base ledger schema exists.

| Switch | Meaning |
|---|---|
| `--db PATH` | Check another SQLite ledger |

**Safety:** READ ONLY.

## `scout probe`

Use this to test a filing-date range before a normal Scout acquisition:

```bash
python pipeline.py scout probe \
  --start-date 2026-08-09 \
  --end-date 2026-08-09 \
  --limit 5
```

`probe` contacts REGDOCS. It does not update the main document records in the same way as a normal acquisition run, but run/error/raw-snapshot evidence is still saved.

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

## `scout run`

Normal Scout acquisition:

```bash
python pipeline.py scout run \
  --start-date 2026-08-01 \
  --end-date 2026-08-09
```

Both dates are required by the public CLI.

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

### Public Scout run/probe switches

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
| `--expand-containers` | Traverse Folder / Compound Document members | on |
| `--no-expand-containers` | Do not traverse containers | — |
| `--expand-compounds` | Alias of `--expand-containers` | — |
| `--no-expand-compounds` | Alias of `--no-expand-containers` | — |
| `--container-max-depth N` | Maximum nested Folder/Compound depth | `20` |
| `--container-max-items N` | Maximum unique containers expanded in one run | `10000` |
| `--details` | Fetch each selected item's detail page | on |
| `--no-details` | Skip detail-page enrichment | — |
| `--detail-refresh-days N` | Treat existing detail data as fresh for N days | `30` |
| `--refresh-details` | Refresh detail data even when still considered fresh | off |
| `--concurrency N` | Number of Scout request workers | `1` |
| `--min-delay SECONDS` | Minimum global request-start delay | `2.0` |
| `--max-delay SECONDS` | Maximum global request-start delay | `4.0` |
| `--max-retries N` | Maximum HTTP retries | `4` |
| `--retry-backoff FACTOR` | Retry backoff multiplier | `2.0` |
| `--verbose` | More detailed logging | off |
| `--force-lock` | Force lock removal after confirming Scout is not running | off |

## `scout repair`

Repairs known Folder and Compound Document records already in SQLite without rerunning the normal filing-date search.

```bash
python pipeline.py scout repair
```

Bound the repair:

```bash
python pipeline.py scout repair \
  --container-max-depth 20 \
  --container-max-items 10000
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

### Public Scout repair switches

`repair` uses the same operational controls as Scout, but it does not require a date range or facet-search settings.

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
| `--force-lock` | Force lock removal after confirming Scout is not running |

---

# 7. Stage 2 — Download

Stage 2 downloads source files, checks them, hashes them, and can write metadata sidecars.

## Common Download commands

```bash
python pipeline.py download status
python pipeline.py download plan
python pipeline.py download run
```

## `download status`

```bash
python pipeline.py download status
python pipeline.py download status --json
```

| Switch | Meaning |
|---|---|
| `--json` | Print JSON output |
| `--db PATH` | Use another ledger |

**Safety:** READ ONLY.

## `download plan`

Preview what would be downloaded:

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
| `--document-id ID` | Select one document; repeat for more IDs |
| `--limit N` | Select at most N downloads |
| `--include-html` | Include known HTML records |
| `--force` | Include records already marked successful |
| `--retry-failed` | Include records marked final failure |

**Safety:** READ ONLY.

## `download sidecars`

Writes deterministic `<document-id>.metadata.json` files from the current database and source files:

```bash
python pipeline.py download sidecars
python pipeline.py download sidecars --limit 100
python pipeline.py download sidecars --sidecar-dir /tmp/regdocs-sidecars
```

Preview sidecar destinations without writing them:

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

## `download run`

Normal run:

```bash
python pipeline.py download run
```

Small test:

```bash
python pipeline.py download run --limit 25
```

Retry final failures:

```bash
python pipeline.py download run --retry-failed --limit 25
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE.

### Public Download run switches

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
| `--no-sidecars` | Suppress automatic sidecar creation | off |
| `--sidecar-dir PATH` | Separate sidecar output directory | beside source files |
| `--partial-max-age-hours HOURS` | Remove stale `.part` files older than this | `24.0` |
| `--audit-dir PATH` | Stage 2 run/progress/log directory | Stage 2 workspace |
| `--lock-file PATH` | Stage 2 exclusive lock | project lock |
| `--verbose` | More detailed logging | off |
| `--force-lock` | Force lock removal after confirming no downloader is running | off |

---

# 8. Stage 3A — Azure Content Understanding

Azure Content Understanding is the document-analysis path that can create billable Azure usage.

> **Always run `plan` before `run`.**

The Azure supervisor is intentionally single-threaded and launches one isolated child process per document. Automatic application-level Azure resubmission retries are disabled.

## Choose the scope

Every Azure `plan` or `run` must contain an explicit scope:

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

## `analyze azure plan`

```bash
python pipeline.py analyze azure plan --all
```

Selects candidates and follows the local dry-run path without submitting Azure Content Understanding analysis.

**Safety:** no billable Azure submission.

## `analyze azure run`

One document:

```bash
python pipeline.py analyze azure run --document-id 4657417
```

Bounded batch:

```bash
python pipeline.py analyze azure run --limit 10
```

Everything currently eligible:

```bash
python pipeline.py analyze azure run --all
```

**Safety:** NETWORK, DB WRITE, WORKSPACE WRITE, **AZURE CU COST**.

### Public Azure switches

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

Large PDFs are automatically split into Azure Content-Range requests of at most 300 pages. Already completed valid range artifacts can be reused locally.

---

# 9. Stage 3B — Docling

Docling is a local document-analysis path. It does not create Azure Content Understanding charges.

The Docling supervisor is intentionally single-threaded and uses isolated child processes.

## `analyze docling status`

```bash
python pipeline.py analyze docling status
```

Shows current documents, successful Docling analyses, remaining documents, and quarantine state.

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--download-dir PATH` | Source-file directory |
| `--output-dir PATH` | Docling artifact directory |
| `--state-file PATH` | Docling supervisor state |
| `--lock-file PATH` | Shared Stage 3 lock |
| `--analyzer-id ID` | Analyzer identity |

**Safety:** no document analysis is started.

## `analyze docling run`

Test one document:

```bash
python pipeline.py analyze docling run --max-documents 1
```

Larger batch:

```bash
python pipeline.py analyze docling run --max-documents 100
```

Process all currently selectable documents:

```bash
python pipeline.py analyze docling run
```

### Public Docling switches

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
| `--document-timeout-seconds SECONDS` | Terminate and retry a child exceeding this wall-clock duration | `1200` |
| `--max-documents N` | Stop after launching N child documents | unlimited |
| `--sleep-seconds SECONDS` | Pause between child launches | `0.25` |
| `--retry-quarantined` | Reset quarantined documents and try them again | off |

There is intentionally no pretend `plan` action for Docling. Use `status`, then a small real local run such as `--max-documents 1` when testing. A timed-out worker process group is terminated, counted as a failed attempt, retried fresh, and eventually quarantined under `--max-attempts` so it cannot block the corpus indefinitely.

---

# 10. Stage 4 — Normalize

Normalize converts Stage 3 analyzer output into deterministic JSONL for search and provenance.

It runs locally and does not contact Azure Content Understanding.

## Important output rule

A real Normalize run replaces the canonical Stage 4 JSONL with exactly the selected successful documents.

For example:

```bash
python pipeline.py normalize run --provider azure --limit 10
```

can create a canonical corpus containing only those selected documents.

For testing, prefer:

```bash
python pipeline.py normalize plan --provider azure --limit 10
```

or use a separate output directory.

## `normalize status`

```bash
python pipeline.py normalize status
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Use another ledger |

## `normalize plan`

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

## `normalize run`

Normal Azure-based corpus:

```bash
python pipeline.py normalize run --provider azure
```

Use local parallel workers:

```bash
python pipeline.py normalize run --provider azure --concurrency 4
```

Use Docling artifacts:

```bash
python pipeline.py normalize run --provider docling --concurrency 4
```

### Choosing `--concurrency`

The safe default is:

```bash
--concurrency 1
```

A reasonable first test is:

```bash
--concurrency 2
```

or:

```bash
--concurrency 4
```

Each worker is an isolated child process. Final JSONL document order remains deterministic even when workers finish out of order.

### Public Normalize switches

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

Rules:

- `--max-words` must be at least `--target-words`.
- `--stop-on-error` requires `--concurrency 1` so the first failure has an unambiguous meaning.

### Normalize timing

A real Normalize run records timing information including:

- candidate-selection time;
- worker wall-clock time;
- sum of worker-process time;
- final JSONL merge time; and
- total pipeline wall-clock time.

The final shard merge is streamed instead of loading whole shard files into memory at once.

---

# 11. Stage 5 — Azure AI Search

Stage 5 maps normalized chunks to Azure AI Search documents.

## `index plan`

```bash
python pipeline.py index plan
python pipeline.py index plan --limit 100
python pipeline.py index plan --document-id 4657417
```

Checks items such as:

- JSONL structure;
- chunk/provenance pairing;
- hashes;
- mapped payload size; and
- selected records.

**Safety:** no Azure Search request.

## `index publish`

```bash
python pipeline.py index publish
```

Use another index for testing:

```bash
python pipeline.py index publish --index-name regdocs-chunks-test
```

**Safety:** NETWORK, WORKSPACE WRITE, AZURE SEARCH.

### Public Index plan/publish switches

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

## `index query`

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

**Safety:** NETWORK, AZURE SEARCH. Does not publish documents.

---

# 12. Database commands

These commands work with the SQLite ledger.

## `db migrate`

Preview:

```bash
python pipeline.py db migrate --plan
```

Apply migrations:

```bash
python pipeline.py db migrate
```

| Switch | Meaning | Default |
|---|---|---|
| `--db PATH` | Database to migrate | active project DB |
| `--plan` | Show what would be done without migrating | off |
| `--no-backup` | Do not create the normal safety backup | off |
| `--backup-dir PATH` | Backup directory | project DB backup directory |
| `--force-lock` | Force pipeline lock removal after confirming nothing is running | off |

A normal migration creates a database backup first.

## `db status`

```bash
python pipeline.py db status
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Database to inspect |

## `db verify`

```bash
python pipeline.py db verify
```

Checks migration state, schema, SQLite integrity, and foreign keys.

| Switch | Meaning |
|---|---|
| `--db PATH` | Database to verify |

---

# 13. Useful read-only SQLite questions

These examples use `sqlite3 -readonly` and run only `SELECT` statements.

## Quick corpus snapshot

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT (SELECT COUNT(*) FROM documents) AS documents, (SELECT COUNT(*) FROM documents WHERE is_file=1) AS file_records, (SELECT COUNT(*) FROM files WHERE is_current=1) AS current_files, (SELECT ROUND(COALESCE(SUM(size_bytes),0) / 1073741824.0, 2) FROM files WHERE is_current=1) AS current_file_gib, (SELECT COUNT(*) FROM raw_snapshots) AS scout_snapshots;"
```

The columns mean:

- `documents`: every REGDOCS ledger record, including containers;
- `file_records`: records Scout identifies as files;
- `current_files`: current downloaded source files represented in the `files` table.

For “how many downloaded files do I have?”, `current_files` is usually the useful number.

## Total analyzed pages by analyzer

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT analyzer_id, COUNT(*) AS documents, COALESCE(SUM(page_count),0) AS total_pages FROM analyses WHERE status='SUCCEEDED' GROUP BY analyzer_id ORDER BY total_pages DESC;"
```

Keep this grouped by `analyzer_id`. The same source file can have results from more than one analyzer, so adding every successful analysis row together can double-count the corpus.

## Largest documents by page count

Top 20 unique REGDOCS documents:

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT a.document_id, MAX(a.page_count) AS pages, d.name FROM analyses AS a JOIN documents AS d ON d.id=a.document_id WHERE a.status='SUCCEEDED' AND a.page_count IS NOT NULL GROUP BY a.document_id, d.name ORDER BY pages DESC, a.document_id LIMIT 20;"
```

Show analyzers separately:

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT a.document_id, a.analyzer_id, a.page_count AS pages, d.name FROM analyses AS a JOIN documents AS d ON d.id=a.document_id WHERE a.status='SUCCEEDED' AND a.page_count IS NOT NULL ORDER BY a.page_count DESC, a.document_id LIMIT 20;"
```

## Largest downloaded source files

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT f.document_id, ROUND(f.size_bytes / 1048576.0, 1) AS mib, f.extension, d.name FROM files AS f JOIN documents AS d ON d.id=f.document_id WHERE f.is_current=1 ORDER BY f.size_bytes DESC LIMIT 20;"
```

## Current files by extension

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT COALESCE(extension,'(none)') AS extension, COUNT(*) AS files, ROUND(COALESCE(SUM(size_bytes),0) / 1073741824.0, 2) AS gib FROM files WHERE is_current=1 GROUP BY extension ORDER BY files DESC;"
```

## Download status counts

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT download_status, COUNT(*) AS documents FROM documents GROUP BY download_status ORDER BY documents DESC;"
```

## Analysis status counts

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT analyzer_id, status, COUNT(*) AS analyses FROM analyses GROUP BY analyzer_id, status ORDER BY analyzer_id, status;"
```

## Unresolved errors by stage and severity

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT stage, severity, COUNT(*) AS errors FROM errors WHERE resolved_at IS NULL GROUP BY stage, severity ORDER BY errors DESC, stage, severity;"
```

## Recent pipeline runs

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT id, stage, status, started_at, finished_at, completed_units, total_units FROM runs ORDER BY id DESC LIMIT 20;"
```

## Scout raw-evidence storage

```bash
sqlite3 -readonly -header -column database/regdocs.db "SELECT COUNT(*) AS snapshots, ROUND(COALESCE(SUM(size_bytes),0) / 1073741824.0, 2) AS raw_gib, ROUND(COALESCE(SUM(compressed_size_bytes),0) / 1073741824.0, 2) AS compressed_gib FROM raw_snapshots;"
```

## Database file size on disk

```bash
du -h database/regdocs.db database/regdocs.db-wal database/regdocs.db-shm 2>/dev/null
```

The WAL/SHM files may not exist when there is no active or recent WAL activity.

---

# 14. Rebuild and flatten

The recovery model is:

```text
Stage 1 Scout evidence       durable
Stage 2 downloaded files     durable
Stage 3 analyzer artifacts   durable / expensive
SQLite ledger                rebuildable from Stages 1-3
Stage 4 Normalize            locally rebuildable
Stage 5 Search index         republishable
```

The rebuild commands do not contact REGDOCS, Azure Content Understanding, Docling, or Azure AI Search.

## `rebuild inventory`

```bash
python pipeline.py rebuild inventory
```

No switches.

## `rebuild plan`

```bash
python pipeline.py rebuild plan
```

No switches.

## `rebuild prepare`

```bash
python pipeline.py rebuild prepare
```

Skip full raw verification only when deliberately needed:

```bash
python pipeline.py rebuild prepare --no-verify-raw
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Source ledger |
| `--no-verify-raw` | Skip full Scout gzip/size/hash verification |
| `--no-verify-analysis` | Skip Stage 3 artifact verification |

## `rebuild create`

Normal side-by-side rebuild:

```bash
python pipeline.py rebuild create
```

Choose the output path:

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
```

Create a clean operational baseline:

```bash
python pipeline.py rebuild create --flat
```

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

## `rebuild verify`

```bash
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

| Switch | Meaning |
|---|---|
| `--db PATH` | Rebuilt database to verify |

## `rebuild compare`

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

# 15. Scout recovery queue

## Show recovery tasks

```bash
python pipeline.py recover scout
```

Only HIGH priority:

```bash
python pipeline.py recover scout --priority HIGH
```

Only document IDs:

```bash
python pipeline.py recover scout --ids-only
```

| Switch | Meaning |
|---|---|
| `--db PATH` | SQLite ledger |
| `--priority HIGH|NORMAL|LOW` | Filter by task priority |
| `--limit N` | Maximum tasks shown |
| `--ids-only` | Print only document IDs |

**Safety:** READ ONLY.

## Execute Scout recovery

```bash
python pipeline.py recover scout --execute --priority HIGH --limit 100
```

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

# 16. Common recipes

## Process one document after Scout

```bash
python pipeline.py download run --document-id 4657417
python pipeline.py analyze azure plan --document-id 4657417
python pipeline.py analyze azure run --document-id 4657417
python pipeline.py normalize plan --provider azure --document-id 4657417
```

For a one-document Normalize test, use another output directory so you do not replace the main Stage 4 corpus:

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

Then test:

```bash
python pipeline.py normalize run --provider azure --concurrency 4
```

Compare the timing summary before increasing concurrency further.

## Retry only failed downloads

```bash
python pipeline.py download run --retry-failed
```

## Test Azure without selecting the whole corpus

```bash
python pipeline.py analyze azure plan --limit 5
```

Only after reviewing that output:

```bash
python pipeline.py analyze azure run --limit 5
```

## Check document and page totals

Use the read-only queries in [Section 13](#13-useful-read-only-sqlite-questions).

---

# 17. What should be backed up?

The most important local data is:

```text
workspace/1_scout/       raw REGDOCS evidence
workspace/2_download/    downloaded source files and metadata
workspace/3_analyze/     Azure/Docling analysis artifacts
database/regdocs.db      operational SQLite ledger
```

Stage 4 and Stage 5 can be recreated:

```text
workspace/4_normalize/
workspace/5_index/
```

The SQLite ledger can also be reconstructed from the durable Stage 1-3 evidence.

> Do not run `git clean -fdx` in a checkout containing the durable workspace. It can delete ignored `workspace/` and `database/` content.

---

# 19. Troubleshooting and final safety checks

## Before a large Azure run

```bash
python pipeline.py status
python pipeline.py analyze azure plan --all
python pipeline.py cost rates
```

## Before replacing the full normalized corpus

```bash
python pipeline.py normalize plan --provider azure
```

## Before publishing search

```bash
python pipeline.py index plan
```

## Unexpected lock file

If a lock exists unexpectedly:

1. check whether the matching pipeline process is still running;
2. do not delete the lock just because a command is blocked;
3. use `--force-lock` only after you have confirmed no conflicting process is active.

## A command seems dangerous or unclear

Show help instead of guessing:

```bash
python pipeline.py help
python pipeline.py help scout
python pipeline.py help analyze azure
```

A bare stage name is also safe and prints help:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
```
