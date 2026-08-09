# REGDOCS Atlas release notes

## 0.0.3 — Safe migrations and artifact-driven recovery — 2026-08-09

`0.0.3` turns the database-recovery design into an executable recovery path while keeping normal stage behavior and expensive analyzer artifacts intact.

### Migration safety

`python pipeline.py db migrate` now:

- supports `--plan` for a read-only migration preview;
- refuses schema changes while a live Scout, Download, Analyze, or Normalize stage lock is present;
- creates a transactionally consistent SQLite backup by default with the SQLite backup API, including committed WAL state;
- stores migration backups under `database/backups/` by default;
- runs schema verification, `PRAGMA integrity_check`, and `PRAGMA foreign_key_check` after migration; and
- stores migration fingerprints/checksums so an already-applied migration cannot silently drift later.

Use `--no-backup` only when intentionally suppressing the default safety copy. The compatibility command `python pipeline/regdocs_release.py --sync-db` now routes through the same safe migration path.

### Recovery-state migration

Migration `006_recovery_state` adds explicit recovery semantics without rewriting normal documents. Existing documents default to `acquisition_state='OBSERVED'` and `scout_refresh_needed=0`.

Artifact-rebuilt documents can instead be represented as:

```text
RECOVERED_COMPLETE
RECOVERED_PARTIAL
RECOVERED_MINIMAL
```

with explicit missing-fact JSON, rebuild provenance, and a `recovery_tasks` queue. Unknown titles/URLs are left empty for minimal recovery instead of being fabricated.

### Durable Stage 2 sidecars

Normal Stage 2 public runs now enable deterministic `<document-id>.metadata.json` sidecars by default. Use `--no-sidecars` only to intentionally opt out. Existing `--sidecars-only` remains available to backfill/refresh sidecars without downloading source files.

### Rebuild implementation

The unified CLI now supports:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

`rebuild create` refuses to overwrite an existing target. It creates the new ledger through the same migration chain, hashes surviving current source files, validates matching Stage 2 sidecars, reconstructs `documents` and `files`, and reconstructs matching Azure/Docling `analyses` rows when canonical Stage 3 artifacts agree with the recovered document ID and source SHA-256.

If only a source file survives, the document is recovered minimally and the missing Scout-derived facts are explicitly recorded. If a valid Stage 2 sidecar survives, current metadata is recovered from the sidecar but missing raw Scout evidence remains visible.

Stage 4 `normalizations` rows are deliberately not invented from bare JSONL output yet. They will be reconstructed once generation/manifests can prove the normalizer/config/input identities. The existing Stage 4 files remain preserved on disk.

### Scout recovery queue

A rebuild queues missing acquisition work instead of treating partial data as either fully valid or unusable:

```bash
python pipeline.py recover scout --db database/regdocs.rebuilt.db
python pipeline.py recover scout --db database/regdocs.rebuilt.db --priority HIGH
python pipeline.py recover scout --db database/regdocs.rebuilt.db --ids-only
```

Source-only records receive HIGH-priority Scout repair work. Sidecar-recovered records with otherwise complete current metadata normally receive LOW-priority work for missing raw Scout evidence.

## 0.0.2 — Unified CLI and database migration foundation — 2026-08-09

`0.0.2` begins the package refactor without changing the established Scout → Download → Analyze → Normalize → Index data contracts.

### New command surface

A root `pipeline.py` is now the preferred orchestration entry point:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize --provider azure
python pipeline.py normalize --provider docling
python pipeline.py index
```

The existing `pipeline/regdocs_*.py` commands remain supported. The unified CLI delegates stage execution to those existing entry points in this release so provider behavior, crash isolation, retry/billing semantics, and artifact formats are preserved while the internal package is introduced.

New operational commands include:

```bash
python pipeline.py version
python pipeline.py status
python pipeline.py diagnostics
python pipeline.py db migrate
python pipeline.py db status
python pipeline.py db verify
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
```

### Central database migrations

`regdocs_atlas/db/migrations.py` is now the target owner for SQLite schema evolution.

The first migration chain is:

```text
001_base_ledger
002_analyses
003_normalizations
004_release_tracking
005_recovery_tracking
```

Migrations use a dedicated `schema_migrations` table rather than `PRAGMA user_version`, because the legacy Scout implementation already uses that pragma for its historical schema marker.

The migration runner is intentionally adoption-friendly: on an existing current database, idempotent migration steps verify/make the current shape true and then record themselves without rewriting historical `runs.script_version`, parser versions, release stamps, source hashes, or analysis identities. On an empty SQLite file, the same migration chain creates the complete current Stage 1–4 + release/recovery schema without requiring Scout to run first.

Run after pulling:

```bash
python pipeline.py db migrate
python pipeline.py db verify
```

This also updates `pipeline_metadata['release_version']` to `0.0.2`. Historical run rows retain their prior release provenance; new rows continue to receive the current release through the existing release trigger.

### Shared package foundation

The new `regdocs_atlas` package introduces common modules for repository version/path resolution, SQLite connection policy, schema migrations, generic run/error lifecycle helpers, PID-aware process locks, atomic durable writes, stable hashing/JSON helpers, and durable artifact inventory/recovery planning.

`python pipeline.py rebuild inventory` and `rebuild plan` are deliberately read-only first steps toward the artifact-layer SQLite disaster-recovery roadmap. They do not yet write a reconstructed ledger.

### Refactor plan

See [`roadmap/PIPELINE_REFACTOR.md`](roadmap/PIPELINE_REFACTOR.md). The next phases move existing stages onto shared infrastructure, replace worker monkey-patching with explicit parent-run contracts, extract the common Stage 3 supervisor, split Scout/Download, introduce provider-neutral normalization adapters, and finish artifact reconstruction.

The large legacy implementations are intentionally retained in `0.0.2`; they will only be removed after regression, restart/failure, and artifact-reuse equivalence is demonstrated.

## 0.0.1 — Initial integrated pipeline baseline — 2026-08-09

`0.0.1` establishes the first repository-wide release baseline for the REGDOCS Atlas processing pipeline. Earlier per-script numbers remain historical component identifiers rather than product release numbers.

### Pipeline included in this release

- **Stage 1 — Scout:** discovers Canada Energy Regulator REGDOCS records, preserves raw registry evidence, records document/container structure, metadata, runs, errors, and source snapshots in the shared SQLite ledger.
- **Stage 2 — Download:** downloads current source files, validates content, records SHA-256 identity and file metadata, preserves replaced versions, and maintains download sidecars/artifacts.
- **Stage 3 — Azure Content Understanding:** crash-resilient single-child supervisor, one public invocation per `runs` row, resumable `Content-Range` analysis for PDFs over 300 pages, preserved native JSON/Markdown/range artifacts, conservative retry boundaries to avoid accidental rebilling, and current-file hash validation.
- **Stage 3 — Docling:** local alternate analysis provider with a single active child process, fresh-process crash isolation, retry/quarantine behavior, preserved native Docling output plus the REGDOCS compatibility projection, and conversion status/error preservation for `success`, `partial_success`, and failed conversions.
- **Stage 4 — Normalize:** `regdocs_4_normalize.py` is the primary Azure/Docling provider-selecting supervisor. It owns one Stage 4 run, launches one document child at a time, records document-level failures without terminating the remaining corpus by default, builds per-document shards, and assembles deterministic document/page/chunk/table/provenance JSONL.
- **Stage 5 — Index:** validates normalized chunk/provenance identity and publishes the first keyword/filter/facet Azure AI Search chunk index while retaining Stage 4 as the authoritative normalized/provenance layer.

### Data and recovery baseline

- one shared SQLite ledger for Stages 1–4;
- persistent Stage 1 source evidence, Stage 2 source files, Stage 3 analyzer artifacts, and Stage 4 normalized projections under `workspace/`;
- analyzer artifacts are treated as valuable durable computation rather than disposable scratch;
- Azure AI Search remains a rebuildable publication target;
- stale PID-aware process locks are used by the long-running pipeline supervisors;
- the roadmap now includes rebuilding SQLite corpus state from preserved artifacts so loss of the ledger does not require re-downloading sources or rerunning expensive Stage 3 analysis;
- the roadmap also includes bounded Docling model reuse in future workers while preserving exactly one active document-processing child at a time.

### Versioning change

This release introduces a single repository-wide version in [`VERSION`](VERSION). See [`VERSIONING.md`](VERSIONING.md).

All six primary public stage commands now share that release version contract. `--version` prints only `0.0.1`; `--diagnostics` exposes component/parser/schema/provider details separately. The public stage files are thin release-aware entry points and delegate normal execution to adjacent internal `*_core.py` implementations.

This deliberately separates:

```text
release_version     whole REGDOCS Atlas release
component_version   implementation identity
parser/schema/API   data/provider compatibility identity
```

Do **not** rewrite old `runs.script_version`, parser versions, schema versions, Azure API versions, Docling versions, or artifact identities to `0.0.1`. Those values are historical or compatibility/provenance identifiers.

The SQLite release uplift is intentionally additive:

```text
runs.release_version
pipeline_metadata['release_version']
```

Historical rows remain unchanged and normally have `release_version = NULL`. After syncing the local database, new runs are stamped with the current repository release by a SQLite trigger without requiring every existing stage INSERT statement to be changed immediately.

Run after pulling this release:

```bash
python pipeline/regdocs_release.py --sync-db
```

Inspect the resulting release state with:

```bash
python pipeline/regdocs_release.py --status
```

Inspect any primary stage's implementation identity separately with, for example:

```bash
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_4_normalize.py --diagnostics
```

### Known limitations at 0.0.1

- the pipeline is still a prototype and not an unattended production service;
- Stage 5 is the initial keyword/filter/facet baseline and does not yet establish semantic/vector retrieval as the default;
- Stage 4 still needs versioned manifested generations and stronger atomic publication before production use;
- analyzer comparison and automatic canonical provider selection are not yet complete;
- the SQLite artifact-rebuild path is planned and documented but is not yet a complete disaster-recovery implementation;
- legacy internal `SCRIPT_VERSION` constants and the SQLite `script_version` column remain for compatibility with existing run/artifact provenance; new public surfaces call these component versions, and future implementation revisions should migrate those constants to purpose-specific names without rewriting history.

### Release policy going forward

For the prototype, bump the whole repository release once for each coherent checkpoint:

```text
0.0.1  current integrated baseline
0.0.2  next coherent pipeline release
0.0.3  following coherent pipeline release
...
```

A release bump alone must not invalidate expensive Stage 3 artifacts. Artifact reuse continues to depend on source hashes, analyzer/provider identities, API/package versions, parser/projection contracts, and other relevant compatibility metadata.
