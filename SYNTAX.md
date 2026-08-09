# REGDOCS Atlas command reference

This is the operational reference for the `python pipeline.py ...` POC CLI.

The project version remains **0.0.1** until explicitly changed.

## Safety rule

A stage name by itself never starts work. It only prints help.

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize
python pipeline.py index
```

Every mutating or networked stage operation requires an explicit action such as `run`, `repair`, or `publish`.

The CLI intentionally owns mode flags such as the stage cores' historical `--dry-run`, `--status`, and `--query` switches. Use the public actions documented here instead.

## Safety labels

| Label | Meaning |
|---|---|
| NETWORK | Makes an external network request |
| DB WRITE | May modify the SQLite ledger |
| WORKSPACE WRITE | May write durable/local workspace artifacts |
| AZURE CU COST | May submit billable Azure Content Understanding work |
| AZURE SEARCH | Contacts Azure AI Search |

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

`-h` and `--help` work in the equivalent positions.

---

# Global commands

## `version`

```bash
python pipeline.py version
```

Prints the POC version. It remains `0.0.1` until explicitly changed.

NETWORK: no  
DB WRITE: no  
WORKSPACE WRITE: no

## `status`

```bash
python pipeline.py status
python pipeline.py status --json
```

Shows recent pipeline runs, artifact inventory, schema state, locks, and provider cost information where available.

Switches:

- `--json` — JSON output.

NETWORK: no  
DB WRITE: no

## `diagnostics`

```bash
python pipeline.py diagnostics
```

Shows Python/runtime paths, artifact inventory, migration state, locks, and configured Azure Content Understanding pricing environment variables.

NETWORK: no  
DB WRITE: no

---

# Scout

Bare command:

```bash
python pipeline.py scout
```

prints Scout help and does not contact REGDOCS.

## `scout coverage`

```bash
python pipeline.py scout coverage
python pipeline.py scout coverage --db database/regdocs.db
```

Shows and refreshes the durable Scout date-range watermark at:

```text
workspace/1_scout/manifests/coverage.json
```

Switches:

- `--db PATH` — ledger used to discover additional qualifying historical Scout runs; default `database/regdocs.db`.

NETWORK: no  
DB WRITE: no  
WORKSPACE WRITE: yes, coverage manifest only

Coverage advances only from non-dry-run Scout runs that finished successfully, completed the base search, had zero failed base-search pages, and passed the post-run audit.

## `scout status`

```bash
python pipeline.py scout status
python pipeline.py scout status --json
```

Switches:

- `--json` — JSON status output.
- `--db PATH` — alternate ledger.
- `--progress-file PATH` — alternate live-progress JSON path.

NETWORK: no  
DB WRITE: no

## `scout audit`

```bash
python pipeline.py scout audit
```

Runs the Scout ledger/raw-evidence audit, including SQLite/container relationships and raw gzip/hash verification.

Common switches:

- `--db PATH`
- `--raw-dir PATH`

NETWORK: no  
DB WRITE: no

## `scout schema`

```bash
python pipeline.py scout schema
```

Checks the Scout/base ledger schema.

Switches:

- `--db PATH`

NETWORK: no  
DB WRITE: no

## `scout repair`

```bash
python pipeline.py scout repair
python pipeline.py scout repair --container-max-depth 20 --container-max-items 10000
```

Reprocesses known Folder and Compound Document rows without rerunning the date search.

NETWORK: yes, REGDOCS  
DB WRITE: yes  
WORKSPACE WRITE: yes, raw Scout evidence  
AZURE CU COST: no

Useful switches:

- `--db PATH`
- `--raw-dir PATH`
- `--progress-file PATH`
- `--log-file PATH`
- `--lock-file PATH`
- `--expand-containers` / `--no-expand-containers`
- `--container-max-depth N` — default `20`.
- `--container-max-items N` — default `10000`.
- `--details` / `--no-details`
- `--detail-refresh-days N` — default `30`.
- `--refresh-details`
- `--concurrency N` — default `1`.
- `--min-delay SECONDS` — default `2.0`.
- `--max-delay SECONDS` — default `4.0`.
- `--max-retries N` — default `4`.
- `--retry-backoff FACTOR` — default `2.0`.
- `--verbose`
- `--force-lock`

## `scout probe`

```bash
python pipeline.py scout probe \
  --start-date 2026-08-08 \
  --end-date 2026-08-08 \
  --limit 5
```

Fetches/parses an explicit filing-date range without updating `documents`. It is not a no-network plan: run/error/raw-snapshot evidence remains durable.

NETWORK: yes, REGDOCS  
DB WRITE: yes, run/error/raw-snapshot evidence  
WORKSPACE WRITE: yes, raw Scout evidence  
AZURE CU COST: no

Both dates are mandatory.

## `scout run`

```bash
python pipeline.py scout run \
  --start-date 2026-08-08 \
  --end-date 2026-08-09
```

Normal Scout acquisition. Both dates are mandatory; Scout never chooses a range automatically.

NETWORK: yes, REGDOCS  
DB WRITE: yes  
WORKSPACE WRITE: yes  
AZURE CU COST: no

Public Scout run/probe switches:

- `--start-date YYYY-MM-DD` — required.
- `--end-date YYYY-MM-DD` — required.
- `--db PATH`
- `--raw-dir PATH`
- `--progress-file PATH`
- `--log-file PATH`
- `--lock-file PATH`
- `--page-size 20|50|100|200` — default `200`.
- `--limit N`
- `--facets all|none|LIST` — comma-separated categories are also accepted.
- `--expand-containers` / `--no-expand-containers`
- `--container-max-depth N` — default `20`.
- `--container-max-items N` — default `10000`.
- `--details` / `--no-details`
- `--detail-refresh-days N` — default `30`.
- `--refresh-details`
- `--concurrency N` — default `1`.
- `--min-delay SECONDS` — default `2.0`.
- `--max-delay SECONDS` — default `4.0`.
- `--max-retries N` — default `4`.
- `--retry-backoff FACTOR` — default `2.0`.
- `--verbose`
- `--force-lock`

Common flow:

```bash
python pipeline.py scout coverage
python pipeline.py scout probe --start-date 2026-08-08 --end-date 2026-08-08 --limit 5
python pipeline.py scout run --start-date 2026-08-08 --end-date 2026-08-08
```

---

# Download

Bare command:

```bash
python pipeline.py download
```

prints Download help and performs no work.

## `download status`

```bash
python pipeline.py download status
python pipeline.py download status --json
```

Switches:

- `--json`
- `--db PATH`

NETWORK: no  
DB WRITE: no

## `download plan`

```bash
python pipeline.py download plan
python pipeline.py download plan --limit 20
python pipeline.py download plan --document-id 1234567
```

Previews eligible downloads without network requests or writes.

NETWORK: no  
DB WRITE: no  
WORKSPACE WRITE: no

Selection switches:

- `--db PATH`
- `--downloads PATH` / `--output-dir PATH`
- `--document-id ID` — repeatable.
- `--limit N`
- `--include-html`
- `--force`
- `--retry-failed`

## `download sidecars`

```bash
python pipeline.py download sidecars
python pipeline.py download sidecars --limit 100
python pipeline.py download sidecars --sidecar-dir /tmp/regdocs-sidecars
```

Writes deterministic `<document-id>.metadata.json` sidecars from the current ledger and files without downloading anything.

NETWORK: no  
DB WRITE: no  
WORKSPACE WRITE: yes

Useful switches:

- `--db PATH`
- `--downloads PATH` / `--output-dir PATH`
- `--document-id ID` — repeatable.
- `--limit N`
- `--sidecar-dir PATH`
- `--force-lock`

A low-level `--dry-run` may be added to this action to preview sidecar destinations without writing them.

## `download run`

```bash
python pipeline.py download run
python pipeline.py download run --limit 25
python pipeline.py download run --retry-failed --limit 25
```

Reconciles current files, downloads eligible records, hashes/versions files, and refreshes sidecars.

NETWORK: yes, REGDOCS file endpoints when candidates exist  
DB WRITE: yes  
WORKSPACE WRITE: yes  
AZURE CU COST: no

Public run switches:

- `--db PATH`
- `--downloads PATH` / `--output-dir PATH`
- `--document-id ID` — repeatable.
- `--limit N`
- `--include-html`
- `--force` — redownload successful/current records.
- `--retry-failed`
- `--attempts N` — default `4` HTTP attempts per selected document.
- `--concurrency N` — default `1`.
- `--min-delay SECONDS` — default `3.0`.
- `--max-delay SECONDS` — default `6.0`.
- `--connect-timeout SECONDS` — default `30.0`.
- `--read-timeout SECONDS` — default `300.0`.
- `--max-file-size-mb MB` — default `2048.0`.
- `--reconcile` / `--no-reconcile` — reconcile existing files; default enabled.
- `--verify-existing` — re-hash during reconciliation.
- `--archive-replaced` / `--no-archive-replaced` — default enabled.
- `--sidecars` / `--write-sidecars` — explicitly request sidecars; root POC CLI already enables sidecars by default for normal runs.
- `--sidecar-dir PATH`
- `--partial-max-age-hours HOURS` — default `24.0`.
- `--audit-dir PATH`
- `--lock-file PATH`
- `--verbose`
- `--force-lock`

---

# Analyze: Azure Content Understanding

Bare provider command:

```bash
python pipeline.py analyze azure
```

prints help and cannot submit to Azure.

Every Azure action also requires an explicit scope:

```text
--all
--limit N
--document-id ID
```

## `analyze azure plan`

```bash
python pipeline.py analyze azure plan --all
python pipeline.py analyze azure plan --limit 10
python pipeline.py analyze azure plan --document-id 1234567
```

Uses the existing Azure dry-run path to select candidates and exercise local worker selection without submitting Content Understanding analysis.

NETWORK: no Azure Content Understanding submission  
AZURE CU COST: no  
DB/STATE WRITE: may record dry-run/supervisor state; do not treat it as a pristine read-only query

## `analyze azure run`

```bash
python pipeline.py analyze azure run --document-id 1234567
python pipeline.py analyze azure run --limit 10
python pipeline.py analyze azure run --all
```

This is the only public Stage 3 Azure action that permits Content Understanding submissions.

NETWORK: yes, Azure Content Understanding  
DB WRITE: yes  
WORKSPACE WRITE: yes, expensive provider artifacts  
AZURE CU COST: **yes**

Always run `plan` first.

Public Azure switches:

- `--all` — all currently eligible documents.
- `--limit N` — explicit bounded candidate scope.
- `--document-id ID` — one document.
- `--db PATH`
- `--endpoint URL` — defaults from `CONTENTUNDERSTANDING_ENDPOINT`.
- `--key KEY` — defaults from `CONTENTUNDERSTANDING_KEY`.
- `--api-version VERSION` — defaults from `CONTENTUNDERSTANDING_API_VERSION`, currently `2025-11-01`.
- `--polling-interval SECONDS` — default `3`.
- `--download-dir PATH`
- `--output-dir PATH`
- `--lock-file PATH`
- `--force-lock`
- `--state-file PATH`
- `--worker-sleep-seconds SECONDS` — default `0.25`.
- `--analyzer-id ID` — default `prebuilt-layout`.
- `--force` — ignore successful current analysis state and reselect; use with extreme care because it can cause billable reanalysis.
- `--no-reconcile-artifacts`
- `--no-verify-hash`

Cost/status helpers:

```bash
python pipeline.py status
python pipeline.py cost azure
python pipeline.py cost azure --run-id 123
python pipeline.py cost rates
```

---

# Analyze: Docling

Bare provider command:

```bash
python pipeline.py analyze docling
```

prints help and performs no conversion.

## `analyze docling status`

```bash
python pipeline.py analyze docling status
```

Shows current document count, successful current Docling analyses, remaining documents, and quarantine state.

NETWORK: no external document service  
DB WRITE: no

Useful switches:

- `--db PATH`
- `--download-dir PATH`
- `--output-dir PATH`
- `--state-file PATH`
- `--lock-file PATH`
- `--analyzer-id ID`

## `analyze docling run`

```bash
python pipeline.py analyze docling run --max-documents 1
python pipeline.py analyze docling run --max-documents 100
python pipeline.py analyze docling run
```

Runs local Docling conversion using one isolated child process at a time.

NETWORK: no Azure Content Understanding  
DB WRITE: yes  
WORKSPACE WRITE: yes  
AZURE CU COST: no

Public Docling switches:

- `--db PATH`
- `--download-dir PATH`
- `--output-dir PATH`
- `--state-file PATH`
- `--lock-file PATH`
- `--force-lock`
- `--analyzer-id ID` — default `docling-standard`.
- `--max-attempts N` — maximum fresh-child attempts per document.
- `--max-documents N` — stop after launching N child documents; recommended for POC testing.
- `--sleep-seconds SECONDS` — default `0.25`.
- `--retry-quarantined`

There is intentionally no fake public `plan` action for Docling because the current supervisor has no true dry-run selection mode. Use `status`, then a bounded real local `run --max-documents N`.

---

# Normalize

Bare command:

```bash
python pipeline.py normalize
```

prints help and does not rebuild normalized JSONL.

## `normalize status`

```bash
python pipeline.py normalize status
```

Shows the latest Normalize run.

NETWORK: no  
DB WRITE: no meaningful pipeline work

Useful switches:

- `--db PATH`

## `normalize plan`

```bash
python pipeline.py normalize plan --provider azure --limit 100
python pipeline.py normalize plan --provider docling --limit 10
```

Resolves selected Stage 3 artifacts and reports `OK` / `MISSING_JSON` without replacing canonical Stage 4 JSONL.

`--provider azure|docling` is mandatory in the public CLI.

NETWORK: no  
AZURE CU COST: no  
CANONICAL STAGE 4 WRITE: no

## `normalize run`

```bash
python pipeline.py normalize run --provider azure
python pipeline.py normalize run --provider azure --document-id 1234567
python pipeline.py normalize run --provider docling --limit 100
```

Runs local normalization and atomically replaces canonical Stage 4 JSONL with the selected successful worker shards.

NETWORK: no external analysis service  
DB WRITE: yes  
WORKSPACE WRITE: yes  
AZURE CU COST: no

Important: do not use a bounded real `run --limit N` against your main `workspace/4_normalize` if you expect it to retain the full corpus. A bounded real run creates a bounded canonical output. Use `plan --limit N`, or use alternate `--db` and `--output-dir` paths for execution testing.

Public Normalize switches:

- `--provider azure|docling` — required for `plan` and `run`.
- `--db PATH`
- `--analysis-dir PATH`
- `--output-dir PATH`
- `--document-id ID` — repeatable.
- `--limit N`
- `--target-words N` — normal chunk target.
- `--max-words N` — must be at least target words.
- `--stop-on-error` — default behavior continues to next document.
- `--lock-file PATH`
- `--force-lock`

---

# Index / Azure AI Search

Bare command:

```bash
python pipeline.py index
```

prints help and does not contact Azure Search.

## `index plan`

```bash
python pipeline.py index plan
python pipeline.py index plan --limit 100
python pipeline.py index plan --document-id 1234567
```

Validates `chunks.jsonl` / `provenance.jsonl`, maps search documents, counts payloads, and hashes inputs. Azure Search is not contacted.

NETWORK: no  
DB WRITE: no  
WORKSPACE WRITE: no  
AZURE SEARCH: no

## `index publish`

```bash
python pipeline.py index publish
python pipeline.py index publish --index-name regdocs-chunks-poc
```

Creates/validates the Azure AI Search index and uploads selected normalized chunks.

NETWORK: yes, Azure AI Search  
WORKSPACE WRITE: yes, `workspace/5_index/last_run.json`  
AZURE CU COST: no  
AZURE SEARCH: yes

Public plan/publish switches:

- `--normalized-dir PATH`
- `--output-dir PATH`
- `--endpoint URL` — defaults from `AZURE_SEARCH_ENDPOINT`.
- `--api-key KEY` — defaults from `AZURE_SEARCH_ADMIN_KEY`; otherwise uses `DefaultAzureCredential`.
- `--index-name NAME` — defaults from `AZURE_SEARCH_INDEX_NAME` or `regdocs-chunks`.
- `--document-id ID` — repeatable.
- `--limit N` — chunk count, not document count.
- `--batch-size N` — default `500`, maximum `1000`.
- `--max-batch-bytes BYTES` — default `12582912` (12 MiB).
- `--recreate-index` — cannot be combined with `--document-id` or `--limit`.

## `index query`

```bash
python pipeline.py index query "pipeline abandonment"
python pipeline.py index query "compressor station" --top 10
python pipeline.py index query "*" --top 1
python pipeline.py index query "pipeline" --filter "document_id eq '1234567'"
```

Queries an existing Azure AI Search index without publishing anything.

NETWORK: yes, Azure AI Search  
WORKSPACE WRITE: no  
AZURE CU COST: no  
AZURE SEARCH: yes

Switches:

- query text — required immediately after `query`.
- `--endpoint URL`
- `--api-key KEY`
- `--index-name NAME`
- `--top N` — default `5`.
- `--filter ODATA`

---

# Database

Database commands already require explicit actions.

## `db migrate`

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db migrate --db database/other.db
```

Switches:

- `--db PATH`
- `--plan` — show migration plan only.
- `--no-backup`
- `--backup-dir PATH`
- `--force-lock`

Without `--plan`, this mutates the database and normally creates a safety backup first.

## `db status`

```bash
python pipeline.py db status
python pipeline.py db status --db database/regdocs.flat.db
```

Switches:

- `--db PATH`

NETWORK: no  
DB WRITE: no

## `db verify`

```bash
python pipeline.py db verify
python pipeline.py db verify --db database/regdocs.flat.db
```

Verifies migrations/schema plus SQLite integrity/foreign keys.

Switches:

- `--db PATH`

NETWORK: no  
DB WRITE: no

---

# Rebuild / flatten

Rebuild commands never contact REGDOCS, Azure Content Understanding, Docling, or Azure AI Search.

## `rebuild inventory`

```bash
python pipeline.py rebuild inventory
```

Counts durable Scout/Download/Stage 3 artifacts and normalized outputs.

## `rebuild plan`

```bash
python pipeline.py rebuild plan
```

Reports the best artifact-recovery tier and whether Stage 1-3 can be rebuilt without reruns.

## `rebuild prepare`

```bash
python pipeline.py rebuild prepare
python pipeline.py rebuild prepare --no-verify-raw
```

Switches:

- `--db PATH`
- `--no-verify-raw` — skip full Scout raw gzip/hash verification.
- `--no-verify-analysis` — skip Stage 3 provider-artifact verification.

Writes/refreshed durable recovery manifests under `workspace/` and also refreshes Scout coverage.

## `rebuild create`

```bash
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild create --flat
python pipeline.py rebuild create --flat --output database/regdocs.flat2.db
```

Switches:

- `--output PATH`
- `--flat` — rebuild the Stage 1-3 operational truth, then remove run/error/recovery history from the new output DB.

Flat default output: `database/regdocs.flat.db`.  
Normal default output: `database/regdocs.rebuilt.db`.

The active `database/regdocs.db` is never overwritten by this command.

## `rebuild verify`

```bash
python pipeline.py rebuild verify --db database/regdocs.flat.db
```

Switches:

- `--db PATH`

## `rebuild compare`

```bash
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.flat.db
```

Switches:

- `--source PATH`
- `--rebuilt PATH`

The key Stage 1-3 recovery result is:

```text
source_and_stage3_equivalent: true
```

Stage 4 normalization rows are deliberately not part of the durable rebuild target.

---

# Scout recovery queue

## Show queue

```bash
python pipeline.py recover scout
python pipeline.py recover scout --priority HIGH --limit 100
python pipeline.py recover scout --ids-only
```

Switches:

- `--db PATH`
- `--priority HIGH|NORMAL|LOW`
- `--limit N`
- `--ids-only`

NETWORK: no  
DB WRITE: no

## Execute Scout recovery

The current recovery execution safety switch remains explicit:

```bash
python pipeline.py recover scout --execute --priority HIGH --limit 100
```

Switches:

- `--execute` — required to perform the recovery network requests.
- `--db PATH`
- `--priority HIGH|NORMAL|LOW`
- `--limit N`
- `--timeout SECONDS` — default `60.0`.
- `--force-lock`

NETWORK: yes, REGDOCS  
DB WRITE: yes  
AZURE CU COST: no

---

# Recommended POC operating sequence

Check state first:

```bash
python pipeline.py status
python pipeline.py scout coverage
python pipeline.py download plan
python pipeline.py analyze azure plan --all
python pipeline.py normalize plan --provider azure --limit 100
python pipeline.py index plan
```

Then explicitly run only the stages that have intended work:

```bash
python pipeline.py scout run --start-date YYYY-MM-DD --end-date YYYY-MM-DD
python pipeline.py download run
python pipeline.py analyze azure run --all
python pipeline.py normalize run --provider azure
python pipeline.py index publish
```

For the expensive Stage 3 boundary, preserve:

```text
workspace/3_analyze/
```

A repository cleanup must never remove `workspace/` or `database/`. In particular, do not use `git clean -fdx` in a checkout containing the durable artifacts.
