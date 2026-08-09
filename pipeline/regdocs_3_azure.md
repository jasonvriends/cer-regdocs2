# `regdocs_3_azure.py`

Stage 3 Azure provider for REGDOCS: analyze verified current Stage 2 files with
Azure AI Content Understanding while preserving raw layout JSON, Markdown,
ledger provenance, and crash-recovery state.

The public command is a **single-threaded crash-resilient supervisor**. It
launches exactly one short-lived child process per document through
`regdocs_3_azure_worker.py`.

Script version documented: **3.7.0**.

## Run ownership

One public supervisor invocation equals one row in the SQLite `runs` table.
Workers are isolated execution units, not pipeline runs.

```text
python regdocs_3_azure.py --all
        |
        v
Run 42  provider=azure
        |
        +--> document A worker --> analyses.run_id = 42
        +--> document B worker --> analyses.run_id = 42
        +--> document C worker --> analyses.run_id = 42
        `--> ...
```

The supervisor creates and finishes Run 42 and maintains its heartbeat, progress,
summary, and final status. The child worker is bound to that parent run and does
not allocate or finish another `runs` row during normal public execution.

If the supervisor is interrupted, that run is recorded as `INTERRUPTED`. A later
normal invocation creates a new run and skips already committed successful
analysis identities.

## Cost-safe retry policy

Azure retries are intentionally conservative because another submission may be
billable.

```text
worker launches per document per run = 1
application-level Azure submission attempts per request/range = 1
Azure SDK transport retries = 0
automatic same-run retries = 0
concurrency = 1
```

A handled Azure failure, segmentation fault, OOM kill, or other worker crash is
recorded and the supervisor immediately advances to the next selected document.
It does **not** relaunch that document during the same run.

A later normal Azure Stage 3 invocation is the retry boundary. Failed/crashed
analysis identities remain eligible because only intact `SUCCEEDED` identities
are skipped.

There is no Azure quarantine/retry queue.

## Normal commands

Preview a bounded selection without Azure calls:

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

Analyze every remaining eligible current document:

```bash
python pipeline/regdocs_3_azure.py --all
```

Normal progress is intentionally compact:

```text
Run 42: Azure supervisor 3.7.0; 4070 document(s) selected
Concurrency:       1 child process
Azure retries:     disabled
Retry boundary:    next normal Stage 3 Azure rerun

[1/4070] 4659390 ... SUCCEEDED pages=60
[2/4070] 4659392 ... SUCCEEDED pages=2
[3/4070] 4659394 ... SUCCEEDED pages=1
```

Worker stdout/stderr is captured on normal successes. Detailed worker diagnostics
are surfaced when a document fails, crashes, or has a configuration error.

## Durable supervisor state

Default state file:

```text
workspace/3_analyze/content-understanding/supervisor-state.json
```

The state file records process-level history such as cumulative worker launches,
last exit code/signal, timestamps, crash count, and the last parent pipeline run
ID. It is diagnostic state; SQLite analysis identity and canonical artifacts
determine whether a document is complete.

## Locking

The supervisor owns the normal Stage 3 lock for the full run:

```text
database/locks/3_analyze.lock
```

A worker receives a derived short-lived worker lock. Stale PID detection lets a
later worker clean up a lock left by a crashed child.

Do not use `--force-lock` unless you have independently confirmed that no live
Stage 3 Azure process owns the lock.

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

The public supervisor ignores `CONTENTUNDERSTANDING_MAX_ATTEMPTS` and invokes the
worker with one application-level submission attempt. The worker also constructs
the Azure client with SDK transport retries disabled.

## Candidate identity

Successful Azure analysis identity is:

```text
file_id + file_sha256 + analyzer_id + api_version
```

Unless `--force` is supplied, intact canonical successes are skipped. A failed
or crashed identity remains eligible on the next normal run. A stale or legacy
success can be reconciled from matching local artifacts without another Azure
call when possible.

## Artifacts

Default root:

```text
workspace/3_analyze/content-understanding/
```

Canonical outputs are keyed by analyzer, API version, document ID, and source
SHA-256:

```text
workspace/3_analyze/content-understanding/
├── raw/<analyzer>/<api-version>/<document-id>/<sha256>.json
└── markdown/<analyzer>/<api-version>/<document-id>/<sha256>.md
```

## Large PDFs and Content-Range recovery

PDFs over 300 pages are analyzed through inclusive Azure `content_range`
requests of at most 300 pages each. The original source PDF is not physically
split.

Every successful range is committed immediately under a `.parts/` directory.
If a later range fails or the worker crashes, that document is not retried in
the same run. On a later refresh, valid completed range artifacts can be reused
without resubmitting those successful ranges.

`--force` and `--no-reconcile-artifacts` disable normal range-part reuse. Do not
use `--force` merely to resume interrupted work.

## Remaining billing ambiguity

If Azure accepts a billable operation and the worker dies before the operation
ID/result is durably recorded, a later refresh cannot yet resume that accepted
operation by ID. A future durability upgrade should persist accepted operation
IDs immediately and resume polling rather than resubmitting uncertain work.

## Provider boundary

The local alternative is:

```text
regdocs_3_docling.py
regdocs_3_docling_worker.py
```

Both Stage 3 supervisors use the same run-ownership rule: **one public supervisor
invocation equals one pipeline run**. Their retry policies differ because Azure
has per-submission billing risk while Docling is local.

Previous: [Stage 2 downloader](regdocs_2_download.md).

Alternative Stage 3 provider: [Docling](regdocs_3_docling.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
