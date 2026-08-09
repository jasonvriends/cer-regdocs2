# Database migration and recovery

REGDOCS Atlas treats `database/regdocs.db` as the fast operational ledger, while durable corpus artifacts are designed to support independent disaster recovery.

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

1. takes the canonical `database/locks/pipeline.lock` orchestration lock;
2. refuses to run while a live legacy Scout, Download, Analyze, or Normalize stage lock exists;
3. creates a consistent SQLite backup with SQLite's backup API under `database/backups/`;
4. applies only pending named migrations;
5. verifies the expected schema;
6. runs `PRAGMA integrity_check`; and
7. runs `PRAGMA foreign_key_check`.

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

Normal Stage 2 public runs write deterministic metadata sidecars by default:

```text
workspace/2_download/files/4710492.pdf
workspace/2_download/files/4710492.metadata.json
```

The sidecar preserves current document metadata plus source SHA-256 and file facts needed for stronger reconstruction. Opt out only intentionally:

```bash
python pipeline.py download --no-sidecars
```

Backfill or refresh sidecars without downloads:

```bash
python pipeline.py download --sidecars-only
```

## Safest rebuild test: do not delete the working DB first

To prove recovery, build a second ledger beside the working one. This is safer and gives you something to compare against:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

Keep the original `database/regdocs.db` until the rebuilt ledger has been inspected. A filesystem copy of a SQLite database taken while writers are active is not the preferred safety copy; use the migration backup API or stop all writers first.

For a meaningful disaster-recovery test, preserve at least:

```text
workspace/1_scout/          # raw Scout evidence, if available
workspace/2_download/       # source files + sidecars + historical versions
workspace/3_analyze/        # Azure and Docling artifacts
workspace/4_normalize/      # normalized corpus
VERSION
RELEASE_NOTES.md
```

Also preserve any non-repository secrets/configuration you need to contact REGDOCS/Azure later. Azure AI Search itself is a rebuildable publication target and does not need to be part of the local recovery backup.

If you intentionally want to simulate complete SQLite loss after a successful side-by-side test, stop every pipeline writer first, retain the original DB/backup outside the active path, remove any old `-wal`/`-shm` only after SQLite is closed, and promote the verified rebuilt DB only after comparison.

## What rebuild-create currently reconstructs

`rebuild create` refuses to overwrite an existing target. It creates the new ledger through the same migration chain, hashes surviving current Stage 2 source files, validates matching Stage 2 sidecars, reconstructs `documents` and `files`, and reconstructs matching Azure/Docling `analyses` rows when canonical Stage 3 artifacts agree with the recovered document ID and source SHA-256.

If only a source file survives, the document is explicitly `RECOVERED_MINIMAL`; unknown title/URL/filing metadata remains empty rather than being fabricated. If a valid Stage 2 sidecar survives, current metadata is recovered from that sidecar but missing raw Scout evidence remains visible.

Stage 4 JSONL is preserved but `normalizations` rows are not yet reconstructed from bare JSONL because the current outputs do not independently prove every normalizer/config/input identity. Generation manifests are still required before that layer can be reconstructed safely.

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
OBSERVED             normal document backed by acquisition evidence
RECOVERED_COMPLETE   recovery artifacts contain required current facts
RECOVERED_PARTIAL    useful facts exist but acquisition evidence/facts remain missing
RECOVERED_MINIMAL    only minimal source/file identity can be proven
```

Inspect queued Scout work without network requests:

```bash
python pipeline.py recover scout --db database/regdocs.rebuilt.db
python pipeline.py recover scout --db database/regdocs.rebuilt.db --priority HIGH
python pipeline.py recover scout --db database/regdocs.rebuilt.db --priority HIGH --ids-only
```

Execute a selective authoritative REGDOCS detail refresh for the queued records:

```bash
python pipeline.py recover scout \
  --execute \
  --db database/regdocs.rebuilt.db \
  --priority HIGH \
  --limit 100
```

The selective recovery path only processes queued numeric REGDOCS item IDs. For each successful fetch it preserves the fresh detail HTML under `workspace/1_scout/raw/regdocs/recovery-detail/`, inserts a `raw_snapshots` row, parses current detail metadata with the existing Scout parser, and updates only facts supported by that response. It marks a recovery task `PARTIAL` when facts such as container relationships are still unproven rather than claiming the document is fully observed.

This is intentionally different from rerunning the historical date crawl: recovery repairs known surviving document identities first, while missing historical search/container evidence remains visible until separately reacquired.
