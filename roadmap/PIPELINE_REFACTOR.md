# Pipeline Package and Migration Refactor

This plan turns the current collection of large stage scripts into one stable
command surface backed by a reusable Python package, while preserving the
existing stage boundaries, process isolation, SQLite ledger, and durable artifact
contracts.

The rule is **compatibility first**: architecture may move behind the public
commands, but a refactor must not silently change corpus identity, source hashes,
analyzer identities, normalized contracts, or retry/billing behavior.

## Target

```text
pipeline.py
    |
    v
regdocs_atlas.cli
    |
    +-- scout
    +-- download
    +-- analyze azure
    +-- analyze docling
    +-- normalize
    +-- index
    +-- db migrate/status/verify
    +-- rebuild inventory/plan
    |
    v
shared package
    +-- db
    +-- runtime
    +-- artifacts
    +-- stages
```

`pipeline.py` should remain tiny. It is a router, not a monolith.

The existing `pipeline/regdocs_*.py` scripts remain compatibility entry points
until the package stage modules have proven equivalent.

## Phase 1 — unified command surface and database foundation

**Implemented in release 0.0.2.**

- add root `pipeline.py`;
- add `regdocs_atlas` package;
- add `python pipeline.py version`, `status`, and `diagnostics`;
- route Scout, Download, Azure, Docling, Normalize, and Index to the existing tested stage entry points;
- add `python pipeline.py db migrate|status|verify`;
- add an explicit `schema_migrations` registry rather than overloading `PRAGMA user_version`;
- centralize the complete current Stage 1–4 schema in migrations;
- make migrations idempotent so an existing ledger can be adopted;
- create the complete current ledger from an empty SQLite file without running Scout first;
- add shared SQLite connection policy;
- add common run/error helpers for newly refactored stages;
- add common PID lock, atomic-write, and hashing primitives;
- add artifact inventory/recovery-plan primitives;
- reserve rebuild/recovery provenance tables for the database disaster-recovery implementation.

## Migration contract

Schema changes now have one target home: `regdocs_atlas/db/migrations.py`.

A migration must have a stable ID, preserve existing provenance, be idempotent
against current ledgers, fail closed on unknown old shapes, and be tested both
against clean and existing databases.

Current migration chain:

```text
001_base_ledger
002_analyses
003_normalizations
004_release_tracking
005_recovery_tracking
```

`PRAGMA user_version` is intentionally not the migration registry because legacy Scout currently uses it for its own schema marker.

## Phase 2 — adopt shared infrastructure in existing stages

**Started in release 0.0.2.**

Completed:

- legacy `regdocs_paths.py` is now a compatibility export of the single `regdocs_atlas.paths` contract;
- the legacy `regdocs_release.py --sync-db` command now routes through the central migration engine instead of carrying its own release-schema ALTER/trigger implementation.

Next:

- replace duplicated `open_db()` functions with `db.open_ledger()`;
- replace duplicated PID/stale-lock implementations with `runtime.ProcessLock`;
- replace duplicated hashing and atomic-write functions with shared helpers;
- move generic run/error lifecycle behavior to `db.runs`;
- ensure all stages call the migration runner before relying on tables they own;
- stop Stage 1, Stage 3, and Stage 4 from independently creating or altering user tables.

Exit condition: schema ownership exists only in migrations.

## Phase 3 — explicit worker contracts

Replace monkey-patched worker behavior with explicit process APIs carrying
`--parent-run-id`. A child worker must never need runtime monkey-patching to
suppress parent-run creation/completion. Preserve exactly one active document
processing child for Azure, Docling, and Normalize.

## Phase 4 — common Stage 3 supervisor

Extract generic candidate iteration, durable state, one-child-at-a-time launch,
exit interpretation, heartbeat, retry/quarantine boundaries, interruption, and
result reconciliation. Keep Azure billing/range/API behavior and Docling
model/GPU behavior provider-specific.

## Phase 5 — split Scout and Download

Decompose Scout behind unchanged behavior into client/parser/container/identity/raw-store/service modules. Decompose Download into HTTP/filetype/reconcile/version/sidecar/service modules. Stage 2 sidecars must become reusable recovery artifacts.

## Phase 6 — provider-neutral normalization

Move analyzer interpretation behind Azure and Docling adapters that emit one
REGDOCS analysis model before chunking/tables/provenance. This is the prerequisite
for deterministic mixed-provider canonical selection.

## Phase 7 — finish SQLite artifact reconstruction

Build on the migration and artifact inventory foundation. The rebuild command
must create its target through the same migration runner and never maintain a
private copy of schema SQL. See `roadmap/SQLITE_REBUILD.md` for recovery tiers.

## Testing and removal rule

Every extraction must prove clean migration, adoption, idempotence, output
identity, restart/failure behavior, and artifact reuse. Do not delete a legacy
`*_core.py` until its package replacement is demonstrably equivalent.
