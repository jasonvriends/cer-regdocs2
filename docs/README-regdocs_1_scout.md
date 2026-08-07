# `regdocs_1_scout.py`

Stage 1 of the REGDOCS acquisition pipeline: **discover, catalogue, enrich, and preserve provenance for public Canada Energy Regulator REGDOCS records**.

```text
REGDOCS
   |
   v
date search
   |
   +--> explicit Folder / Compound Document traversal
   |
   +--> live facet enrichment
   |
   +--> item detail pages
   |
   v
five-table SQLite ledger + gzip source archive
```

## Purpose

The scout answers two questions for each REGDOCS item it observes:

1. **What metadata can be collected without downloading the source file?**
2. **What is the current pipeline state for that item?**

It is the catalogue/provenance stage. It does not perform document OCR, text extraction, embeddings, or source-file download.

Script version documented: **1.1.1**.

## What it collects

The scout can collect:

- date-range search results;
- stable REGDOCS item IDs;
- result-row metadata;
- filing/company/project references exposed by the result row;
- explicit `Folder` and `Compound Document` membership;
- nested explicit container membership;
- the live Advanced Search facet catalogue;
- facet values associated with scouted documents;
- each selected item's own REGDOCS detail page;
- title-derived high-confidence identifiers;
- raw successful HTML responses used by the parser.

## What it deliberately does not crawl

Container traversal is intentionally narrow.

The scout does **not** follow:

- arbitrary outbound links;
- project links as crawl expansion;
- company links as crawl expansion;
- taxonomy links;
- breadcrumbs;
- site navigation;
- unrelated REGDOCS item links.

A `Folder` or `Compound Document` is expanded only when its own item page explicitly declares the member-result endpoint used by REGDOCS.

## Stable identity

For normal items, the numeric REGDOCS item ID is the primary document identity.

REGDOCS can reuse a placeholder numeric ID for multiple paper-only rows inside a container. When that occurs, the scout creates a stable synthetic identity based on the parent container plus exhibit number or a deterministic content fingerprint.

Synthetic container rows preserve the original REGDOCS numeric value in metadata, but are marked non-downloadable because one synthetic identity cannot safely be mapped to a unique source-file URL.

## Database

The scout creates exactly five user tables:

| Table | Role |
|---|---|
| `documents` | Document metadata and all pipeline stage statuses |
| `runs` | Run history, parameters, heartbeat, counters, progress, summary |
| `errors` | Structured warnings/errors and resolution state |
| `raw_snapshots` | Pointers and hashes for preserved REGDOCS HTML responses |
| `files` | Reserved/shared file-version table used by Stage 2 |

The database is a **pipeline ledger**, not the final search/index database.

By design, detail fields and facet data are stored inside `documents.metadata` as JSON rather than normalized into additional tables.

## Raw source archive

Successful HTML responses are gzip-compressed and stored by SHA-256.

Default layout:

```text
raw/regdocs/
├── advanced/<prefix>/<sha256>.html.gz
├── search/<prefix>/<sha256>.html.gz
├── facet/<prefix>/<sha256>.html.gz
├── detail/<prefix>/<sha256>.html.gz
└── container/<prefix>/<sha256>.html.gz
```

Keep this archive with `regdocs.db`.

It provides the source evidence behind normalized metadata and allows parser logic to be improved later without losing the original response that produced the metadata.

## Default behavior

Running:

```bash
python regdocs_1_scout.py
```

uses the production/default profile.

### Automatic date policy

When no explicit dates are supplied:

- **new database:** January 1 of the current year through today;
- **normal repeat run:** newest stored filing date minus seven days through today;
- **full refresh:** the full current year at least every 30 days.

### Other defaults

- page size: `200`;
- all live facet categories;
- container expansion enabled;
- nested container maximum depth: `20`;
- maximum unique containers per run: `10,000`;
- item detail pages enabled;
- successful detail metadata reused for `30` days;
- concurrency: `1`;
- random delay between request starts: `2–4` seconds;
- maximum retries: `4`;
- retry backoff base: `2.0`;
- raw snapshots enabled.

## Installation

Required:

```bash
pip install httpx beautifulsoup4
```

Recommended for progress bars:

```bash
pip install tqdm
```

Optional faster HTML parser:

```bash
pip install lxml
```

If `tqdm` is unavailable, the script still runs with a minimal fallback.

## Common commands

### Normal incremental/default run

```bash
python regdocs_1_scout.py
```

### Explicit date range

```bash
python regdocs_1_scout.py \
  --start-date 2025-01-01 \
  --end-date 2025-12-31
```

If only `--end-date` is supplied, the start defaults to January 1 of that year.

### Preview/fetch without updating documents

```bash
python regdocs_1_scout.py --dry-run
```

`--dry-run` does not update document rows, but run/error/raw-snapshot evidence remains durable.

### Limit records while testing

```bash
python regdocs_1_scout.py --limit 100
```

### Disable facets

```bash
python regdocs_1_scout.py --facets none
```

### Select facet categories

```bash
python regdocs_1_scout.py --facets "Document Type,File Type,Role"
```

### Disable container expansion

```bash
python regdocs_1_scout.py --no-expand-containers
```

The legacy alias `--no-expand-compounds` maps to the same setting.

### Repair known containers only

```bash
python regdocs_1_scout.py --repair-containers
```

Repair mode loads known `Folder` and `Compound Document` records from SQLite and reprocesses their explicit membership without rerunning:

- the date search;
- facet searches; or
- every non-container detail page.

### Force detail refresh

```bash
python regdocs_1_scout.py --refresh-details
```

### Change detail freshness window

```bash
python regdocs_1_scout.py --detail-refresh-days 7
```

### Disable detail pages

```bash
python regdocs_1_scout.py --no-details
```

### Show status

```bash
python regdocs_1_scout.py --status
```

Machine-readable:

```bash
python regdocs_1_scout.py --status-json
```

### Show effective default profile

```bash
python regdocs_1_scout.py --show-defaults
```

### Check the database schema

```bash
python regdocs_1_scout.py --check-schema
```

### Audit the ledger and raw archive

```bash
python regdocs_1_scout.py --audit
```

The audit is read-only and checks areas including:

- SQLite integrity;
- expected five-table schema;
- container counts and backlinks;
- snapshot references;
- raw gzip sizes;
- SHA-256 hashes;
- unresolved errors.

### Offline self-test

```bash
python regdocs_1_scout.py --self-test
```

### Script identity/version

```bash
python regdocs_1_scout.py --version
```

The version output includes script version, parser version, schema version, expected tables, and the script SHA-256.

## CLI reference

| Option | Default | Description |
|---|---:|---|
| `--db` | `regdocs.db` | SQLite pipeline ledger |
| `--raw-dir` | `raw/regdocs` | Raw content-addressed HTML archive |
| `--progress-file` | `_audit/scout-progress.json` | Atomic progress projection |
| `--log-file` | `_audit/scout.log` | Durable log |
| `--start-date` | automatic | Start date in `YYYY-MM-DD` |
| `--end-date` | today/automatic | End date in `YYYY-MM-DD` |
| `--page-size` | `200` | REGDOCS page size: 20, 50, 100, or 200 |
| `--limit` | none | Limit selected base records |
| `--facets` | `all` | `all`, `none`, or comma-separated category names |
| `--[no-]expand-containers` | enabled | Traverse explicit Folder/Compound membership |
| `--repair-containers` | disabled | Reprocess known containers only |
| `--container-max-depth` | `20` | Maximum nested explicit container depth |
| `--container-max-items` | `10000` | Maximum unique containers expanded per run |
| `--[no-]details` | enabled | Fetch each item's own detail page |
| `--detail-refresh-days` | `30` | Detail-page cache/freshness window |
| `--refresh-details` | disabled | Ignore detail freshness and refetch |
| `--concurrency` | `1` | Maximum concurrent request workers |
| `--min-delay` | `2.0` | Minimum global request-start delay |
| `--max-delay` | `4.0` | Maximum global request-start delay |
| `--max-retries` | `4` | Maximum retry count for temporary failures |
| `--retry-backoff` | `2.0` | Retry backoff base |
| `--dry-run` | disabled | Parse/fetch without document-row updates |
| `--verbose` | disabled | Debug logging |
| `--force-lock` | disabled | Replace a stale scout lock |
| `--status` | — | Human-readable status |
| `--status-json` | — | JSON status |
| `--show-defaults` | — | Print default profile |
| `--self-test` | — | Offline self-test |
| `--version` | — | Script identity/version JSON |
| `--check-schema` | — | Verify exact five-table schema |
| `--audit` | — | Read-only ledger/raw archive audit |

## Container traversal

Container expansion is queue-based and loop-safe.

Safeguards include:

- explicit container-kind check;
- explicit `Item/LoadResult/<id>` endpoint discovery;
- no guessed pagination parameters;
- seen-set loop prevention;
- maximum nesting depth;
- maximum expanded-container count;
- completeness checks against the result total when available.

When a container result is incomplete, newly observed membership is merged with the previous known manifest rather than silently deleting previously known children.

When a complete refresh is available, stale child membership can be removed.

## Facet enrichment

The scout first fetches the live Advanced Search page and discovers facet categories and filter IDs from the current site.

It can then execute filtered searches and project matching values into each document's JSON metadata.

Known categories receive stable convenience keys, including:

- `Document Type` -> `document_types`
- `Application Type` -> `application_types`
- `File Type` -> `file_types`
- `Role` -> `roles`
- `Commodity` -> `commodities`

The raw facet structure is also preserved.

## Detail-page enrichment

Each eligible item can have its own REGDOCS detail page fetched.

The parser uses restrained generic extraction from structures such as:

- page headings;
- metadata tags;
- definition lists;
- tables;
- common label/value layouts;
- embedded JSON/JSON-LD scalar fields.

Known labels can be projected into canonical fields such as:

- title;
- date / filing date;
- submitter;
- company / applicant;
- project;
- filing number;
- document type;
- application type;
- file type;
- role;
- commodity;
- language;
- page count;
- description;
- hearing order;
- docket;
- regulatory status.

The scout filters known Government of Canada template/boilerplate metadata while retaining the source HTML snapshot for auditability.

## Title-derived identifiers

The script can extract high-confidence identifiers displayed in titles, including patterns such as:

- filing numbers;
- filing sequence;
- exhibit numbers;
- activity numbers;
- regulatory instrument numbers;
- explicit EN/FR markers.

These are recorded as title-pattern-derived metadata; they are not represented as if REGDOCS supplied them through a structured API.

## Progress and monitoring

Progress is written to SQLite and atomically projected to:

```text
_audit/scout-progress.json
```

Logs default to:

```text
_audit/scout.log
```

Typical live monitor:

```bash
watch -n 5 'python regdocs_1_scout.py --status'
```

The status includes:

- run ID and phase;
- completed/total units;
- percentage;
- request counts;
- retries and failed attempts;
- elapsed time;
- ETA when estimable;
- heartbeat age;
- stale-heartbeat detection;
- unresolved error count;
- current progress message.

## Locking

The scout uses an exclusive lock derived from the database path:

```text
regdocs.db.scout.lock
```

If a previous process died and left a stale lock, first verify that no scout is actually running. Then:

```bash
python regdocs_1_scout.py --force-lock
```

## Error handling

Warnings and errors are stored in the shared `errors` table with:

- run ID;
- document ID where applicable;
- stage;
- error code;
- severity;
- message;
- retryability;
- JSON context;
- created timestamp;
- resolution timestamp.

A successful later retry can resolve earlier errors without deleting their audit history.

## Exit codes

Typical exit behavior:

| Code | Meaning |
|---:|---|
| `0` | Command/run succeeded |
| `2` | Schema/audit command completed but validation failed |
| `1` | Configuration or fatal runtime error |
| `130` | Interrupted by user |

## Files produced

A normal run may update or create:

```text
regdocs.db
regdocs.db-wal
regdocs.db-shm
regdocs.db.scout.lock          # only while running
raw/regdocs/...
_audit/scout-progress.json
_audit/scout.log
```

## Relationship to Stage 2

Stage 2 reads the same five-table database.

The scout should run before the downloader because it establishes:

- which rows represent downloadable files;
- source URLs;
- catalogue kind;
- metadata;
- scout completion state;
- document identity;
- container membership;
- the shared `files` table used later for downloaded versions.

Next: [Stage 2 downloader](README-regdocs_2_download.md).
