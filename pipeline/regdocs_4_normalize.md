# `regdocs_4_normalize.py`

Stage 4 of the REGDOCS processing pipeline: **deterministically project
successful Azure layout analyses into document, page, chunk, table, and
provenance JSONL records**.

```text
analyses + raw Content Understanding JSON
                     |
                     v
          validate artifact contract
                     |
                     v
       local deterministic normalization
                     |
        +------------+-------------+-------------+
        |            |             |             |
   documents      pages         chunks        tables
                                      \
                                       +--> provenance
```

Script version documented: **4.1.0**.

## Purpose and boundary

Stage 4 is a local transformation. It makes no network or Azure calls. It joins
catalogue metadata, the current downloaded-file identity, and a successful
Stage 3 layout result into search-oriented JSONL while retaining links back to
source elements and page geometry.

Stage 4 is the deterministic corpus boundary between external document analysis
and downstream retrieval. Stage 5 consumes `chunks.jsonl` and
`provenance.jsonl` to publish a rebuildable Azure AI Search index; Stage 4
remains authoritative for normalized search content and detailed provenance.

## Stage 3 ranged-PDF compatibility

Stage 3 automatically analyzes PDFs over 300 pages with multiple Azure
`Content-Range` requests and combines the results into one canonical JSON
artifact. Each ranged Azure result is preserved as a separate entry in the
canonical `contents[]` array rather than rewriting its internal spans and
offsets.

Stage 4 iterates those `contents[]` entries independently. A 986-page Stage 3
result containing four ranged contributions is still normalized as one REGDOCS
document. Page, table, section, figure, chunk, and provenance records are built
across the complete combined result.

Azure source page identity is preserved, so a record from source page 742 still
points to page 742 of the original PDF. `content_index` identifies which
canonical `contents[]` contribution produced the record.

Beginning with Stage 4.1.0, provenance element identities are also globally
qualified across the combined result. Azure-local pointers are retained for
inspection, but the primary pointer includes the `contents[]` index.

For example, an Azure-local pointer:

```text
/paragraphs/12
```

from the third `contents[]` entry is normalized as:

```json
{
  "content_index": 2,
  "element": "/contents/2/paragraphs/12",
  "local_element": "/paragraphs/12"
}
```

The same rule is used for paragraph, table, and figure evidence, including
paragraph evidence nested under a figure. This removes the ambiguity that
previously existed when two ranged content entries both contained something
like `/paragraphs/12`.

The Stage 3 canonical artifact also contains `regdocsChunking` metadata for
audit purposes. Stage 4 leaves that metadata in the raw Stage 3 artifact rather
than copying it into every normalized record.

## Installation

From the repository root, install the shared pipeline dependencies:

```bash
python -m pip install -r pipeline/requirements.txt
```

Stage 4 itself uses only the Python standard library, but the shared environment
supports running the complete pipeline.

## Important corpus-replacement rule

Every non-dry run writes a complete new set of five files to its output
directory. It does not merge records into existing JSONL.

Consequently, a filtered run using `--document-id` or `--limit` with the
default output directory replaces the main corpus with only that subset.

Use a separate output directory for every pilot:

```bash
python pipeline/regdocs_4_normalize.py \
  --document-id 4647200 \
  --output-dir workspace/4_normalize/pilot
```

Reserve an unfiltered command for rebuilding the canonical corpus:

```bash
python pipeline/regdocs_4_normalize.py
```

## Inputs and candidate selection

Defaults:

```text
ledger:         database/regdocs.db
analysis root:  workspace/3_analyze/
output:         workspace/4_normalize/
analyzer:       prebuilt-layout
API version:    2025-11-01
```

A candidate must satisfy all of these conditions:

- its `analyses.status` is `SUCCEEDED`;
- its analyzer ID and API version match the requested values;
- the joined Stage 2 file is current;
- the analysis source hash matches the current file SHA-256.

Candidates are sorted naturally by REGDOCS document ID. `--document-id` may be
repeated, and `--limit` truncates the sorted selection.

The raw Stage 3 JSON is required. A separate Markdown artifact is optional
because Markdown embedded in the JSON can be used as a fallback.

## Input validation

For each candidate, the normalizer verifies that:

- the raw JSON exists and contains an object;
- the JSON analyzer ID matches the ledger;
- the JSON API version matches the ledger;
- `contents` is a non-empty list;
- every content object contains Markdown;
- a separate Markdown artifact, when present, exactly matches the Markdown
  embedded in the JSON.

Differences between Stage 3's recorded page/table/section counts and the JSON
are preserved as normalization warnings rather than silently ignored.

For a ranged PDF, Stage 3 itself validates each requested range page count and
the combined page count before publishing the canonical artifact. Stage 4 then
processes the published `contents[]` entries independently.

## Outputs

The default output set is:

```text
workspace/4_normalize/
├── documents.jsonl
├── pages.jsonl
├── chunks.jsonl
├── tables.jsonl
└── provenance.jsonl
```

Each line is one compact JSON object with keys sorted deterministically.

| File | One record per | Primary purpose |
|---|---|---|
| `documents.jsonl` | selected document | Catalogue, file, analysis, and normalization metadata |
| `pages.jsonl` | analyzed page | Page text, Markdown slice, dimensions, labels, headers, and footers |
| `chunks.jsonl` | searchable text/table/figure unit | Search-ready content with inherited filters and section/page context |
| `tables.jsonl` | detected table | Detailed table cells, dimensions, caption, text, geometry, and source links |
| `provenance.jsonl` | emitted chunk | Source elements, content index, spans, page range, and regions supporting the chunk |

Stage 5 uses `chunks.jsonl` as its primary search-document input and joins each
chunk to its matching `provenance.jsonl` record before Azure AI Search
publication. The other Stage 4 projections remain available for document/page
inspection and future application features.

## Stable record identities

Generated IDs follow these forms:

```text
page:       <document-id>:page:<four-digit-page-number>
chunk:      <document-id>:chunk:<four-digit-sequence>
table:      <document-id>:table:<four-digit-sequence>
figure ref: <document-id>:figure:<four-digit-sequence>
```

Chunk and table numbering follows deterministic source traversal order for the
same validated input and configuration. Table and figure numbering use global
offsets across all `contents[]` entries so ranged Stage 3 results remain one
logical sequence.

Every projection retains the document ID and source-file SHA-256. Relevant
records also retain source URL, resolved URL, analyzer ID, API version, and
normalizer version.

## Document projection

Each document record combines:

- REGDOCS title, dates, submitter, company, project, and filing metadata;
- facets, identifiers, roles, language, and container memberships;
- current Stage 2 file identity and metadata;
- Stage 3 analysis identity and observed counts;
- logical paths to raw JSON and Markdown;
- analysis and normalization warnings;
- Markdown SHA-256;
- normalizer version, parser version, and configuration hash.

For ranged Stage 3 input, `page_count`, `table_count`, `section_count`, and
`figure_count` are sums across all canonical `contents[]` entries.

## Page projection

Page records contain:

- physical source page number;
- `content_index` for the contributing Stage 3 `contents[]` entry;
- printed page labels;
- width, height, unit, and angle;
- body content;
- page-scoped Markdown derived from Azure spans;
- headers and footers as separate arrays;
- source-file and processing provenance.

Headers, footers, and page-number paragraphs are not mixed into the page body.

Azure `Content-Range` keeps the source page identity, so ranged large PDFs
continue to use original document page numbers.

## Chunking behavior

Default chunk targets:

| Setting | Default |
|---|---:|
| preferred size | 800 words |
| maximum size | 1,200 words |

Change them with:

```bash
python pipeline/regdocs_4_normalize.py \
  --target-words 600 \
  --max-words 900
```

The normalizer processes each Stage 3 content entry in order, walks its Azure
sections in source-span order, and:

- preserves the section-heading path on each chunk;
- excludes page headers, footers, page numbers, and section headings from body
  chunks;
- groups paragraph units toward the target size without exceeding the maximum
  when structural splitting is possible;
- splits an oversized paragraph by sentence, then by words when necessary;
- emits tables as their own search chunks, repeating header rows on later table
  parts;
- emits figure text when it contains at least three words;
- retains orphan sections, tables, and figures that were not reachable from a
  normal section root.

Chunk types currently include `text`, `table`, and `figure`.

Every emitted chunk is required to carry `content_index`. Stage 4.1.0 treats a
missing `content_index` as an internal provenance error rather than silently
publishing an ambiguous chunk.

## Table and provenance behavior

Detailed table records preserve row/column indices, spans, cell kinds, cell
content, source strings, parsed page polygons, the global table identity, and
the local `content_table_index` within its Azure content object.

Each chunk receives one provenance record with:

- chunk and document identity;
- source-file SHA-256;
- `content_index`;
- first and last source page;
- merged source regions and page polygons;
- qualified Azure element pointers;
- original Azure-local element pointers;
- spans, source strings, and fragment metadata.

A typical evidence item now looks like:

```json
{
  "content_index": 2,
  "element": "/contents/2/paragraphs/12",
  "local_element": "/paragraphs/12",
  "element_type": "paragraph",
  "element_index": 12,
  "source": "D(742,...)"
}
```

This gives two complementary routes back to evidence:

```text
REGDOCS document + source SHA-256
             |
             +--> original PDF page + polygon
             |
             +--> contents[index] + exact Azure element
```

The first route supports source-document inspection. The second supports exact
dereferencing inside the combined Stage 3 JSON.

## Known content-coverage limitations

The current traversal still does not emit every Azure Content Understanding
element:

- hyperlink elements and their targets are not projected;
- figure captions and footnotes are not included unless their text is also
  reachable through handled figure elements;
- table footnotes are not projected;
- non-header/footer paragraphs outside the walked section structure have no
  general fallback pass;
- a split oversized paragraph retains the original paragraph-wide span and
  region for every fragment.

The former multi-`contents[]` pointer ambiguity is **not** a current limitation
as of Stage 4.1.0; provenance element identities are qualified by
`content_index`.

Until broader coverage auditing is implemented, validate representative
documents against their raw Stage 3 JSON before using the normalized corpus for
high-recall retrieval or evidentiary citation.

## Determinism and configuration identity

For fixed Stage 1 metadata, Stage 2 file identity, Stage 3 artifacts, script
version, and arguments, Stage 4 writes records in deterministic order with
canonical JSON serialization.

The configuration hash includes:

```text
normalizer version
parser version
analyzer ID
API version
target words
maximum words
```

Stage 4.1.0 changes the normalized provenance contract, so its version and
parser identity were bumped. Re-running normalization therefore produces a new
configuration identity rather than presenting the 4.0.1 output as equivalent.

Each document also receives a deterministic SHA-256 over its document, page,
chunk, table, and provenance records. Each final JSONL file receives a SHA-256
stored in the completed run summary.

The configuration hash does not currently include a hash of the raw analysis
artifact or a revision/hash of all Stage 1 metadata, so it is not by itself a
complete input fingerprint.

When `--analysis-dir` points somewhere other than the normal workspace, the
current document projection still constructs its logical raw/Markdown paths
from the default analysis root. The input can be read successfully while the
published provenance path is inaccurate.

## Safe pilot and verification workflow

Preview one document without writing JSONL:

```bash
python pipeline/regdocs_4_normalize.py \
  --document-id 4647200 \
  --dry-run
```

Write a one-document pilot away from the canonical corpus:

```bash
python pipeline/regdocs_4_normalize.py \
  --document-id 4647200 \
  --output-dir workspace/4_normalize/pilot
```

For a large ranged PDF, verify that the Stage 4 `OK` page count matches the
Stage 3 `COMBINED` page count and the source PDF page count.

Then inspect a provenance record from a later content range:

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("workspace/4_normalize/pilot/provenance.jsonl")
for line in path.open(encoding="utf-8"):
    record = json.loads(line)
    if record.get("content_index", 0) > 0 and record.get("elements"):
        print(json.dumps(record, indent=2, ensure_ascii=False))
        break
PY
```

For Stage 4.1.0, evidence in that record should include both a qualified
`element` beginning with `/contents/<content_index>/...` and a `local_element`
containing the original Azure-local pointer.

Inspect line counts and parse every emitted line:

```bash
wc -l workspace/4_normalize/pilot/*.jsonl

python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("workspace/4_normalize/pilot").glob("*.jsonl")):
    with path.open(encoding="utf-8") as handle:
        count = sum(1 for line in handle if json.loads(line))
    print(path, count)
PY
```

Show the most recent normalization run:

```bash
python pipeline/regdocs_4_normalize.py --status
```

Rebuild the full canonical corpus only after the pilot is satisfactory:

```bash
python pipeline/regdocs_4_normalize.py
```

No Azure rerun is required for this provenance change. Stage 4.1.0 can rebuild
its outputs from existing successful Stage 3 JSON.

After the final canonical normalize, validate the Stage 5 handoff locally:

```bash
python pipeline/regdocs_5_index.py --dry-run
```

## Dry-run and status side effects

`--dry-run` resolves candidates and artifacts without creating JSONL or a
normalization run row. `--status` prints the latest Stage 4 run.

Both commands currently open the SQLite database in normal read/write mode and
run `ensure_schema` first. If the `normalizations` table or indexes do not yet
exist, this can create them. They are therefore operationally read-mostly, not
guaranteed byte-for-byte read-only database operations.

## Publishing and crash behavior

Each final JSONL file is written to a fixed `.partial` file, flushed and
`fsync`ed, then renamed over its final path. On a caught interruption or error,
the script removes partial files and keeps the prior final files.

The five-file set is not currently published as one atomic generation. A
process crash during the sequence of five renames can leave files from mixed
runs. In addition, per-document `normalizations` rows are committed as
`SUCCEEDED` before the output set is promoted. The database can therefore get
ahead of the published files after a hard crash or final-write failure.

Do not run two normalizers against the same output directory concurrently. The
current script has no Stage 4 lock and uses shared `.partial` names.

Back up or snapshot the canonical output before a high-value rebuild.

## Failure modes

By default, one document failure aborts the run. Partial files are removed and
the old final files normally remain.

With `--skip-errors`, processing continues and the successfully normalized
subset is published. The command exits nonzero and the run becomes
`COMPLETED_WITH_ERRORS`, but the new output is still incomplete. Use a separate
output directory when diagnosing with this option.

Errors are appended to the shared `errors` table, and per-document state is
recorded in `normalizations`.

## `normalizations` ledger table

The processing identity is:

```text
analysis_id + normalizer_version + config_hash
```

The table records source identities, run ID, status, per-document output hash,
projection counts, timestamps, and error details.

Current statuses are `RUNNING`, `SUCCEEDED`, and `FAILED`. Run statuses include
`RUNNING`, `SUCCEEDED`, `COMPLETED_WITH_ERRORS`, `INTERRUPTED`, and `FAILED`.

## CLI reference

`--help` is authoritative for the installed script.

| Option | Effect |
|---|---|
| `--db PATH` | Override the SQLite ledger |
| `--analysis-dir PATH` | Override the Stage 3 root |
| `--output-dir PATH` | Choose the five-file output directory |
| `--analyzer-id VALUE` | Select successful analyses from this analyzer |
| `--api-version VALUE` | Select successful analyses from this API version |
| `--document-id ID` | Filter to an ID; may be repeated |
| `--limit N` | Limit the naturally sorted selection |
| `--target-words N` | Preferred chunk size; minimum 50 |
| `--max-words N` | Maximum chunk size; must be at least the target |
| `--skip-errors` | Publish successful records despite failures |
| `--dry-run` | Resolve selected inputs without writing JSONL or a run row |
| `--status` | Print the latest normalize run |
| `--version` | Print the script version |

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Run/status/version succeeded, or no candidates were selected |
| `1` | A candidate is missing, one or more documents failed, or a fatal error occurred |
| `130` | Interrupted by the user |

Argument validation errors currently pass through the general fatal-error path
and return `1`.

## Current hardening priorities

Before treating Stage 4 output as an independently publishable production
snapshot, prioritize:

1. add a source-element coverage invariant and preserve hyperlink targets,
   table/figure captions, and footnotes more completely;
2. refuse filtered runs against the canonical output unless explicitly
   acknowledged;
3. write versioned generation directories plus a manifest and atomically switch
   a `CURRENT` pointer only after every file is durable;
4. link `normalizations` rows to a generation and commit their success only
   after publication;
5. add a single-writer lock and unique, securely created temporary files;
6. make `--skip-errors` diagnostic by default and require a separate explicit
   option to publish an incomplete corpus;
7. store the actual resolved artifact path and raw JSON SHA-256;
8. add JSON Schema and output schema-version fields, a standalone `--audit`,
   and a manifest containing selection, counts, hashes, failures, and complete
   input fingerprints;
9. add regression fixtures that include a Stage 3 multi-`contents[]` ranged PDF
   and assert qualified provenance pointers at range boundaries;
10. move toward per-document shards or partitioned snapshots for incremental
    rebuilds and large corpora.

Previous: [Stage 3 analyzer](regdocs_3_analyze.md).

Next: [Stage 5 index publisher](regdocs_5_index.md).
