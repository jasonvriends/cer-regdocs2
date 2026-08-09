# Database migration and recovery

REGDOCS Atlas treats `database/regdocs.db` as the fast operational ledger, but durable corpus artifacts are also designed to support disaster recovery.

## Safe schema migration

Preview pending migrations without writing:

```bash
python pipeline.py db migrate --plan
```

Apply migrations:

```bash
python pipeline.py db migrate
```

For an existing database, the default migration path:

1. refuses to run while a live Scout, Download, Analyze, or Normalize stage lock exists;
2. creates a consistent SQLite backup with SQLite's backup API under `database/backups/`;
3. applies only pending named migrations;
4. verifies the expected schema;
5. runs `PRAGMA integrity_check`; and
6. runs `PRAGMA foreign_key_check`.

Use `--no-backup` only when intentionally suppressing that safety copy.

Verify independently:

```bash
python pipeline.py db verify
python pipeline.py db status
```

The migration registry is `schema_migrations`. `PRAGMA user_version` is not the authoritative migration registry because the historical Scout implementation already uses that pragma.

## Current migration chain

```text
001_base_ledger
002_analyses
003_normalizations
004_release_tracking
005_recovery_tracking
006_recovery_state
```

Migration 006 adds recovery state without rewriting normal corpus records. Existing documents default to:

```text
acquisition_state = OBSERVED
scout_refresh_needed = 0
```

Only an artifact rebuild creates `RECOVERED_*` states and recovery tasks.

## Durable Stage 2 sidecars

Normal Stage 2 public runs now write deterministic metadata sidecars by default:

```text
workspace/2_download/files/4710492.pdf
workspace/2_download/files/4710492.metadata.json
```

The sidecar preserves current document metadata plus the source SHA-256 and file facts needed for stronger reconstruction. Opt out only when intentional:

```bash
python pipeline.py download --no-sidecars
```

Backfill or refresh sidecars without downloads:

```bash
python pipeline.py download --sidecars-only
```

## Rebuild workflow

Inventory surviving artifacts:

```bash
python pipeline.py rebuild inventory
```

Preview the best available recovery tier:

```bash
python pipeline.py rebuild plan
```

Create a new database; the command refuses to overwrite an existing target:

```bash
python pipeline.py rebuild create \
  --output database/regdocs.rebuilt.db
```

Verify it:

```bash
python pipeline.py rebuild verify \
  --db database/regdocs.rebuilt.db
```

The current rebuild implementation reconstructs current `documents` and `files` from surviving Stage 2 source files. A matching valid Stage 2 sidecar restores current metadata. Without a sidecar, the record is explicitly `RECOVERED_MINIMAL`; unknown title/URL/filing metadata remains empty rather than being fabricated.

Matching canonical Azure and Docling Stage 3 JSON artifacts are reconstructed into `analyses` when their artifact path/payload identity agrees with the recovered document ID and source SHA-256. This does not call Azure or rerun Docling.

Stage 4 JSONL is preserved but `normalizations` rows are not yet reconstructed from bare JSONL because the current outputs do not independently prove every normalizer/config/input identity. Generation manifests are the next requirement for that layer.

## Missing Scout data

A rebuild does not pretend recovered metadata is equivalent to preserved Scout evidence. Missing acquisition facts are represented explicitly in:

```text
documents.acquisition_state
documents.scout_refresh_needed
documents.recovery_missing_facts_json
recovery_provenance
recovery_tasks
```

Typical states:

```text
OBSERVED             normal document created by the acquisition pipeline
RECOVERED_COMPLETE   artifact recovery contains the required current facts
RECOVERED_PARTIAL    useful metadata recovered, but acquisition evidence/facts are missing
RECOVERED_MINIMAL    only minimal source/file identity can be proven
```

Inspect queued Scout repair work:

```bash
python pipeline.py recover scout \
  --db database/regdocs.rebuilt.db
```

Prioritize source-only records:

```bash
python pipeline.py recover scout \
  --db database/regdocs.rebuilt.db \
  --priority HIGH
```

Export just the IDs:

```bash
python pipeline.py recover scout \
  --db database/regdocs.rebuilt.db \
  --priority HIGH \
  --ids-only
```

At release 0.0.3 this command exposes the durable repair queue; it does not yet issue selective REGDOCS network requests. The next Scout refactor should consume this queue and complete tasks only after fresh authoritative evidence is stored.
