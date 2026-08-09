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
    +-- rebuild inventory/plan/create/verify
    +-- recover scout
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

Schema changes have one target home: `regdocs_atlas/db/migrations.py`.

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
006_recovery_state
```

Release 0.0.3 adds migration fingerprints/checksums, a read-only migration plan,
consistent SQLite backups before existing-ledger migration, live-stage lock
checks, and post-migration schema/integrity/foreign-key verification.

`PRAGMA user_version` is intentionally not the migration registry because legacy Scout currently uses it for its own schema marker.

## Phase 2 — adopt shared infrastructure in existing stages

**In progress.**

Completed:

- legacy `regdocs_paths.py` is a compatibility export of the single `regdocs_atlas.paths` contract;
- the legacy `regdocs_release.py --sync-db` command routes through the safe central migration CLI instead of carrying its own schema logic;
- Stage 2 public runs now enable deterministic sidecars by default, giving the rebuild path a durable current-metadata artifact.

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

Decompose Scout behind unchanged behavior into client/parser/container/identity/raw-store/service modules. Decompose Download into HTTP/filetype/reconcile/version/sidecar/service modules. Stage 2 sidecars are now durable by default; the refactor should move their schema/write logic into a reusable package module without changing bytes.

The Scout refactor must also add an explicit selective repair API that consumes
`recovery_tasks` and marks tasks complete only after fresh authoritative REGDOCS
evidence is stored. Release 0.0.3 creates and exposes this queue but deliberately
does not issue selective repair requests yet.

## Phase 6 — provider-neutral normalization

Move analyzer interpretation behind Azure and Docling adapters that emit one
REGDOCS analysis model before chunking/tables/provenance. This is the prerequisite
for deterministic mixed-provider canonical selection.

## Phase 7 — finish SQLite artifact reconstruction

**Started in release 0.0.3.**

Implemented:

- `rebuild create` creates a new target through the normal migration chain and refuses overwrite;
- current source bytes are re-hashed and reconstructed into `documents`/`files`;
- valid Stage 2 sidecars restore current document metadata;
- source-only records are explicitly `RECOVERED_MINIMAL` rather than populated with invented title/URL values;
- matching Azure/Docling canonical Stage 3 artifacts can reconstruct successful `analyses` rows without provider calls;
- every recovered document/file/analysis carries recovery provenance;
- missing Scout facts become prioritized `recovery_tasks` visible through `pipeline.py recover scout`.

Still required:

- parse/preserve surviving Stage 1 raw evidence into `raw_snapshots` and authoritative Scout metadata where possible;
- add selective Scout task execution and completion;
- add Stage 3 artifact-side manifests where current native artifacts do not independently prove all identity fields;
- add Stage 4 generation manifests and reconstruct `normalizations` only when normalizer/config/input identity can be proven;
- compare rebuilt and original ledgers in fault-injection tests;
- add recovery for historical source versions where artifact evidence is sufficient.

See `roadmap/SQLITE_REBUILD.md` and `docs/DATABASE_RECOVERY.md`.

## Testing and removal rule

Every extraction must prove clean migration, adoption, idempotence, output
identity, restart/failure behavior, and artifact reuse. Do not delete a legacy
`*_core.py` until its package replacement is demonstrably equivalent.
