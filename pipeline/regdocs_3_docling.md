# `regdocs_3_docling.py`

Experimental local Stage 3 provider for REGDOCS. It analyzes the same verified
current Stage 2 source files with Docling so its extraction can be compared with
Azure Content Understanding.

The public command is a durable, single-threaded supervisor. Exactly one child
process analyzes one document at a time.

Script version documented: **3d.3.1**.

## Run ownership

One public Docling invocation equals one SQLite `runs` row.

```text
python pipeline/regdocs_3_docling.py
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
and records final status. Isolated workers attach their `analyses` rows to the
supervisor-owned run instead of allocating one run per document.

This matches the Azure Stage 3 rule: **one supervisor invocation = one run**.

## Shared Stage 3 lock

Azure and Docling are alternate Stage 3 providers over the same corpus and
shared SQLite ledger, so their public supervisors are mutually exclusive.

Both use:

```text
database/locks/3_analyze.lock
```

Whichever provider starts first owns that lock for its whole processing run. A
later invocation removes a stale lock automatically only when the recorded PID
is no longer alive.

`python pipeline/regdocs_3_docling.py --status` remains read-only with respect to
the Stage 3 processing lock.

Do not use `--force-lock` unless you have independently confirmed that neither
an Azure nor a Docling supervisor is active.

## Install

Docling is intentionally kept out of the core requirements because its runtime
and model dependencies are heavier:

```bash
python -m pip install -r pipeline/requirements-docling.txt
```

## Durable single-active-child execution

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

There is never more than one Docling document worker active at a time. This
keeps document-level SQLite writes serialized, limits GPU/resource contention,
and preserves a simple crash boundary.

A Docling/native-library crash, segfault, or OOM kill can terminate the current
child without terminating the supervisor. Completed analysis artifacts and
ledger rows remain committed.

The roadmap contains a separate future optimization to let one active child
reuse loaded Docling/PyTorch models for a bounded group of roughly 25–100
documents before recycling. That proposal still keeps exactly one active child
and one current document-level writer; it is not concurrent document analysis.

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
python pipeline/regdocs_3_docling.py --max-documents 25
```

Normal progress mirrors the Azure console shape. The success line is built from
the committed SQLite analysis row.

## Docling conversion status and errors

The worker does not infer success merely because `converter.convert()` returned
a document object.

For every conversion it now reads Docling's conversion result status and error
list before publishing the artifact:

- `success` is accepted as a successful analysis;
- `partial_success` is accepted but preserved with warnings;
- any other status is recorded as a failed analysis;
- Docling conversion errors are serialized into the raw artifact;
- the `analyses.warning_count` reflects the preserved conversion warnings; and
- failed conversions retain their error code/message in the `analyses` row.

The raw artifact records this provider-native outcome under:

```text
regdocsDocling.conversionStatus
regdocsDocling.conversionErrors
warnings
```

This makes Azure-versus-Docling comparison able to distinguish clean success
from partial extraction instead of reporting every returned Docling document as
warning-free success.

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

Common durable result fields include:

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

The raw JSON preserves `DoclingDocument.export_to_dict()` under
`regdocsDocling.native` and contains a conservative REGDOCS compatibility
projection under `contents[]` for the current Stage 4 normalizer.

The compatibility projection is intentionally experimental. It maps text, page
provenance, basic headings, and table structure into the subset of the current
normalization contract needed for comparison work; it does not claim Azure and
Docling have equivalent native schemas.

## Stage 4 provider choice

`regdocs_4_normalize.py` is the primary Stage 4 command. Choose the Docling
analysis explicitly:

```bash
python pipeline/regdocs_4_normalize.py --analysis-provider docling
```

When run interactively with no `--analysis-provider`, Stage 4 asks whether to
use Azure or Docling. The Docling selection resolves the newest successful
current `docling-standard` version in the ledger.

The older `regdocs_4_normalize_provider.py` command remains only as a
compatibility launcher and delegates to `regdocs_4_normalize.py`.

Alternative Stage 3 provider: [Azure](regdocs_3_azure.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
