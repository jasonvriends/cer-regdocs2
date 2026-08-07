# `regdocs_2_download.py`

Stage 2 of the REGDOCS acquisition pipeline: **select, download, verify, version, reconcile, and export metadata for source files discovered by the scout**.

```text
regdocs.db
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
downloads/ + files table + optional JSON sidecars
```

Script version documented: **1.1.1**.

## Purpose

The downloader consumes the exact five-table SQLite ledger created by `regdocs_1_scout.py`.

It does not crawl REGDOCS for new documents. Its job is to turn already-scouted file records into durable, verified local source files while preserving download state and file-version history.

## Default policy

By default the downloader:

- selects rows where `documents.is_file = 1`;
- skips known HTML records;
- never downloads folders;
- never downloads compound-document shells;
- never downloads synthetic paper-only rows;
- attempts unknown file kinds and identifies them from the response;
- stores current files as `downloads/<document-id>.<extension>`;
- streams in-progress downloads under `downloads/.partial/`;
- archives replaced versions under `downloads/_versions/<document-id>/`;
- reconciles files already present on disk before selecting network work;
- uses SQLite as the authoritative metadata ledger;
- uses one globally paced worker;
- waits a random `3–6` seconds between request starts;
- allows up to four HTTP attempts per selected document.

## Database contract

The downloader expects exactly these five user tables:

- `documents`
- `runs`
- `errors`
- `raw_snapshots`
- `files`

It refuses databases with missing or unexpected user tables.

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
downloads/.partial/
```

Only a completed and validated file is moved to its final location.

Default current-file naming:

```text
downloads/<document-id>.<extension>
```

Examples:

```text
downloads/4710492.pdf
downloads/4812031.docx
downloads/4910022.xlsx
```

This stable naming model makes the REGDOCS document ID the filesystem join key.

## Version archive

If a newly downloaded file replaces a different existing file for the same document ID, the prior file is archived by hash:

```text
downloads/_versions/<document-id>/<sha256>.<extension>
```

This preserves source history while keeping one simple current path.

Disable version archiving with:

```bash
python regdocs_2_download.py --no-archive-replaced
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
python regdocs_2_download.py --verify-existing
```

Disable reconciliation:

```bash
python regdocs_2_download.py --no-reconcile
```

## JSON sidecars

Stage 2 can export deterministic portable metadata sidecars.

For a current file:

```text
downloads/4710492.pdf
```

the default sidecar is:

```text
downloads/4710492.metadata.json
```

Generate after a normal download run:

```bash
python regdocs_2_download.py --sidecars
```

Generate or refresh sidecars without network access:

```bash
python regdocs_2_download.py --sidecars-only
```

Write sidecars to a separate directory:

```bash
python regdocs_2_download.py \
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

## Installation

Required:

```bash
pip install httpx
```

Recommended for progress bars:

```bash
pip install tqdm
```

## Common commands

### Preview selected work

```bash
python regdocs_2_download.py --dry-run --limit 25
```

### Normal download run

```bash
python regdocs_2_download.py
```

### Download one document

```bash
python regdocs_2_download.py --document-id 4710492
```

Repeat the option for multiple IDs:

```bash
python regdocs_2_download.py \
  --document-id 4710492 \
  --document-id 4710501
```

### Include HTML

```bash
python regdocs_2_download.py --include-html
```

### Retry records marked final-failed

```bash
python regdocs_2_download.py --retry-failed
```

### Force redownload

```bash
python regdocs_2_download.py --force
```

### Verify existing local files

```bash
python regdocs_2_download.py --verify-existing
```

### Download and create sidecars

```bash
python regdocs_2_download.py --sidecars
```

### Sidecars only

```bash
python regdocs_2_download.py --sidecars-only
```

### Status

```bash
python regdocs_2_download.py --status
```

Machine-readable:

```bash
python regdocs_2_download.py --status-json
```

### Offline self-test

```bash
python regdocs_2_download.py --self-test
```

### Version

```bash
python regdocs_2_download.py --version
```

## CLI reference

| Option | Default | Description |
|---|---:|---|
| `--db` | `regdocs.db` | Scout SQLite database |
| `--downloads`, `--output-dir` | `downloads` | Current source-file directory |
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
| `--audit-dir` | `_audit` | Log, progress, and lock directory |
| `--dry-run` | disabled | Preview selection without writes |
| `--status` | — | Human-readable latest status |
| `--status-json` | — | JSON latest status |
| `--version` | — | Print script version |
| `--self-test` | — | Run offline tests |
| `--verbose` | disabled | Debug logging |
| `--force-lock` | disabled | Replace a stale download lock |

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

`Retry-After` is honored when available.

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
python regdocs_2_download.py --max-file-size-mb 512
```

Both declared `Content-Length` and streamed bytes are checked.

## Progress and audit files

Default audit directory:

```text
_audit/
```

During a run:

```text
_audit/download-progress.json
_audit/download.log
_audit/download.lock
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

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | No final selected-document or sidecar failures |
| `2` | Run completed with one or more selected-document/sidecar failures |
| `1` | Configuration, database, or fatal runtime error |
| `130` | Interrupted; completed work remains committed |

## Self-test coverage

The offline self-test exercises core behavior including:

- exact five-table schema expectations;
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
python regdocs_2_download.py --self-test
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

Previous: [Stage 1 scout](README-regdocs_1_scout.md).
