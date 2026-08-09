# `regdocs_3_docling.py`

Experimental Stage 3b of REGDOCS: analyze the same verified current Stage 2
source files locally with Docling so its output can be compared with Azure
Content Understanding.

This is an alternate analysis backend, not a replacement decision.

## Install

Docling is intentionally kept out of the core requirements because its runtime
and model dependencies are much heavier:

```bash
python -m pip install -r pipeline/requirements-docling.txt
```

## Durable single-threaded execution

`regdocs_3_docling.py` is the public entry point. It is intentionally a
durable, single-threaded launcher: exactly one document is analyzed in one
child process at a time.

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

The worker implementation is internal plumbing. Normal operations should call
`regdocs_3_docling.py`, not `regdocs_3_docling_worker.py`.

This process boundary is deliberate. A Docling/native-library crash, segfault,
or OOM kill can terminate the current child without terminating the launcher.
Completed analysis artifacts and ledger rows remain committed, and the launcher
continues or retries from durable state.

There is never more than one Docling worker process at a time.

## Run

Process the remaining current corpus:

```bash
python pipeline/regdocs_3_docling.py
```

Inspect progress:

```bash
python pipeline/regdocs_3_docling.py --status
```

Bound a pilot by number of launched documents:

```bash
python pipeline/regdocs_3_docling.py --max-documents 10
```

By default a document is attempted up to three times. If it still does not
produce a matching successful `analyses` row, it is quarantined and processing
continues with the next document instead of getting stuck forever.

Retry quarantined documents later with:

```bash
python pipeline/regdocs_3_docling.py --retry-quarantined
```

Durable launcher state is stored at:

```text
workspace/3_analyze/docling/supervisor-state.json
```

The SQLite `analyses` ledger remains authoritative for completed analysis
identity; the state file records launcher attempts, abnormal exits, signals,
and quarantine state.

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
```

The raw JSON preserves the complete native `DoclingDocument.export_to_dict()`
result under `regdocsDocling.native`. It also contains a conservative REGDOCS
compatibility projection under `contents[]` so the current Stage 4 normalizer
can consume Docling results while the native Docling representation remains
available for comparison and future provider-specific adapters.

The compatibility projection is intentionally experimental. It maps text,
page provenance, basic headings, and table structure into the subset of the
existing Stage 4 input contract needed for normalization. It does not claim
that Docling and Azure have equivalent native schemas or semantics.

## Stage 4 provider choice

For the initial multi-provider implementation, use the provider launcher:

```bash
python pipeline/regdocs_4_normalize_provider.py --analysis-provider azure
python pipeline/regdocs_4_normalize_provider.py --analysis-provider docling
```

All normal Stage 4 options can be passed through, for example:

```bash
python pipeline/regdocs_4_normalize_provider.py \
  --analysis-provider docling \
  --document-id 4647200 \
  --output-dir workspace/4_normalize/docling-pilot
```

Use a separate output directory for pilots. Like the canonical Stage 4 command,
a filtered non-dry run writes a complete replacement output set to its chosen
output directory.

The Docling provider selector resolves the newest successful
`docling-standard` version recorded in the ledger. Azure retains the existing
`prebuilt-layout / 2025-11-01` default.
