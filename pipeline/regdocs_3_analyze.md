# `regdocs_3_analyze.py`

Stage 3 of the REGDOCS processing pipeline: **submit verified current source
files to Azure AI Content Understanding and preserve the returned layout JSON
and Markdown with ledger provenance**.

PDFs larger than Azure's 300-page per-analysis limit are handled automatically
with `Content-Range` requests. The source PDF is not physically split.

```text
database/regdocs.db + workspace/2_download/files/
                         |
                         v
                 validate source hash
                         |
                         v
              inspect PDF page count
                         |
             +-----------+-----------+
             |                       |
        <= 300 pages             > 300 pages
             |                       |
             |                1-300, 301-600, ...
             |                       |
             +-----------+-----------+
                         v
              Azure Content Understanding
                         |
                         +--> raw JSON
                         +--> Markdown
                         v
                  analyses + runs
```

Script version documented: **3.4.0**.

## Purpose and boundary

The analyzer consumes current files recorded by Stage 2. It uses Azure's
`prebuilt-layout` analyzer by default, records the operation result in the
shared SQLite ledger, and writes reusable Stage 3 artifacts.

This stage:

- makes billable external API calls unless every selected item is reconciled
  from existing artifacts or `--dry-run` is used;
- sends selected source-file bytes to the configured Azure endpoint;
- automatically analyzes PDFs over 300 pages in inclusive 1-based page ranges;
- does not discover REGDOCS records or download source files;
- does not produce the final normalized search corpus.

Stage 4 performs local normalization of the Stage 3 result.

## Large PDFs and `Content-Range`

Stage 3 counts PDF pages locally with `pypdf`. A PDF with more than 300 pages is
submitted as a sequence of Azure analyses with at most 300 source pages each.
For example, a 986-page PDF is analyzed as:

```text
1-300
301-600
601-900
901-986
```

The same original PDF bytes are sent for each ranged request with the Azure
`content_range` parameter. REGDOCS does **not** create derivative split PDFs,
so the Stage 2 source SHA-256 and document identity remain unchanged.

Each successful range is validated before it is accepted. Azure must return
exactly the number of pages requested for that range. After all ranges are
available, Stage 3 also requires the total returned page count to equal the
local PDF page count before publishing the canonical combined artifact.

The combined JSON preserves each ranged Azure response as a separate
`contents[]` contribution instead of rewriting cross-range spans or offsets.
Stage 4 processes each `contents[]` object independently. Beginning with Stage
4.1.0, normalized provenance also qualifies Azure-local paragraph/table/figure
pointers with that `contents[]` index, so the separate ranged responses remain
exactly dereferenceable after normalization.

The canonical JSON receives additional metadata:

```json
{
  "regdocsChunking": {
    "strategy": "content_range",
    "sourcePageCount": 986,
    "validatedPageCount": 986,
    "maxPagesPerRequest": 300,
    "rangeCount": 4,
    "parts": [
      {"range": "1-300", "rawJsonPath": "...", "metaJsonPath": "..."}
    ]
  }
}
```

### Range artifacts and restart behavior

Every completed range is committed immediately under the canonical raw JSON
identity:

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

The `.meta.json` file records the document ID, source SHA-256, analyzer, API
version, range, returned page count, operation ID, and completion time.

If a later range fails or the process is interrupted, a normal rerun can reuse
valid completed range artifacts and avoid resubmitting those pages. A cached
range is reused only when its processing identity matches and its returned page
count matches the requested range.

`--force` and `--no-reconcile-artifacts` disable range-part reuse as well as
their existing reconciliation behavior. Use a normal rerun when the goal is to
resume an interrupted large PDF without rebilling completed ranges.

### What ranging does not solve

`Content-Range` solves the **page-count** limit, not the source-file byte limit.
The implementation still reads the full file into memory and sends the same
binary for every ranged request. A PDF that exceeds Azure's allowed input size
still requires a different strategy even when each request selects at most 300
pages.

Check current Azure service limits before large runs because provider limits can
change independently of this repository.

## Cost warning

Stage 3 requires exactly one selection scope: `--document-id`, `--limit`, or
`--all`. The explicit `--all` acknowledgement selects every eligible current
file that lacks a matching successful analysis. In a large ledger, that can
cause thousands of billable submissions.

A large PDF can require multiple Azure submissions. `attempt_count` and the run
summary count actual Azure submission attempts, so a successful 986-page PDF
normally contributes four attempts when no retries or recovered range parts are
involved.

Always inspect a bounded selection first:

```bash
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

Then begin with one document or a small limit. The script still has no built-in
price estimate, byte budget, or cost budget. `--all` confirms selection scope,
not cost.

## Inputs and candidate selection

The default database is:

```text
database/regdocs.db
```

The default source-file root is:

```text
workspace/2_download/files/
```

A candidate must be a row in `files` where `is_current = 1`. Unless `--force`
is supplied, Stage 3 skips a file when `analyses` already contains a
`SUCCEEDED` row with the same:

```text
file_id + file_sha256 + analyzer_id + api_version
```

Selection is ordered by REGDOCS document ID. `--document-id` selects one ID,
`--limit` truncates the selected list, and `--all` explicitly selects the full
remaining eligible set.

The selected file path is resolved from the stored ledger path, configured
download directory, and normal `<document-id>.<extension>` fallback.

## Supported source types

The script currently admits these filename extensions:

```text
pdf
png jpg jpeg tif tiff bmp heif
docx xlsx pptx
txt html htm md rtf xml json csv tsv
eml msg
```

PDFs receive the local page-count/ranging behavior described above. Other
content types retain the single-request Stage 3 path.

An unrecognized extension is recorded as `SKIPPED_UNSUPPORTED` without an
Azure submission.

## Analysis identity and ledger state

Stage 3 owns the additive `analyses` table. Its unique processing identity is:

```text
file_id + file_sha256 + analyzer_id + api_version
```

Important fields include:

| Field | Meaning |
|---|---|
| `document_id` / `file_id` | Source identities from Stages 1 and 2 |
| `file_sha256` | Exact source-byte version |
| `analyzer_id` / `api_version` | Azure processing contract |
| `operation_id` | Azure operation ID; ranged analyses store comma-separated completed operation IDs |
| `status` | Current state for this analysis identity |
| `raw_json_path` / `markdown_path` | Canonical artifact paths |
| count fields | Pages, tables, sections, and warnings observed |
| `attempt_count` / `elapsed_seconds` | Azure submissions and elapsed time |
| `artifact_source` / `reconciled_at` | Whether output came from Azure/ranged Azure or local recovery |
| error fields | Final error code and message |

Every invocation also records a `runs` row with parameters, progress,
heartbeat, counters, and summary. The key value itself is not stored, but the
endpoint and authentication mode are recorded.

## Artifact layout

The default artifact root is:

```text
workspace/3_analyze/content-understanding/
```

Canonical paths are keyed by analyzer, API version, document ID, and source
SHA-256:

```text
workspace/3_analyze/content-understanding/
├── raw/<analyzer>/<api-version>/<document-id>/<source-sha256>.json
└── markdown/<analyzer>/<api-version>/<document-id>/<source-sha256>.md
```

Large PDFs additionally keep the `.parts/` directory described above.

Each artifact is written through a `.partial` path and then renamed over the
final path. The canonical JSON is authoritative. Markdown is also extracted to
a separate file for convenient inspection.

The processing identity path is stable, but the implementation is not
append-only: `--force` can replace artifacts at the same canonical path.

## Installation

From the repository root, install the shared pipeline dependencies:

```bash
python -m pip install -r pipeline/requirements.txt
```

Stage 3 dependencies include:

- `azure-ai-contentunderstanding`;
- `azure-core`;
- `azure-identity`; and
- `pypdf` for local PDF page counting.

## Authentication and endpoint configuration

Preferred authentication uses `DefaultAzureCredential`:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
az login
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

An API key can instead be supplied through the environment:

```bash
export CONTENTUNDERSTANDING_ENDPOINT="https://<resource>.services.ai.azure.com"
export CONTENTUNDERSTANDING_KEY="<key>"
```

Do not put a key directly in the command line. Although `--key` is accepted,
command-line secrets can appear in shell history, process listings, terminal
captures, and automation logs.

Supported environment settings:

| Variable | Purpose |
|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Azure resource endpoint |
| `CONTENTUNDERSTANDING_KEY` | Optional API key; omit for `DefaultAzureCredential` |
| `CONTENTUNDERSTANDING_API_VERSION` | API version override |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | Analyzer override |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | LRO polling interval in seconds |
| `CONTENTUNDERSTANDING_MAX_ATTEMPTS` | Maximum pre-acceptance submissions per request/range |
| `CONTENTUNDERSTANDING_RETRY_BASE_DELAY` | Initial retry delay |
| `CONTENTUNDERSTANDING_RETRY_MAX_DELAY` | Maximum retry delay |

Treat the endpoint as trusted configuration. The script does not currently
enforce an HTTPS or Azure-host allowlist before attaching an API-key credential.

## Safe pilot workflow

Preview up to ten candidates without calling Azure:

```bash
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

Analyze one known document:

```bash
python pipeline/regdocs_3_analyze.py --document-id 4647200
```

For a PDF over 300 pages, no different command is required. Stage 3 prints the
local page count and each range before submission.

Analyze a small batch:

```bash
python pipeline/regdocs_3_analyze.py --limit 10
```

Preview all remaining selection without calling Azure:

```bash
python pipeline/regdocs_3_analyze.py --all --dry-run
```

After reviewing selection and provider cost exposure:

```bash
python pipeline/regdocs_3_analyze.py --all
```

Do not add `--force` to a normal full run. Successful matching analyses are
already skipped and completed large-PDF range parts can be reused after an
interrupted attempt.

## Locking and concurrent runs

Stage 3 holds an exclusive lock at:

```text
database/locks/3_analyze.lock
```

This prevents two local analyzers from selecting and submitting the same
billable work concurrently. The lock is removed on normal exit, handled
failure, or Ctrl-C. If a process is killed abruptly, first confirm its recorded
PID is no longer running before using `--force-lock`; forcing a live lock can
duplicate Azure submissions.

## `--dry-run` side effects

`--dry-run` guarantees that the candidate loop does not call Azure. It is not
currently a fully read-only command and it does not count PDF pages because
page inspection occurs inside actual analysis.

Before and during selection it can:

- create the output directory;
- create or migrate the `analyses` table;
- insert and update a Stage 3 `runs` row;
- audit successful artifact rows;
- canonicalize existing artifacts;
- mark missing or invalid successful artifacts `STALE_ARTIFACTS`.

Use it as a **no-Azure-call preview**, not as a zero-write database audit or
page-volume estimate.

## Source verification

Before submission, Stage 3 computes the source file's SHA-256 and compares it
with `files.sha256`. A mismatch is recorded as `FAILED/HASH_MISMATCH` and is not
submitted.

`--no-verify-hash` disables this protection. It should be reserved for
diagnosis because it weakens the provenance link between the Stage 2 file and
the Stage 3 output.

For PDFs, page counting happens after hash validation. The script currently
reads the entire file into memory before Azure submission.

## Artifact reconciliation and recovery

Before candidate selection, successful rows are audited unless `--force` or
`--no-reconcile-artifacts` is supplied.

For a matching processing identity, Stage 3 can:

- verify an artifact recorded in SQLite;
- discover the canonical artifact path;
- discover the legacy pre-versioned artifact layout;
- validate analyzer ID, API version, JSON structure, and `contents`;
- copy valid legacy output to the canonical path;
- regenerate a missing Markdown file from Markdown embedded in raw JSON;
- backfill the success row without another Azure call.

A successful row with no valid JSON becomes `STALE_ARTIFACTS` and is eligible
for repair.

Large-PDF range artifacts add a second recovery layer: when there is no valid
canonical success yet, matching completed range parts can be reused during a
normal rerun.

Disable reconciliation only when diagnosing it:

```bash
python pipeline/regdocs_3_analyze.py --no-reconcile-artifacts --limit 10
```

That option also disables reuse of saved range parts.

## Force semantics

```bash
python pipeline/regdocs_3_analyze.py --document-id 4647200 --force
```

`--force` bypasses successful-row reconciliation, canonical artifact recovery,
and large-PDF range-part reuse. It can incur another charge for every required
request, resets the existing unique ledger row to `RUNNING`, and uses the same
canonical artifact path.

For a large PDF, a forced run therefore resubmits every range. Do **not** use
`--force` merely to resume a failed final range; use the normal command so
completed range artifacts can be recovered locally.

Back up the database and relevant Stage 3 artifact directory before using
`--force` on important records.

## Retries and long-running operations

The script retries retryable submission failures only when Azure has not yet
returned a poller. It honors numeric `Retry-After` values when available and
otherwise uses bounded exponential backoff.

Defaults:

| Setting | Default |
|---|---:|
| polling interval | 3 seconds |
| maximum submission attempts | 4 per request/range |
| initial retry delay | 2 seconds |
| maximum retry delay | 30 seconds |

For ranged PDFs, each page range has its own retry loop. Completed ranges are
saved before the next range begins.

After Azure accepts a long-running operation, the process waits for the result.
For the normal single-request path, the operation ID is held in memory and is
written to SQLite when the attempt returns an outcome. For ranged PDFs, a
successful range operation ID is also committed to its `.meta.json` file.

A hard crash or ambiguous network failure after Azure accepts an operation but
before the result is committed can still leave a billable operation that a
restart cannot resume. Check Azure activity before resubmitting uncertain
failures.

## CLI reference

`--help` is authoritative for the installed script. The primary options are:

| Option | Effect |
|---|---|
| `--db PATH` | Override the SQLite ledger |
| `--download-dir PATH` | Override the Stage 2 file root |
| `--output-dir PATH` | Override the Stage 3 artifact root |
| `--lock-file PATH` | Override the exclusive Stage 3 lock path |
| `--force-lock` | Remove a confirmed-stale Stage 3 lock |
| `--endpoint URL` | Override the Azure endpoint environment setting |
| `--key VALUE` | API key override; accepted but discouraged |
| `--api-version VALUE` | Select the Azure API version |
| `--analyzer-id VALUE` | Select the analyzer |
| `--document-id ID` | Select one document |
| `--limit N` | Select at most N candidates |
| `--all` | Explicitly select every remaining eligible candidate |
| `--force` | Resubmit despite prior success/artifacts; also bypass ranged-part reuse |
| `--no-reconcile-artifacts` | Disable artifact discovery/repair and ranged-part reuse |
| `--no-verify-hash` | Disable Stage 2 source-hash verification |
| `--polling-interval SECONDS` | Set LRO polling interval |
| `--max-attempts N` | Bound retryable pre-acceptance submissions per request/range |
| `--retry-base-delay SECONDS` | Set initial exponential delay |
| `--retry-max-delay SECONDS` | Cap retry delay |
| `--dry-run` | Avoid Azure calls but retain the side effects listed above |

Stage 3 currently has no `--status`, `--status-json`, `--version`, or
`--self-test` command.

## Statuses and failure handling

Analysis rows can include:

| Status | Meaning |
|---|---|
| `RUNNING` | Processing started for the identity |
| `SUCCEEDED` | Valid canonical JSON and Markdown paths were recorded |
| `FAILED` | Submission, source validation, page validation, or artifact creation failed |
| `SKIPPED_UNSUPPORTED` | The file extension is unsupported |
| `STALE_ARTIFACTS` | A prior success no longer has valid matching output |

Large-PDF-specific failures include `PDF_PAGE_COUNT_FAILED`,
`RANGE_PAGE_COUNT_MISMATCH`, and `RANGE_ANALYSIS_INCOMPLETE`.

Errors are also appended to the shared `errors` table with document ID, code,
message, retryability, and JSON context.

A successful analysis or artifact recovery marks prior unresolved Stage 3
errors for that document resolved. Error rows remain in the ledger as run
history.

Completed document outcomes are committed as the run progresses. Ctrl-C marks
the run `INTERRUPTED`; a later normal run skips intact canonical successes and
can reuse valid range parts for an unfinished large PDF.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | No selected item failed, including a dry preview |
| `1` | One or more items failed, or a fatal runtime error occurred |
| `2` | Invalid arguments, required configuration missing, or database missing |
| `130` | Interrupted by the user |

## Current hardening priorities

Before treating Stage 3 as an unattended production worker, prioritize:

1. add byte-volume and cost estimates before submission and expose PDF page
   volume during dry-run planning;
2. persist accepted operation IDs immediately and resume polling after restart;
3. make analysis attempts and canonical raw results append-only instead of
   overwriting a prior success under `--force`;
4. make inspection modes read-only and lazy-load Azure dependencies;
5. remove command-line key input and validate the configured endpoint;
6. add provider byte-limit preflight, streaming or a memory limit, artifact
   hashes, unique temporary files, and a standalone audit/status command;
7. add regression tests for range boundaries, cached-part recovery, forced
   resubmission, page-count mismatch, and Stage 4 normalization of multiple
   `contents[]` entries with qualified provenance pointers.

Previous: [Stage 2 downloader](regdocs_2_download.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
