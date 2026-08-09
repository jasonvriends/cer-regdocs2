# `regdocs_4_normalize.py`

Stage 4 of the REGDOCS processing pipeline. The public command is now a
**provider-selecting, crash-resilient supervisor** that normalizes successful
Azure Content Understanding or Docling analyses into deterministic document,
page, chunk, table, and provenance JSONL.

Script version documented: **4.2.0**.

The detailed deterministic projection implementation from Stage 4.1.0 now lives
in `regdocs_4_normalize_worker.py`. The public supervisor owns provider
selection, the Stage 4 run, locking, per-document child isolation, progress, and
final corpus assembly.

## Public execution model

```text
regdocs_4_normalize.py
        |
        +--> choose Azure or Docling
        |
        v
one SQLite runs row
        |
        +--> worker child: document A --> shard + normalization row
        |        wait for child exit
        |
        +--> worker child: document B --> shard + normalization row
        |        wait for child exit
        |
        +--> worker child: document C --> FAILED, record error, continue
        |        wait for child exit
        |
        `--> ...
                 |
                 v
          merge successful shards
                 |
                 v
      atomic per-file canonical replace
```

There is exactly one active normalization child at a time. Each child receives
one document ID and writes its output into a run-scoped shard directory. The
supervisor owns the single parent `runs` row and prevents child workers from
finishing or rewriting that run independently.

This deliberately mirrors the Stage 3 Azure/Docling supervisor pattern: **one
public invocation = one pipeline run**, with isolated document workers attached
to it.

## Provider choice

Run Azure explicitly:

```bash
python pipeline/regdocs_4_normalize.py --analysis-provider azure
```

Run Docling explicitly:

```bash
python pipeline/regdocs_4_normalize.py --analysis-provider docling
```

When run from an interactive terminal without `--analysis-provider`, the script
asks:

```text
Analysis provider [azure/docling]:
```

For automation/non-interactive execution, provider choice is mandatory.

Provider resolution currently maps to:

```text
azure   -> prebuilt-layout / 2025-11-01
docling -> newest successful current docling-standard version in SQLite
```

`regdocs_4_normalize_provider.py` remains only as a compatibility launcher. It
executes the primary `regdocs_4_normalize.py` command with the original
arguments.

## Stage 4 lock

Normal processing uses:

```text
database/locks/4_normalize.lock
```

The lock records the supervisor PID. A later invocation removes it
automatically when that PID is no longer alive. Do not use `--force-lock` unless
you have independently confirmed that another Stage 4 supervisor is not active.

`--status` does not acquire the processing lock.

## Inputs and candidate selection

Defaults:

```text
ledger:         database/regdocs.db
analysis root:  workspace/3_analyze/
output:         workspace/4_normalize/
```

A candidate must have:

- a successful Stage 3 `analyses` row for the selected provider/version;
- a current Stage 2 file;
- a Stage 3 file SHA matching the current Stage 2 file SHA.

Candidates are sorted naturally by REGDOCS document ID. `--document-id` may be
repeated and `--limit` truncates the selected set.

## Document-level failure isolation

A malformed or inconsistent Stage 3 artifact is a **document failure**, not a
pipeline-fatal condition by default.

For example, if an Azure artifact has no usable `contents[]`:

```text
[5061/7958] 4665098 ...
          FAILED ValueError: Content Understanding result has no contents[]
[5062/7958] ...
          OK ...
```

The worker records the failed `normalizations` row and shared `errors` row. The
supervisor prints diagnostics and launches the next document child.

Use `--stop-on-error` only when diagnostic work should intentionally halt the
pass at the first failed document.

At the end:

- no failed documents -> parent run is `SUCCEEDED` and command exits `0`;
- one or more failed documents -> parent run is `COMPLETED_WITH_ERRORS`, the
  successful corpus is published, and the command exits `1` so automation still
  sees that the corpus has gaps.

A worker crash or abnormal process exit is handled in the same parent loop. If
no matching successful normalization row exists, that document is counted as a
failure and processing continues unless `--stop-on-error` was requested.

## Run ownership and ledger state

The supervisor creates the Stage 4 `runs` row and records:

```text
provider
analyzer_id
api_version
normalizer configuration hash
selection scope
concurrency = 1
worker_isolation = one_document_per_child
continue_on_document_error
```

Each child attaches its `normalizations` and `errors` rows to that supervisor
run. Child workers do not allocate or finish their own top-level pipeline run.

`python pipeline/regdocs_4_normalize.py --status` reports the latest Stage 4
parent run.

## Per-document shards and final publication

Workers write temporary run-scoped shards below:

```text
workspace/4_normalize/.workers/run-<run-id>/<document-id>/
    documents.jsonl
    pages.jsonl
    chunks.jsonl
    tables.jsonl
    provenance.jsonl
```

Only shards from documents whose normalization row is `SUCCEEDED` are included
in final assembly.

After all selected documents have been attempted, the supervisor concatenates
successful shards in deterministic candidate order into `.partial` files,
flushes and `fsync`s each file, then replaces the canonical file with
`os.replace`.

The canonical output remains:

```text
workspace/4_normalize/
├── documents.jsonl
├── pages.jsonl
├── chunks.jsonl
├── tables.jsonl
└── provenance.jsonl
```

Final per-file SHA-256 values are stored in the parent run summary.

If the supervisor is interrupted during document processing, the run becomes
`INTERRUPTED`, completed worker shards are retained for diagnostics, and the
canonical JSONL set is not replaced by that interrupted pass.

The five canonical files are still replaced one file at a time rather than by
an atomic generation-directory pointer. Versioned manifested generations remain
a roadmap hardening item.

## Deterministic worker contract

The internal `regdocs_4_normalize_worker.py` preserves the Stage 4.1.0
normalization contract. It performs no network calls.

For each candidate it validates:

- raw Stage 3 JSON exists and is an object;
- analyzer ID and provider/API/package version match the ledger;
- `contents` is a non-empty list;
- each content object contains Markdown;
- a separate Markdown artifact, when present, matches embedded Markdown.

It projects:

| File | One record per | Purpose |
|---|---|---|
| `documents.jsonl` | document | catalogue/file/analysis identity and counts |
| `pages.jsonl` | source page | page text, dimensions, labels and provenance |
| `chunks.jsonl` | search unit | text/table/figure chunks with metadata |
| `tables.jsonl` | detected table | detailed cell/table structure |
| `provenance.jsonl` | emitted chunk | exact source elements, pages and polygons |

Default chunk targets remain 800 words preferred / 1,200 words maximum.

## Ranged Azure PDF provenance

Azure PDFs analyzed in multiple `Content-Range` requests remain one logical
REGDOCS document. Stage 4 processes every canonical `contents[]` entry and keeps
original source page numbers.

Provenance pointers remain globally qualified by `content_index`. For example:

```json
{
  "content_index": 2,
  "element": "/contents/2/paragraphs/12",
  "local_element": "/paragraphs/12"
}
```

This prevents two ranged contributions that both contain `/paragraphs/12` from
being confused while preserving the provider-local pointer.

## Docling compatibility input

Docling Stage 3 preserves its native document under `regdocsDocling.native` and
adds a conservative REGDOCS compatibility projection under `contents[]`.

Stage 4 consumes that compatibility projection through the same deterministic
worker contract. Provider identity remains on normalized records so later
comparison work can distinguish Azure- and Docling-derived corpora.

This compatibility layer is experimental and does not imply Azure and Docling
native schemas are equivalent.

## Safe pilot workflow

Preview ten Azure analyses without writing a run or JSONL:

```bash
python pipeline/regdocs_4_normalize.py \
  --analysis-provider azure \
  --limit 10 \
  --dry-run
```

Write a bounded Azure pilot away from the canonical corpus:

```bash
python pipeline/regdocs_4_normalize.py \
  --analysis-provider azure \
  --limit 10 \
  --output-dir workspace/4_normalize/pilot-azure
```

Do the same for Docling:

```bash
python pipeline/regdocs_4_normalize.py \
  --analysis-provider docling \
  --limit 10 \
  --output-dir workspace/4_normalize/pilot-docling
```

Then rebuild the selected provider's full canonical corpus:

```bash
python pipeline/regdocs_4_normalize.py --analysis-provider azure
```

No Stage 3 Azure rerun is required merely because Stage 4 failed or changed;
normalization reads the already preserved Stage 3 artifacts.

## Corpus replacement rule

A successful completed Stage 4 invocation builds a complete output set for the
**documents selected by that invocation**. A filtered `--document-id` or
`--limit` run against the default output directory will therefore publish only
that subset.

Use a separate `--output-dir` for pilots. Reserve the default canonical output
for an unfiltered provider pass.

## CLI reference

`--help` is authoritative for the installed script.

| Option | Effect |
|---|---|
| `--analysis-provider azure|docling` | Select Stage 3 input; interactive prompt when omitted on a TTY |
| `--db PATH` | Override SQLite ledger |
| `--analysis-dir PATH` | Override Stage 3 root |
| `--output-dir PATH` | Select final five-file output directory |
| `--document-id ID` | Filter to an ID; may repeat |
| `--limit N` | Limit the naturally sorted candidate selection |
| `--target-words N` | Preferred chunk size |
| `--max-words N` | Maximum chunk size |
| `--stop-on-error` | Stop after first document failure instead of continuing |
| `--dry-run` | Resolve selected inputs without normalizing |
| `--status` | Show latest Stage 4 parent run |
| `--lock-file PATH` | Override Stage 4 lock path |
| `--force-lock` | Remove existing lock before starting; use only after external verification |
| `--version` | Print public supervisor version |

Legacy `--analyzer-id`, `--api-version`, and `--skip-errors` are retained only
for compatibility/internal delegation and are intentionally hidden from normal
help.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Run completed with no document failures, or status/version/no-candidate operation succeeded |
| `1` | One or more documents failed, required input is missing, or a fatal supervisor error occurred |
| `130` | Interrupted by the user |

## Current hardening priorities

The next Stage 4 durability work remains:

1. versioned generation directories plus a manifest and atomic `CURRENT`
   pointer rather than five independent final-file replacements;
2. a generation identity linking `normalizations` rows to the published corpus;
3. explicit protection against publishing a filtered run into the canonical
   output directory by accident;
4. broader source-element coverage auditing, including hyperlink targets,
   captions and footnotes;
5. raw artifact SHA/path fingerprints in normalized provenance/manifests; and
6. regression/fault-injection tests proving malformed documents, child crashes,
   interrupts, and provider-specific artifacts cannot corrupt or abort the
   remaining corpus pass.

Previous: [Stage 3 Azure](regdocs_3_azure.md) or [Stage 3 Docling](regdocs_3_docling.md).

Next: [Stage 5 index publisher](regdocs_5_index.md).
