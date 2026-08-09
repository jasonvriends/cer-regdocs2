# `regdocs_2_download.py`

Stage 2 of the REGDOCS acquisition pipeline: **select, download, verify, version, reconcile, and export metadata for source files discovered by the scout**.

```text
database/regdocs.db
   |
   v
eligible file rows
   |
   +--> reconcile existing files
   |
   +--> download with pacing/retries
   |
   +--> detect + validate type
   |
   +--> SHA-256
   |
   +--> archive replaced versions
   |
   v
workspace/2_download/files/ + files table + optional JSON sidecars
```

Script version documented: **1.1.2**.

## Purpose

The downloader consumes the shared SQLite ledger created by `regdocs_1_scout.py`.

It does not crawl REGDOCS for new documents. Its job is to turn already-scouted
file records into managed local source files while preserving download state
and file-version history. Download-time validation is strong, but the current
reconciliation and crash-recovery limitations below matter when calling the
collection fully verified or durable.

## Default policy

By default the downloader:

- selects rows where `documents.is_file = 1`;
- skips known HTML records;
- never downloads folders;
- never downloads compound-document shells;
- never downloads synthetic paper-only rows;
- attempts unknown file kinds and identifies them from the response;
- stores current files as `workspace/2_download/files/<document-id>.<extension>`;
- streams in-progress downloads under `workspace/2_download/files/.partial/`;
- archives replaced versions under `workspace/2_download/files/_versions/<document-id>/`;
- reconciles files already present on disk before selecting network work;
- uses SQLite as the authoritative metadata ledger;
- uses one globally paced worker;
- waits a random `3–6` seconds between request starts;
- allows up to four HTTP attempts per selected document.

## Database contract

The downloader requires these five acquisition tables:

- `documents`
- `runs`
- `errors`
- `raw_snapshots`
- `files`

It refuses databases missing an acquisition table, while allowing additive
tables owned by downstream stages such as `analyses` and `normalizations`.

Stage 2 creates no additional user tables.

## Selection logic

Candidate selection begins with:

```sql
documents.is_file = 1
```

Records are then filtered according to status and options.

Without `--force`:

- `SUCCEEDED` records are skipped;
- `FAILED_FINAL` records are skipped unless `--retry-failed` is supplied;
- `NOT_APPLICABLE` records are skipped.

Known HTML records are skipped unless `--include-html` is supplied.

Non-file records are normalized to:

```text
download_status = NOT_APPLICABLE
```

when appropriate.

## File type detection

The downloader does not trust filename extensions alone.

Detection can use:

1. file magic / leading bytes;
2. ZIP package contents for Office Open XML;
3. HTTP `Content-Type`;
4. server-provided filename;
5. final URL filename;
6. REGDOCS catalogue kind;
7. binary fallback.

Recognized examples include:

- PDF;
- PNG;
- JPEG;
- GIF;
- TIFF;
- RTF;
- DOCX;
- XLSX;
- PPTX;
- ZIP;
- HTML/XHTML.

The detection method and confidence are stored in metadata.

For catalogue entries expected to be PDF, a non-PDF response is treated as a type mismatch rather than silently accepted.

## Download validation

Before a file becomes current, the downloader verifies that:

- the temporary file exists;
- the response body is non-empty;
- the configured maximum size was not exceeded;
- the detected type is plausible;
- a detected PDF begins with `%PDF-`.

The entire file is hashed with SHA-256.

SHA-256 is the definitive local version identity.

## Filesystem commit model

Downloads are first streamed into:

```text
workspace/2_download/files/.partial/
```

Only a completed and validated file is moved to its final location.

Default current-file naming:

```text
workspace/2_download/files/<document-id>.<extension>
```

Examples:

```text
workspace/2_download/files/4710492.pdf
workspace/2_download/files/4812031.docx
workspace/2_download/files/4910022.xlsx
```

This stable naming model makes the REGDOCS document ID the filesystem join key.

## Version archive

If a newly downloaded file replaces a different existing file for the same document ID, the prior file is archived by hash:

```text
workspace/2_download/files/_versions/<document-id>/<sha256>.<extension>
```

This preserves source history while keeping one simple current path.

Disable version archiving with:

```bash
python pipeline/regdocs_2_download.py --no-archive-replaced
```

## Existing-file reconciliation

Reconciliation runs by default before new downloads.

It can:

- discover existing `<document-id>.<extension>` files;
- compare them with recorded file paths;
- validate detected types;
- adopt valid local files into the ledger;
- restore missing ledger/file relationships;
- reset records to `PENDING` when a file marked current is missing;
- record reconciliation problems in the `errors` table.

By default, an already recorded hash may be reused during reconciliation. To re-hash existing files:

```bash
python pipeline/regdocs_2_download.py --verify-existing
```

Use `--verify-existing` for integrity-sensitive runs. Without it, a replaced or
corrupt file can be adopted while retaining a previously recorded SHA-256.
Also note that reconciliation is currently global: even a targeted
`--document-id` or `--limit` run can reconcile and reset unrelated file rows.

Disable reconciliation:

```bash
python pipeline/regdocs_2_download.py --no-reconcile
```

## JSON sidecars

Stage 2 can export deterministic portable metadata sidecars.

For a current file:

```text
workspace/2_download/files/4710492.pdf
```

the default sidecar is:

```text
workspace/2_download/files/4710492.metadata.json
```

Generate after a normal download run:

```bash
python pipeline/regdocs_2_download.py --sidecars
```

Generate or refresh sidecars without network access:

```bash
python pipeline/regdocs_2_download.py --sidecars-only
```

Write sidecars to a separate directory:

```bash
python pipeline/regdocs_2_download.py \
  --sidecars-only \
  --sidecar-dir export/metadata
```

Sidecars are deterministically serialized. If the bytes would be unchanged, the existing sidecar is not rewritten.

### Sidecar content

The sidecar projects authoritative SQLite state into a portable document including:

- document ID;
- title;
- source URL;
- item kind;
- filing date;
- submitter;
- company;
- project;
- filing number;
- snippet;
- content type;
- extension;
- SHA-256;
- local file facts;
- pipeline statuses and timestamps;
- the full `documents.metadata` JSON object.

This makes sidecars convenient inputs for downstream OCR, content-understanding, ETL, or RAG jobs while keeping SQLite as the source of truth.

Sidecars contain the full metadata object and local paths. Treat them as
operational exports, review them before sharing, and create them under suitable
filesystem permissions. `--sidecars-only` trusts ledger/file relationships; it
does not re-hash the source bytes.

## Installation

From the repository root, install the shared pipeline dependencies:

```bash
python -m pip install -r pipeline/requirements.txt
```

The shared file includes both `httpx` and `tqdm` used by this stage.

## Common commands

### Preview selected work

```bash
python pipeline/regdocs_2_download.py --dry-run --limit 25
```

### Normal download run

```bash
python pipeline/regdocs_2_download.py
```

### Download one document

```bash
python pipeline/regdocs_2_download.py --document-id 4710492
```

Repeat the option for multiple IDs:

```bash
python pipeline/regdocs_2_download.py \
  --document-id 4710492 \
  --document-id 4710501
```

### Include HTML

```bash
python pipeline/regdocs_2_download.py --include-html
```

### Retry records marked final-failed

```bash
python pipeline/regdocs_2_download.py --retry-failed
```

### Force redownload

```bash
python pipeline/regdocs_2_download.py --force
```

### Verify existing local files

```bash
python pipeline/regdocs_2_download.py --verify-existing
```

### Download and create sidecars

```bash
python pipeline/regdocs_2_download.py --sidecars
```

### Sidecars only

```bash
python pipeline/regdocs_2_download.py --sidecars-only
```

### Status

```bash
python pipeline/regdocs_2_download.py --status
```

Machine-readable:

```bash
python pipeline/regdocs_2_download.py --status-json
```

### Offline self-test

```bash
python pipeline/regdocs_2_download.py --self-test
```

### Version

```bash
python pipeline/regdocs_2_download.py --version
```

## CLI reference

| Option | Default | Description |
|---|---:|---|
| `--db` | `database/regdocs.db` | Scout SQLite database |
| `--downloads`, `--output-dir` | `workspace/2_download/files` | Current source-file directory |
| `--document-id` | none | Select one document ID; repeatable |
| `--limit` | none | Process at most N selected records |
| `--include-html` | disabled | Download known HTML records |
| `--force` | disabled | Redownload successful/current records |
| `--retry-failed` | disabled | Retry `FAILED_FINAL` records |
| `--attempts` | `4` | Maximum HTTP attempts per selected document |
| `--concurrency` | `1` | Maximum active downloads |
| `--min-delay` | `3.0` | Minimum global request-start delay |
| `--max-delay` | `6.0` | Maximum global request-start delay |
| `--connect-timeout` | `30.0` | Connection timeout in seconds |
| `--read-timeout` | `300.0` | Read/write timeout in seconds |
| `--max-file-size-mb` | `2048.0` | Maximum accepted file size |
| `--[no-]reconcile` | enabled | Reconcile local files before selecting work |
| `--verify-existing` | disabled | Re-hash existing files during reconciliation |
| `--[no-]archive-replaced` | enabled | Preserve replaced file versions |
| `--sidecars`, `--write-sidecars` | disabled | Write metadata sidecars after the run |
| `--sidecars-only` | disabled | Write sidecars without network downloads |
| `--sidecar-dir` | beside source file | Alternate sidecar directory |
| `--partial-max-age-hours` | `24.0` | Delete stale partials older than this |
| `--audit-dir` | `workspace/2_download/run` | Log and progress directory |
| `--lock-file` | `database/locks/2_download.lock` | Exclusive stage lock |
| `--dry-run` | disabled | Read-only candidate preview; no reconciliation or download |
| `--status` | — | Human-readable latest status |
| `--status-json` | — | JSON latest status |
| `--version` | — | Print script version |
| `--self-test` | — | Run offline tests |
| `--verbose` | disabled | Debug logging |
| `--force-lock` | disabled | Unconditionally remove the lock; only after proving no downloader is running |

## Download metadata written to the ledger

On success, `documents.metadata.download` can include facts such as:

- status;
- original source URL;
- resolved/final URL;
- catalogue kind;
- original server filename;
- local filename/path;
- extension;
- content type;
- size;
- SHA-256;
- `ETag`;
- `Last-Modified`;
- `Content-Disposition`;
- detection method/confidence;
- download timestamp;
- run ID;
- attempt history;
- script/parser version.

Selected file facts are also projected to top-level document metadata for convenience.

## `files` table and current versions

Each distinct observed file hash can be represented as a version in `files`.

Important fields include:

- document ID;
- path;
- original filename;
- MIME type;
- extension;
- size;
- SHA-256;
- download timestamp;
- `is_current`.

Before a new current version is recorded, prior rows for the document are marked non-current.

This separates **document identity** from **file version identity**.

## Retry behavior

Temporary HTTP/network failures can be retried.

Retryable HTTP statuses include:

- `408`
- `425`
- `429`
- `500`
- `502`
- `503`
- `504`

`Retry-After` is honored when available. It is not currently capped, so an
unexpectedly large server value can stall the run.

Without it, retries use a bounded exponential delay.

The script records attempt-level facts, including:

- attempt number;
- timestamps;
- duration;
- HTTP status;
- success/failure;
- error code;
- bytes received;
- resolved URL;
- retryability;
- retry delay where applicable.

## File-size guard

The default maximum file size is:

```text
2048 MB
```

Change it with:

```bash
python pipeline/regdocs_2_download.py --max-file-size-mb 512
```

Both declared `Content-Length` and streamed bytes are checked.

## Progress and audit files

Default run-state directory:

```text
workspace/2_download/run/
```

During a run:

```text
workspace/2_download/run/progress.json
workspace/2_download/run/download.log
database/locks/2_download.lock
```

The progress JSON contains:

- run ID;
- status/phase;
- completed and total units;
- heartbeat;
- progress message;
- counters.

## Statuses

Typical download statuses include:

- `PENDING`
- `RUNNING`
- `SUCCEEDED`
- `SKIPPED_HTML`
- `NOT_APPLICABLE`
- `FAILED_RETRYABLE`
- `FAILED_FINAL`

A successfully downloaded document also receives:

```text
documents.status = DOWNLOADED
```

## Selection, reconciliation, and preview scope

Candidate download selection honors `--document-id` and `--limit`, but the
pre-download reconciliation and non-file normalization passes currently scan
the full ledger. A one-document smoke test is therefore not isolated from other
document state.

An explicit unknown or ineligible `--document-id` can result in a successful
zero-work run. Confirm that the expected ID appears in preview output.

A normal `--dry-run` opens the ledger read-only, does not reconcile, does not
acquire the stage lock, and does not download files. The special combination
`--sidecars-only --dry-run` does acquire and release the lock even though it
does not write sidecars.

With `--sidecars --limit N`, sidecar selection is based on the first N
successful ledger rows, not necessarily the N documents downloaded by that
invocation.

## Locking and concurrent runs

The downloader uses:

```text
database/locks/2_download.lock
```

Stale partial cleanup currently occurs before the lock is acquired. The lock
does not validate process liveness or ownership, and `--force-lock`
unconditionally unlinks it. Never run two downloaders against the same ledger
and output tree. Verify the original process is stopped before forcing a lock.

## Failure handling

Final failures are written to the shared `errors` table.

The downloader records structured context such as:

- source URL;
- HTTP status;
- resolved URL;
- selected response headers;
- bytes received;
- partial hash when available;
- attempt history.

A later successful download resolves prior unresolved download errors for that document without deleting the historical rows.

An invalid file found during reconciliation can currently leave its document
at `SUCCEEDED`, where normal selection skips it. Review reconciliation errors
and use `--verify-existing`; do not rely on status alone as an integrity audit.

A failed forced refresh can also demote the download status even while a
previous current file remains available. Availability of the last verified
artifact and outcome of the latest refresh attempt are not yet separate states.

The filesystem archive/promotion steps and their SQLite updates are not one
crash-atomic transaction. A hard crash at exactly the wrong point can require
manual hash/path reconciliation. The fallback used for some cross-filesystem
moves is not atomic. Keep database and file backups together and run an
integrity verification after abnormal termination.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | No final selected-document or sidecar failures |
| `2` | Run completed with one or more selected-document/sidecar failures |
| `1` | Configuration, database, or fatal runtime error |
| `130` | Interrupted; completed work remains committed |

## Self-test coverage

The offline self-test exercises core behavior including:

- required five-table acquisition schema with additive downstream tables allowed;
- candidate selection;
- HTML skipping;
- non-file normalization;
- adoption of an existing PDF;
- SHA-256 recording;
- file version/current-row behavior;
- type detection;
- content-disposition filename parsing;
- deterministic metadata sidecars;
- version replacement/archive behavior.

Run it before deploying script changes:

```bash
python pipeline/regdocs_2_download.py --self-test
```

## Future update-check mode

The source code documents a planned lightweight update-scan design.

The intended approach is:

1. reuse stored `ETag` and/or `Last-Modified`;
2. send conditional requests using `If-None-Match` / `If-Modified-Since`;
3. treat `304 Not Modified` as unchanged without transferring the file body;
4. for `200 OK`, stream and validate the file;
5. compare SHA-256 before deciding that a new version exists.

The design explicitly keeps SHA-256 as the definitive version identity. HTTP validators are only optimization signals.

This mode is **not implemented in the current script**.

## Current trust and security limits

The current HTTP client follows redirects without enforcing a scheme, host, or
private-network policy. A poisoned ledger URL or hostile redirect can make the
downloader contact an unintended destination. Run against trusted Stage 1 data
until every request and redirect is constrained to approved HTTPS CER hosts.

Document IDs are used in filenames, globs, temporary prefixes, and archive
directories without a centralized strict path-component check. Reconciliation
can also encounter symlinks. Keep the managed download tree private and free of
untrusted filenames/symlinks.

Downloaded documents are untrusted external bytes. Downstream parsing should
run with resource limits and isolation; a quarantine or malware-scanning hook
is not currently present.

## Current hardening priorities

Before unattended production use, prioritize:

1. acquire a robust lock before partial cleanup and supervise worker failures so
   an unexpected exception cannot leave `queue.join()` waiting forever;
2. always hash adopted/changed files, quarantine invalid files, and make status
   reflect reconciliation failures;
3. add a filesystem/SQLite intent journal with durable directory sync and
   startup recovery for every archive/promotion crash point;
4. scope repair work to explicit selection unless `--reconcile-all` is chosen;
5. enforce approved HTTPS destinations, safe document/path components, and
   symlink containment;
6. preserve a known-good current artifact when a forced refresh fails;
7. add schema migrations and a uniqueness constraint for one current file per
   document;
8. bound retry waits, transfer duration, disk use, and numeric arguments;
9. separate audit, repair, quarantine, and download modes, then add conditional
   GET and resumable-download support;
10. add local HTTP, concurrency, signal, corruption, and crash fault-injection
    tests.

## Relationship to downstream document intelligence

The downloader should hand downstream processing a stable pair:

```text
<document-id>.<extension>
<document-id>.metadata.json    # optional
```

plus the authoritative SQLite ledger.

A downstream processor should normally key its cache on:

```text
document_id + source_sha256 + processor_version
```

That allows OCR, extraction, chunking, and embeddings to be rerun only when the source bytes or processing logic change.

Previous: [Stage 1 scout](regdocs_1_scout.md).
