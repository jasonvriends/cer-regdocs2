# `regdocs_3_docling.py`

Experimental local Stage 3 provider for REGDOCS. It analyzes the same verified
current Stage 2 source files with Docling so its extraction can be compared with
Azure Content Understanding.

The public command is a durable, single-threaded supervisor. Exactly one child
process analyzes one document at a time.

Script version documented: **3d.2.0**.

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

Inspect progress without creating a run:

```bash
python pipeline/regdocs_3_docling.py --status
```

Bound a pilot by child launches:

```bash
python pipeline/regdocs_3_docling.py --max-documents 10
```

Normal output identifies the parent pipeline run once and then shows child
progress without creating a new run number for every document.

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
