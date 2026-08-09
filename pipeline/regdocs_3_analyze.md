# `regdocs_3_analyze.py`

Stage 3 of the REGDOCS pipeline: **analyze verified current source files with
Azure AI Content Understanding and preserve layout JSON + Markdown with ledger
provenance**.

The public command is now a **single-threaded crash-resilient supervisor**. It
does not import the Azure SDK or parse PDFs itself. Instead it launches exactly
one short-lived child process per document using
`regdocs_3_analyze_worker.py`. A segfault, OOM kill, or other native-process
crash therefore kills only the current document worker instead of the full
Stage 3 queue.

Script version documented: **3.5.0**.

## Architecture

```text
python pipeline/regdocs_3_analyze.py --limit 1000
                    |
                    v
        durable single-thread supervisor
        - owns database/locks/3_analyze.lock
        - owns supervisor-state.json
        - selects eligible documents
        - concurrency = 1
                    |
                    v
       regdocs_3_analyze_worker.py
       --document-id <one document>
                    |
          +---------+---------+
          |                   |
       success             process crash
          |                   |
          v                   v
      next document      retry fresh child
                              |
                      max crash attempts?
                         |           |
                        no          yes
                         |           |
                         v           v
                       retry      quarantine
                                      |
                                      v
                                 next document
```

The worker is the Stage 3 Azure implementation that existed before 3.5.0. It
still owns:

- source-path resolution and SHA-256 verification;
- Azure authentication and Content Understanding calls;
- retryable HTTP/service retry policy;
- local PDF page counting;
- `Content-Range` handling for PDFs over 300 pages;
- immediate persistence of completed range artifacts;
- final JSON + Markdown artifact publication;
- `analyses`, `runs`, and `errors` ledger writes;
- artifact reconciliation and no-cost local recovery.

The supervisor owns process durability, crash retries, quarantine state, and the
long-lived Stage 3 lock.

## Why the worker boundary exists

Python `try/except` cannot recover from a native segmentation fault in the same
process. Before 3.5.0, a segfault anywhere inside the Azure SDK, HTTP/native
dependency stack, PDF parsing dependency, or other native code could terminate
the entire Stage 3 loop.

Now each document gets a fresh Python child. If that child dies before recording
a terminal database result, the parent process remains alive and can retry the
same document or quarantine it and continue.

This remains intentionally **single-threaded**: there is never more than one
Azure document worker running at a time.

## Durable supervisor state

Default state file:

```text
workspace/3_analyze/content-understanding/supervisor-state.json
```

The state is written through a `.partial` file, `fsync`ed, and atomically
renamed. Per-document state includes the current source/analyzer/API identity,
worker launch count, crash-attempt count, last exit code, last signal, and
quarantine state.

A quarantine applies only to the same processing identity. If the current file
SHA-256, analyzer ID, or API version changes, the old quarantine is cleared
automatically for that document.

Default crash policy:

```text
worker-max-attempts = 3
concurrency = 1
```

A normal handled Azure/document failure is **not** retried by the supervisor.
Azure retry policy already ran inside the worker. Crash-level retries are only
used when the child exits before recording a normal terminal result.

After the crash limit is reached, the document is quarantined and Stage 3
continues with the next selected document.

Retry quarantined documents explicitly:

```bash
python pipeline/regdocs_3_analyze.py --limit 1000 --retry-quarantined
```

or for one known document:

```bash
python pipeline/regdocs_3_analyze.py --document-id 4647200 --retry-quarantined
```

## Locking

The supervisor holds the normal Stage 3 lock for its entire lifetime:

```text
database/locks/3_analyze.lock
```

The lock records the supervisor PID. On startup:

```text
lock exists
   |
   v
read PID
   |
   v
is PID alive?
   |-- yes --> refuse to start
   `-- no  --> remove stale lock and continue
```

Malformed/unreadable lock state and ambiguous OS process checks are handled
conservatively: Stage 3 keeps the lock and refuses to start. `--force-lock`
remains the manual escape hatch only after independently confirming the lock is
stale.

Each short-lived child uses a separate internal worker lock derived from the
supervisor lock path. If a child crashes and leaves that lock behind, the copied
worker's stale-PID logic removes it on the next child launch.

## Normal commands are unchanged

Preview a bounded selection without Azure calls:

```bash
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

Analyze one document:

```bash
python pipeline/regdocs_3_analyze.py --document-id 4647200
```

Analyze a batch:

```bash
python pipeline/regdocs_3_analyze.py --limit 1000
```

Analyze all remaining eligible documents:

```bash
python pipeline/regdocs_3_analyze.py --all
```

No separate supervisor command is required. `regdocs_3_analyze.py` is the
supervisor; `regdocs_3_analyze_worker.py` is an internal implementation detail.

## Cost warning

Stage 3 makes billable Azure calls unless selected work can be recovered from
existing artifacts or `--dry-run` is used. `--all` can therefore submit a large
amount of work.

The supervisor adds process-level retries, but it does **not** intentionally
resubmit normal handled failures. A hard crash after Azure has accepted a
billable operation but before the worker commits the result can still be
ambiguous: the supervisor cannot prove whether Azure completed the operation.
That risk already existed on hard crashes and remains a future hardening area.

For large PDFs, completed `Content-Range` parts are committed independently, so
a fresh worker can normally reuse already-finished ranges and avoid rebilling
those pages after a later crash.

Do not use `--force` merely to resume work. `--force` disables normal artifact
reconciliation and range-part reuse and can cause unnecessary resubmission.

## Candidate selection

A source candidate is a current Stage 2 file (`files.is_current = 1`). Unless
`--force` is supplied, a matching canonical success is skipped when the
processing identity matches:

```text
file_id + file_sha256 + analyzer_id + api_version
```

The supervisor performs a fast local canonical-artifact check while building
its queue. The worker remains authoritative for full artifact reconciliation.
If a success row has missing/stale/legacy output, the supervisor sends that
document through one worker so the worker can repair/canonicalize it locally
without an Azure call when possible.

Selection order remains by REGDOCS document ID. `--document-id` selects one ID,
`--limit` bounds the queue, and `--all` selects the full remaining queue.

## Large PDFs and `Content-Range`

PDFs over Azure's 300-page per-analysis limit are still handled automatically.
The worker counts pages locally with `pypdf` and submits inclusive 1-based
ranges of at most 300 pages.

Example for 986 pages:

```text
1-300
301-600
601-900
901-986
```

The original PDF is not physically split. The same source bytes are used with
the Azure `content_range` parameter, preserving the Stage 2 source identity.

Each successful range is validated and saved immediately under:

```text
workspace/3_analyze/content-understanding/raw/
  <analyzer>/<api-version>/<document-id>/
    <source-sha256>.json
    <source-sha256>.parts/
      pages-0001-0300.json
      pages-0001-0300.meta.json
      pages-0301-0600.json
      pages-0301-0600.meta.json
      ...
```

A normal retry or fresh crash-recovery worker can reuse valid completed range
parts. After all ranges are available, the worker requires the combined returned
page count to equal the local source page count before publishing the canonical
combined JSON and Markdown.

`Content-Range` solves the page-count limit only. The worker still reads the
full source file into memory and Azure input-byte limits still apply.

## Artifact layout

Default root:

```text
workspace/3_analyze/content-understanding/
```

Canonical artifacts:

```text
workspace/3_analyze/content-understanding/
├── raw/<analyzer>/<api-version>/<document-id>/<source-sha256>.json
├── markdown/<analyzer>/<api-version>/<document-id>/<source-sha256>.md
└── supervisor-state.json
```

Artifacts and supervisor state use `.partial` + atomic rename where applicable.
The canonical raw JSON remains the authoritative Stage 3 output consumed by
Stage 4.

## Authentication

Preferred authentication uses `DefaultAzureCredential`:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
az login
python pipeline/regdocs_3_analyze.py --limit 10
```

API-key mode:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"
python pipeline/regdocs_3_analyze.py --limit 10
```

`--key` remains accepted for compatibility but environment-based secret input is
preferred. When the supervisor launches a worker, the API key is passed through
the child environment rather than copied onto the child command line.

The Azure endpoint is not required merely to perform a no-cost reconciliation.
If a selected worker actually needs Azure and no endpoint is configured, that
worker exits with a configuration error and the supervisor stops.

Supported environment variables:

| Variable | Purpose |
|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Azure resource endpoint |
| `CONTENTUNDERSTANDING_KEY` | Optional API key |
| `CONTENTUNDERSTANDING_API_VERSION` | API version override |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | Analyzer override |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | Worker LRO polling interval |
| `CONTENTUNDERSTANDING_MAX_ATTEMPTS` | Worker Azure submission attempts |
| `CONTENTUNDERSTANDING_RETRY_BASE_DELAY` | Worker retry base delay |
| `CONTENTUNDERSTANDING_RETRY_MAX_DELAY` | Worker retry maximum delay |

## Worker retries vs supervisor retries

There are now two intentionally separate retry layers:

| Layer | Default | Handles |
|---|---:|---|
| Azure request retry (`--max-attempts`) | 4 | Retryable submission failures before Azure accepts the operation |
| Worker crash retry (`--worker-max-attempts`) | 3 | Child process death before a terminal DB result |

A normal `FAILED` analysis row means the child survived and recorded the
failure; the supervisor advances to the next document. A crash leaves no normal
terminal result, so the supervisor launches a fresh child.

Useful options:

```text
--worker-max-attempts N
--worker-sleep-seconds SECONDS
--retry-quarantined
--state-file PATH
```

## Artifact reconciliation and recovery

The worker still audits/reconciles matching artifacts before making Azure calls.
It can:

- verify canonical raw JSON;
- recover legacy/raw paths;
- regenerate missing Markdown from JSON;
- backfill ledger metadata;
- mark invalid successes stale;
- reuse completed large-PDF range parts.

Disable only for diagnosis:

```bash
python pipeline/regdocs_3_analyze.py --no-reconcile-artifacts --limit 10
```

That option also disables range-part reuse.

## Force semantics

```bash
python pipeline/regdocs_3_analyze.py --document-id 4647200 --force
```

`--force` intentionally bypasses successful-artifact recovery and range-part
reuse inside the worker. It can therefore resubmit every required Azure request
and overwrite the canonical artifact for that processing identity.

Use a normal command for crash recovery; use `--force` only when a true fresh
Azure analysis is desired.

## `--dry-run`

`--dry-run` still means **no Azure calls**, not read-only operation. The
supervisor can create its output/state directory and state file, and child
workers can create/update ledger run metadata and perform artifact audit or
canonicalization work.

Use it as a cost-safe selection preview, not as a zero-write audit.

## Ledger behavior

The copied worker continues writing `analyses`, `runs`, and `errors` using the
existing Stage 3 schema and processing identity. In the simple 3.5.0 durability
model, each child invocation creates its own Stage 3 `runs` row rather than one
single monolithic run row for the whole supervisor batch.

This is deliberate for the simple crash-isolation implementation: committed
per-document ledger/artifact state is the durable checkpoint, while the
supervisor JSON records process-level crash/quarantine state.

A future richer orchestration layer could add one aggregate parent run without
changing the per-document isolation model.

## CLI reference

Primary public options:

| Option | Effect |
|---|---|
| `--db PATH` | Override SQLite ledger |
| `--download-dir PATH` | Override Stage 2 file root |
| `--output-dir PATH` | Override Stage 3 artifact root |
| `--lock-file PATH` | Override long-lived supervisor lock |
| `--force-lock` | Remove an existing lock after independently confirming it is stale |
| `--state-file PATH` | Override durable supervisor state file |
| `--worker-max-attempts N` | Crash-level child retries before quarantine |
| `--worker-sleep-seconds S` | Delay between/retrying child processes |
| `--retry-quarantined` | Clear current quarantines and retry them |
| `--endpoint URL` | Azure endpoint override |
| `--key VALUE` | API key override; environment preferred |
| `--api-version VALUE` | Azure API version |
| `--analyzer-id VALUE` | Analyzer ID |
| `--document-id ID` | Select one document |
| `--limit N` | Select at most N eligible documents |
| `--all` | Select all remaining eligible documents |
| `--force` | Force fresh Azure analysis behavior inside worker |
| `--no-reconcile-artifacts` | Disable artifact/range-part recovery |
| `--no-verify-hash` | Disable Stage 2 source SHA-256 verification |
| `--polling-interval S` | Worker Azure LRO polling interval |
| `--max-attempts N` | Worker Azure request retry limit |
| `--retry-base-delay S` | Worker retry base delay |
| `--retry-max-delay S` | Worker retry maximum delay |
| `--dry-run` | No Azure calls |

## Exit codes

Supervisor exit codes:

| Code | Meaning |
|---:|---|
| `0` | Selected queue completed with no normal failures or new quarantines |
| `1` | One or more documents failed normally or were newly quarantined |
| `2` | Invalid/configuration/database/worker error |
| `130` | Interrupted by the user |

A worker killed directly by a Unix signal reports a negative return code to the
supervisor (for example `-11` for `SIGSEGV`). The supervisor stores both the
numeric exit code and decoded signal name in its state file.

## Remaining hardening priorities

The durable worker boundary solves queue death from native crashes, but it does
not solve every Stage 3 durability problem. Important future work remains:

1. persist accepted Azure operation IDs immediately and resume polling after a
   parent/worker restart;
2. add an aggregate parent `runs` record while retaining isolated child work;
3. add byte-volume/cost estimates and provider byte-limit preflight;
4. stream or otherwise cap source-memory usage for very large files;
5. make force/attempt history append-only instead of overwriting one unique
   analysis row;
6. add dedicated supervisor/status inspection and regression tests for signal
   crashes, quarantine reset, range recovery, and stale-lock recovery.

Previous: [Stage 2 downloader](regdocs_2_download.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
