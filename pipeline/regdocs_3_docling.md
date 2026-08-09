# `regdocs_3_docling.py`

Experimental local Stage 3 provider for REGDOCS. It analyzes the same verified
current Stage 2 source files with Docling so its extraction can be compared with
Azure Content Understanding.

The public command is a durable, single-threaded supervisor. Exactly one child
process analyzes one document at a time.

Script version documented: **3d.3.0**.

## Run ownership

One public Docling invocation equals one SQLite `runs` row.

```text
python regdocs_3_docling.py
        |
        v
Run 43  provider=docling
        |
        +--> document A worker --> analyses.run_id = 43
        +--> document B worker --> analyses.run_id = 43
        +--> document C worker --> analyses.run_id = 43
        `--> ...
```

The supervisor creates and finishes the run, maintains its heartbeat/progress,
and records its final status. The isolated child worker is attached to the
supervisor-owned run and does not allocate or finish another pipeline run during
normal public execution.

This matches the Azure Stage 3 rule: **one supervisor invocation = one run**.

## Shared Stage 3 lock

Azure and Docling are alternate Stage 3 providers over the same corpus and
shared SQLite ledger, so their public supervisors are mutually exclusive at
runtime.

Both use:

```text
database/locks/3_analyze.lock
```

Whichever provider starts first owns that lock for its whole processing run. If
Azure is active, starting Docling refuses to process; if Docling is active,
starting Azure refuses to process. The lock records the owning PID and provider
role, and a later invocation automatically removes it only when the recorded PID
is no longer running.

`python pipeline/regdocs_3_docling.py --status` remains read-only and does not
acquire the Stage 3 processing lock.

Do not use `--force-lock` unless you have independently confirmed that neither
an Azure nor a Docling Stage 3 supervisor is alive.

## Install

Docling is intentionally kept out of the core requirements because its runtime
and model dependencies are heavier:

```bash
python -m pip install -r pipeline/requirements-docling.txt
```

## Durable single-threaded execution

```text
regdocs_3_docling.py
        |
        +--> regdocs_3_docling_worker.py --document-id A
        |        wait for child exit
        |
        +--> regdocs_3_docling_worker.py --document-id B
        |        wait for child exit
        |
        +--> ...
```

A Docling/native-library crash, segfault, or OOM kill can terminate the current
child without terminating the supervisor. Completed analysis artifacts and
ledger rows remain committed.

There is never more than one Docling worker process at a time.

## Retry policy

Docling is local, so it keeps a different retry policy from Azure.

By default a document is attempted up to three times in fresh child processes.
If it still does not produce a matching successful `analyses` row, it is
quarantined and the supervisor continues with the next document.

Retry quarantined documents later with:

```bash
python pipeline/regdocs_3_docling.py --retry-quarantined
```

Azure deliberately does not do same-run retries because a resubmission can be
billable.

## Run

Process the remaining current corpus:

```bash
python pipeline/regdocs_3_docling.py
```

Inspect progress without creating a run or taking the Stage 3 processing lock:

```bash
python pipeline/regdocs_3_docling.py --status
```

Bound a pilot by child launches:

```bash
python pipeline/regdocs_3_docling.py --max-documents 10
```

Normal progress intentionally mirrors the Azure console shape. The progress
position is zero-padded to the width of the selected document count, and the
success line is built from the committed SQLite analysis row:

```text
Run 43: Docling supervisor 3d.3.0; 4048 document(s) eligible
Concurrency:    1 child process
Crash retries:  up to 3 attempt(s) per document
Stage 3 lock:   /.../database/locks/3_analyze.lock

[0001/4048] 4659445 ... SUCCEEDED pages=1 tables=1 sections=5 elapsed=3.9s
[0002/4048] 4659447 ... SUCCEEDED pages=12 tables=0 sections=8 elapsed=6.2s
```

If a document needs another fresh child, its document position stays stable and
the retry is made explicit:

```text
[0027/4048] 4660123 ... FAILED exit=-11 signal=SIGSEGV; retrying in fresh child
[0027/4048] 4660123 attempt 2/3 ... SUCCEEDED pages=34 tables=3 sections=14 elapsed=11.4s
```

Worker stdout/stderr remains hidden on normal success and is surfaced for
failure/crash diagnostics.

## Durable state

Launcher state is stored at:

```text
workspace/3_analyze/docling/supervisor-state.json
```

The SQLite `analyses` ledger remains authoritative for completed analysis
identity. The state file records launcher attempts, abnormal exits, signals,
quarantine state, and the last parent pipeline run ID.

## Artifacts and ledger identity

Docling writes under:

```text
workspace/3_analyze/docling/
├── raw/docling-standard/<docling-version>/<document-id>/<sha256>.json
└── markdown/docling-standard/<docling-version>/<document-id>/<sha256>.md
```

The existing `analyses` table is reused with:

```text
analyzer_id = docling-standard
api_version = installed Docling package version
artifact_source = docling
run_id = supervisor-owned Docling run
```

Both Azure and Docling persist the common extraction/result metrics used by the
supervisor console and later comparison work, including:

```text
page_count
table_count
section_count
warning_count
elapsed_seconds
attempt_count
status
error_code
error_message
raw_json_path
markdown_path
```

So the console is only a view of durable ledger data; the SQLite row and native
provider artifacts remain the source of truth.

The raw JSON preserves the native `DoclingDocument.export_to_dict()` result
under `regdocsDocling.native` and also contains a conservative REGDOCS
compatibility projection under `contents[]` for the current Stage 4 normalizer.

The compatibility projection is intentionally experimental. It maps text, page
provenance, basic headings, and table structure into the subset of the current
normalization contract needed for comparison work; it does not claim that Azure
and Docling have equivalent native schemas.

## Stage 4 provider choice

For the initial multi-provider implementation:

```bash
python pipeline/regdocs_4_normalize_provider.py --analysis-provider azure
python pipeline/regdocs_4_normalize_provider.py --analysis-provider docling
```

Use a separate output directory for pilots when appropriate. The Docling
provider selector resolves the newest successful `docling-standard` version
recorded in the ledger. Azure retains the existing
`prebuilt-layout / 2025-11-01` default.

Alternative Stage 3 provider: [Azure](regdocs_3_azure.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
