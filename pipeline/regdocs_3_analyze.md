# `regdocs_3_analyze.py`

Stage 3 of the REGDOCS processing pipeline: **submit verified current source
files to Azure AI Content Understanding and preserve the returned layout JSON
and Markdown with ledger provenance**.

```text
database/regdocs.db + workspace/2_download/files/
                         |
                         v
                 validate source hash
                         |
                         v
              Azure Content Understanding
                         |
                         +--> raw JSON
                         +--> Markdown
                         v
                  analyses + runs
```

Script version documented: **3.3.0**.

## Purpose and boundary

The analyzer consumes current files recorded by Stage 2. It uses Azure's
`prebuilt-layout` analyzer by default, records the operation result in the
shared SQLite ledger, and writes reusable Stage 3 artifacts.

This stage:

- makes billable external API calls unless every selected item is reconciled
  from an existing artifact or `--dry-run` is used;
- sends the contents of selected source files to the configured Azure
  endpoint;
- does not discover REGDOCS records or download source files;
- does not produce the final normalized search corpus.

Stage 4 performs local normalization of the Stage 3 result.

## Cost warning

Stage 3 requires exactly one selection scope: `--document-id`, `--limit`, or
`--all`. The explicit `--all` acknowledgement selects every eligible current
file that lacks a matching successful analysis. In a large ledger, that can
cause thousands of billable submissions.

Always inspect a bounded selection first:

```bash
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

Then begin with one document or a small limit. The current script has no
built-in price estimate, page budget, or byte budget. `--all` confirms the
selection scope, not its cost.

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

The selected file path is resolved from the stored ledger path, the configured
download directory, and the normal `<document-id>.<extension>` fallback.

## Supported source types

The script currently admits these filename extensions:

```text
pdf
png jpg jpeg tif tiff bmp heif
docx xlsx pptx
txt html htm md rtf xml json csv tsv
eml msg
```

Azure service limits can vary by content type, analyzer, API version, and
service release. The current implementation does not preflight page counts or
provider-specific size limits. Check the current Azure limits before a large
run and use a small pilot first.

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
| `operation_id` | Azure long-running-operation identifier when retained |
| `status` | Current state for this analysis identity |
| `raw_json_path` / `markdown_path` | Stored artifact paths |
| count fields | Pages, tables, sections, and warnings observed |
| `attempt_count` / `elapsed_seconds` | Submission attempts and elapsed time |
| `artifact_source` / `reconciled_at` | Whether output came from Azure or local recovery |
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

Example:

```text
workspace/3_analyze/content-understanding/raw/
  prebuilt-layout/2025-11-01/2969897/<sha256>.json
```

Each file is written through a `.partial` path and then renamed over the final
path. The JSON is the authoritative Azure response. Markdown is also extracted
to a separate file for convenient inspection.

The path represents the processing identity, but the current implementation is
not append-only: `--force` can replace the artifacts at that same path.

## Installation

From the repository root, install the shared pipeline dependencies:

```bash
python -m pip install -r pipeline/requirements.txt
```

The shared file includes `azure-ai-contentunderstanding`, `azure-core`, and
`azure-identity` required by this stage.

The current script imports the Azure packages before parsing arguments, so
even `--help` requires those packages to be installed.

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

Do not put a key directly in the command line. Although `--key` is currently
accepted, command-line secrets can appear in shell history, process listings,
terminal captures, and automation logs.

Supported environment settings:

| Variable | Purpose |
|---|---|
| `CONTENTUNDERSTANDING_ENDPOINT` | Azure resource endpoint |
| `CONTENTUNDERSTANDING_KEY` | Optional API key; omit for `DefaultAzureCredential` |
| `CONTENTUNDERSTANDING_API_VERSION` | API version override |
| `CONTENTUNDERSTANDING_ANALYZER_ID` | Analyzer override |
| `CONTENTUNDERSTANDING_POLLING_INTERVAL` | LRO polling interval in seconds |
| `CONTENTUNDERSTANDING_MAX_ATTEMPTS` | Maximum pre-acceptance submissions |
| `CONTENTUNDERSTANDING_RETRY_BASE_DELAY` | Initial retry delay |
| `CONTENTUNDERSTANDING_RETRY_MAX_DELAY` | Maximum retry delay |

Treat the endpoint as trusted configuration. The current script does not
enforce an HTTPS or Azure-host allowlist before attaching an API-key
credential.

## Safe pilot workflow

Preview up to ten candidates without calling Azure:

```bash
python pipeline/regdocs_3_analyze.py --dry-run --limit 10
```

Analyze one known document:

```bash
python pipeline/regdocs_3_analyze.py --document-id 2969897
```

Analyze a small batch:

```bash
python pipeline/regdocs_3_analyze.py --limit 10
```

Before increasing the limit, review the run output, `analyses` statuses,
provider usage, and failures.

Preview the complete remaining selection without calling Azure:

```bash
python pipeline/regdocs_3_analyze.py --all --dry-run
```

After reviewing that selection and provider cost exposure, run all remaining
eligible files with:

```bash
python pipeline/regdocs_3_analyze.py --all
```

Do not add `--force` to a normal full run. Successful matching analyses are
already skipped; `--force` can resubmit and rebill them.

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
currently a fully read-only command.

Before and during selection it can:

- create the output directory;
- create or migrate the `analyses` table;
- insert and update a Stage 3 `runs` row;
- audit successful artifact rows;
- canonicalize existing artifacts;
- mark missing or invalid successful artifacts `STALE_ARTIFACTS`.

Use it as a **no-Azure-call preview**, not as a zero-write database audit.

## Source verification

Before submission, Stage 3 computes the source file's SHA-256 and compares it
with `files.sha256`. A mismatch is recorded as `FAILED/HASH_MISMATCH` and is not
submitted.

`--no-verify-hash` disables this protection. It should be reserved for
diagnosis because it weakens the provenance link between the Stage 2 file and
the Stage 3 output.

The script currently reads the entire file into memory before submission.

## Artifact reconciliation and recovery

Before candidate selection, successful rows are audited unless `--force` or
`--no-reconcile-artifacts` is supplied.

For a matching processing identity, Stage 3 can:

- verify an artifact recorded in SQLite;
- discover the canonical artifact path;
- discover the legacy pre-versioned artifact layout;
- validate analyzer ID, API version, JSON structure, and `contents`;
- copy valid legacy output to the canonical path;
- regenerate a missing Markdown file from Markdown embedded in the raw JSON;
- backfill the success row without another Azure call.

A successful row with no valid JSON becomes `STALE_ARTIFACTS` and is eligible
for repair.

Disable this behavior only when diagnosing reconciliation itself:

```bash
python pipeline/regdocs_3_analyze.py --no-reconcile-artifacts --limit 10
```

## Force semantics

```bash
python pipeline/regdocs_3_analyze.py --document-id 2969897 --force
```

`--force` bypasses successful-row and artifact reconciliation checks. It can
incur another charge, resets the existing unique ledger row to `RUNNING`, and
uses the same canonical artifact path. A failed forced run can therefore
replace a previously successful ledger state with a failure.

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
| maximum submission attempts | 4 |
| initial retry delay | 2 seconds |
| maximum retry delay | 30 seconds |

After Azure accepts a long-running operation, the current process waits for the
result. The operation ID is held in memory and is written to SQLite only when
the attempt returns an outcome. A hard crash or ambiguous network failure after
acceptance can therefore leave a billable operation that a restart cannot
resume. Check Azure activity before resubmitting uncertain failures.

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
| `--force` | Resubmit despite prior success/artifacts |
| `--no-reconcile-artifacts` | Disable artifact discovery and repair |
| `--no-verify-hash` | Disable Stage 2 source-hash verification |
| `--polling-interval SECONDS` | Set LRO polling interval |
| `--max-attempts N` | Bound retryable pre-acceptance submissions |
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
| `SUCCEEDED` | Valid JSON and Markdown paths were recorded |
| `FAILED` | Submission, source validation, or artifact creation failed |
| `SKIPPED_UNSUPPORTED` | The file extension is unsupported |
| `STALE_ARTIFACTS` | A prior success no longer has valid matching output |

Errors are also appended to the shared `errors` table with the document ID,
code, message, retryability, and JSON context.

A successful analysis or artifact recovery marks prior unresolved Stage 3
errors for that document resolved. The error rows remain in the ledger as run
history.

Completed document outcomes are committed as the run progresses. Ctrl-C marks
the run `INTERRUPTED`; a later normal run skips intact successes and can
reconcile artifacts.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | No selected item failed, including a dry preview |
| `1` | One or more items failed, or a fatal runtime error occurred |
| `2` | Invalid arguments, required configuration missing, or database missing |
| `130` | Interrupted by the user |

## Current hardening priorities

Before treating Stage 3 as an unattended production worker, prioritize:

1. add page, byte, and cost estimates before submission;
2. persist accepted operation IDs immediately and resume polling after restart;
3. make analysis attempts and raw results append-only instead of overwriting a
   prior success under `--force`;
4. make inspection modes read-only and lazy-load Azure dependencies;
5. remove command-line key input and validate the configured endpoint;
6. add provider-limit preflight, streaming or a memory limit, artifact hashes,
   unique temporary files, and a standalone audit/status command.

Previous: [Stage 2 downloader](regdocs_2_download.md).

Next: [Stage 4 normalizer](regdocs_4_normalize.md).
