# `regdocs_3_azure.py`

Stage 3 Azure provider for REGDOCS. It analyzes verified Stage 2 files with Azure
AI Content Understanding while preserving raw JSON, Markdown, ledger provenance,
and durable crash state.

The public command is a **single-threaded crash-resilient supervisor**. It
launches exactly one short-lived child process per document through
`regdocs_3_azure_worker.py`.

Script version documented: **3.6.2**.

## Files

The Azure provider intentionally consists of only:

```text
regdocs_3_azure.py          public durable supervisor
regdocs_3_azure_worker.py   one-document Azure worker
regdocs_3_azure.md          runbook
```

## Cost-safe retry policy

Azure retries are intentionally disabled because another submission may be
billable.

```text
worker launches per document per run = 1
application-level submissions per request/range = 1
Azure SDK transport retries = 0
automatic same-run retries = 0
concurrency = 1
```

A handled Azure failure, segmentation fault, OOM kill, or other worker crash is
recorded and the supervisor advances to the next document. The failed document
is not relaunched during the same run.

A later normal Azure Stage 3 invocation is the retry boundary. Intact
`SUCCEEDED` analyses are skipped, while failed/crashed identities remain
eligible.

## Normal commands

Preview without Azure calls:

```bash
python pipeline/regdocs_3_azure.py --dry-run --limit 10
```

Analyze one document:

```bash
python pipeline/regdocs_3_azure.py --document-id 4647200
```

Analyze a bounded batch:

```bash
python pipeline/regdocs_3_azure.py --limit 1000
```

Analyze every remaining eligible document:

```bash
python pipeline/regdocs_3_azure.py --all
```

To retry failures, run the normal command again. No special retry flag is
required.

## Console output

The supervisor prints the selected count once and then one progress line per
document. It does **not** dump the full selected ID list before processing.
Normal successful child output is captured so the worker's internal run banner,
endpoint, artifact paths, and `[1/1]` progress do not duplicate the supervisor.

Typical output:

```text
Azure supervisor 3.6.2: 4070 document(s) selected
Concurrency:       1 child process
Azure retries:     disabled
Retry boundary:    next normal Stage 3 Azure rerun

[1/4070] 4659390 ... SUCCEEDED pages=60
[2/4070] 4659392 ... SUCCEEDED pages=2
[3/4070] 4659394 ... SUCCEEDED pages=1
```

If a worker fails or crashes, its captured stdout/stderr is printed as diagnostic
context for that document.

## Durable supervisor state

Default state file:

```text
workspace/3_analyze/content-understanding/supervisor-state.json
```

It records processing identity, cumulative worker launches, last exit
code/signal, timestamps, last run status, and cumulative crash count. The state
file is written atomically through a `.partial` file.

The state is diagnostic. It does not quarantine Azure failures across refresh
runs.

## Locking

The supervisor owns:

```text
database/locks/3_analyze.lock
```

for the full run. Each short-lived worker receives a derived worker lock. Stale
PID detection removes locks whose owning process is no longer running.

Do not use `--force-lock` unless you have independently confirmed no live Stage
3 Azure process owns the lock.

## Azure configuration

Preferred authentication uses `DefaultAzureCredential`:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
az login
```

An API key can instead be supplied through the environment:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"
```

Do not place the key directly on the command line.

Supported public settings include:

| Variable | Purpose |
|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Azure resource endpoint |
| `CONTENTUNDERSTANDING_KEY` | Optional API key |
| `CONTENTUNDERSTANDING_API_VERSION` | API version override |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | Analyzer override |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | long-running-operation polling interval |

The public supervisor ignores inherited retry-attempt overrides and always gives
the worker `--max-attempts 1`. The worker also disables Azure SDK transport
retries with `retry_total=0`, `retry_connect=0`, `retry_read=0`, and
`retry_status=0`.

## Candidate identity

Successful analysis identity is:

```text
file_id + file_sha256 + analyzer_id + api_version
```

Unless `--force` is supplied, intact canonical successes are skipped. Stale
artifacts can be reconciled locally when possible without another Azure call.

## Artifacts

Default root:

```text
workspace/3_analyze/content-understanding/
```

Canonical outputs are:

```text
raw/<analyzer>/<api-version>/<document-id>/<sha256>.json
markdown/<analyzer>/<api-version>/<document-id>/<sha256>.md
```

## Large PDFs and Content-Range recovery

PDFs over 300 pages are submitted as inclusive page ranges of at most 300 pages.
The original PDF is not physically split.

For a 986-page PDF:

```text
1-300
301-600
601-900
901-986
```

Every successful range is saved immediately below the canonical raw artifact's
`.parts/` directory. If a later range fails or the worker crashes, the next
normal refresh can reuse completed range artifacts and submit only unfinished
ranges.

`--force` and `--no-reconcile-artifacts` bypass this recovery and can cause
unnecessary resubmission.

## Remaining crash/billing ambiguity

If Azure accepts a billable operation and the worker crashes before that
operation ID/result is durably recorded, the next refresh cannot yet resume the
accepted operation. A later run may therefore submit that document/range again.

The next durability improvement is immediate accepted-operation-ID persistence
and polling resume.

## Provider boundary

The local Stage 3 alternative is:

```text
regdocs_3_docling.py
regdocs_3_docling_worker.py
```

Docling can use a different retry/quarantine policy because it does not create
the same Azure per-submission billing risk.

Previous: [Stage 2 downloader](regdocs_2_download.md).
Alternative: [Stage 3 Docling](regdocs_3_docling.md).
Next: [Stage 4 normalizer](regdocs_4_normalize.md).
