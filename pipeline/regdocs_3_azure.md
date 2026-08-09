# `regdocs_3_azure.py`

Stage 3 Azure provider for REGDOCS: analyze verified current Stage 2 files with
Azure AI Content Understanding while preserving raw layout JSON, Markdown,
ledger provenance, and crash-recovery state.

The public command is a **single-threaded crash-resilient supervisor**. It
launches exactly one short-lived child process per document through
`regdocs_3_azure_worker.py`.

Script version documented: **3.6.1**.

## Cost-safe retry policy

Azure retries are intentionally conservative because another submission may be
billable.

For the public `regdocs_3_azure.py` path:

```text
worker launches per document per run = 1
application-level Azure submission attempts per request/range = 1
automatic same-run retries = 0
concurrency = 1
```

A handled Azure failure, segmentation fault, OOM kill, or other worker crash is
recorded and the supervisor immediately advances to the next selected document.
It does **not** relaunch that document during the same run.

A later normal Azure Stage 3 invocation is the retry boundary. Failed/crashed
analysis identities remain eligible because only intact `SUCCEEDED` identities
are skipped.

For example:

```bash
python pipeline/regdocs_3_azure.py --limit 1000
```

If documents 25 and 88 fail, the run continues through the rest of the batch.
A later refresh/rerun:

```bash
python pipeline/regdocs_3_azure.py --limit 1000
```

skips intact successes and may select those failed documents again.

There is no Azure quarantine/retry queue. The older 3.5.x quarantine state is
ignored and cleared as it is encountered.

The internal worker still contains generic retry-capable code, but the public
supervisor always invokes it with `--max-attempts 1` and removes an inherited
`CONTENTUNDERSTANDING_MAX_ATTEMPTS` override. Do not invoke the worker directly
for normal operation.

## Architecture

```text
Stage 2 current source file
        |
        v
regdocs_3_azure.py
single-threaded durable supervisor
        |
        +--> owns database/locks/3_analyze.lock
        +--> owns supervisor-state.json
        +--> selects eligible documents
        +--> prints the complete selected queue before any child starts
        +--> concurrency = 1
        |
        v
regdocs_3_azure_worker.py --document-id <one document>
        |
        +--> hash/source validation
        +--> Azure Content Understanding
        +--> PDF Content-Range handling
        +--> raw JSON + Markdown
        +--> analyses / runs / errors ledger writes
        |
        +--> success --------> next document
        +--> handled failure -> next document
        `--> process crash ---> record + next document
                                 |
                                 v
                         retry only on later rerun
```

Python exception handling cannot recover from a segmentation fault in the same
process. The worker boundary keeps the long-running Stage 3 queue alive if a
native dependency crashes while processing one document.

There is never more than one Azure worker running at once.

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

Before the first Azure worker starts, the supervisor prints the entire selected
queue in processing order. A 4,071-document run therefore prints all 4,071
selected document IDs first, then begins `[1/4071] ...` processing. This makes
the exact billable candidate set visible before submission begins.

To retry failures, run the normal command again. No special retry flag is
required.

## Durable supervisor state

Default state file:

```text
workspace/3_analyze/content-understanding/supervisor-state.json
```

The supervisor records processing identity, cumulative worker launches, last
exit code/signal, timestamps, last run status, and cumulative crash count. The
state file is written atomically through a `.partial` file.

This state is diagnostic and durable, but it does not suppress a failed
identity on future runs. SQLite success state and canonical artifacts determine
whether work is skipped.

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
python pipeline/regdocs_3_azure.py --dry-run --limit 10
```

An API key can instead be supplied through the environment:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"
```

Do not place a key directly in the command line. The supervisor passes key
credentials to the worker through the environment rather than the child command
line.

Supported public supervisor environment settings:

| Variable | Purpose |
|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Azure resource endpoint |
| `CONTENTUNDERSTANDING_KEY` | Optional API key |
| `CONTENTUNDERSTANDING_API_VERSION` | API version override |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | Analyzer override |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | LRO polling interval |

`CONTENTUNDERSTANDING_MAX_ATTEMPTS` is intentionally ignored by the public
supervisor. The cost-safety policy is one application-level submission attempt
per request/range per run.

## Candidate identity

Stage 3 Azure works on current Stage 2 files. Successful analysis identity is:

```text
file_id + file_sha256 + analyzer_id + api_version
```

Unless `--force` is supplied, intact canonical successes are skipped. A failed
or crashed identity remains eligible on the next normal run. A stale or legacy
success can be sent through a worker so the worker can reconcile and
canonicalize it locally without another Azure call when possible.

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

The raw JSON is the authoritative Azure result. Markdown is a convenient
human-readable derivative.

## Large PDFs and Content-Range recovery

The worker counts PDF pages locally with `pypdf`. PDFs over 300 pages are sent
through inclusive Azure `content_range` requests of at most 300 pages each.
The original source PDF is not physically split.

For example, a 986-page PDF is analyzed as:

```text
1-300
301-600
601-900
901-986
```

Every successful range is committed immediately under a `.parts/` directory.
If a later range fails or the worker crashes, that document is not retried in
the same run. On a later refresh, the fresh worker can reuse valid completed
range artifacts and continue without resubmitting those successful ranges.

The final canonical artifact is published only after the combined returned page
count matches the local source PDF page count.

`--force` and `--no-reconcile-artifacts` disable normal range-part reuse. Do not
use `--force` merely to resume interrupted work.

## Cost and crash caveat

Stage 3 Azure can make billable external calls. Keep initial runs bounded and
inspect `--dry-run` output before a large selection.

Disabling same-run retries reduces accidental duplicate submissions, but one
ambiguity remains: if Azure accepts a billable operation and the worker crashes
before the operation/result is durably recorded, the next refresh cannot yet
resume that accepted operation by ID. A later refresh may therefore create a
second billable operation for that document/range.

The planned durability upgrade is to persist accepted Azure operation IDs
immediately and resume polling instead of resubmitting uncertain operations.

## Important options

| Option | Effect |
|---|---|
| `--document-id ID` | Select one document |
| `--limit N` | Select at most N eligible documents |
| `--all` | Explicitly select all remaining eligible documents |
| `--dry-run` | Avoid Azure calls while previewing worker selection/reconciliation |
| `--force` | Bypass successful-artifact reconciliation/range reuse and resubmit |
| `--no-reconcile-artifacts` | Disable artifact/range recovery |
| `--no-verify-hash` | Disable source SHA-256 verification |
| `--force-lock` | Remove an existing lock only after confirming it is stale |
| `--worker-sleep-seconds N` | Optional pause between document workers |

There is intentionally no public `--retry-quarantined` or
`--worker-max-attempts` option for Azure 3.6+. A later normal run is the retry
mechanism.

## Provider boundary

Azure is one Stage 3 analysis provider. The local alternative is:

```text
regdocs_3_docling.py
regdocs_3_docling_worker.py
```

Docling may use a different retry/quarantine policy because it does not create
Azure per-submission billing risk. Provider durability policies therefore do not
need to be identical.

Both providers preserve their Stage 3 artifacts separately. Stage 4 is the
normalization boundary that turns analysis output into the common REGDOCS
corpus model.

Previous: [Stage 2 downloader](regdocs_2_download.md).

Alternative Stage 3 provider: [Docling](regdocs_3_docling.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
