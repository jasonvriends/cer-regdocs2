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

## Durable Stage 1 Scout manifests

Raw Scout HTML under `workspace/1_scout/raw/` is valuable authoritative evidence, but a content-addressed gzip file cannot by itself reconstruct every ledger fact that described the HTTP observation. The raw bytes do not necessarily encode the original source URL, final URL, document association, fetch time, response headers, parser version, or the snapshot ID referenced from document/container metadata.

Release 0.0.5 therefore adds small durable Scout manifests:

```text
workspace/1_scout/manifests/documents/
workspace/1_scout/manifests/snapshots/
workspace/1_scout/manifests/export-summary.json
```

While a healthy migrated ledger still exists, prepare the Stage 1 disaster-recovery layer with:

```bash
python pipeline.py rebuild prepare
```

By default this exports Scout document and snapshot provenance and verifies every preserved raw gzip against the ledger's compressed size, uncompressed size, and content SHA-256. Use `--no-verify-raw` only when deliberately doing a faster metadata-only refresh.

The preferred `python pipeline.py scout ...` path refreshes Scout manifests automatically after a successful or partial Scout run, without re-hashing every old raw artifact on every run. The explicit `rebuild prepare` command remains the verification/backfill operation.

During reconstruction, original snapshot IDs are mapped to the new rebuilt SQLite IDs and references inside preserved document/container metadata are remapped accordingly. Historical `runs` rows are not fabricated; an original run ID may be retained as recovery evidence while the rebuilt `raw_snapshots.run_id` remains null.

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

## Recovery inventory semantics

Inventory now separates raw file counts from recoverable canonical identities. In particular, Azure `*.json` totals may include canonical analyses plus Content-Range result and metadata parts for large PDFs.

Useful fields include:

```text
scout_snapshots
scout_document_manifests
scout_snapshot_manifests
azure_analysis_json
azure_canonical_analysis_json
azure_range_result_json
azure_range_metadata_json
azure_other_json
docling_canonical_analysis_json
```

A plan reports Tier A only when the self-describing Scout manifest layer and current sources are available. If raw Scout evidence exists but manifests have not yet been exported, it reports:

```text
A_RAW_EVIDENCE_NEEDS_MANIFESTS
```

rather than overstating what can be reconstructed from raw bytes alone.

## Safest rebuild test: do not delete the working DB first

To prove recovery, build a second ledger beside the working one. This is safer and gives you something quantitative to compare against:

```bash
python pipeline.py rebuild prepare
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

Keep the original `database/regdocs.db` until the rebuilt ledger has been inspected. A filesystem copy of a SQLite database taken while writers are active is not the preferred safety copy; use the migration backup API or stop all writers first.

For a meaningful disaster-recovery test, preserve at least:

```text
workspace/1_scout/          # raw Scout evidence + durable manifests
workspace/2_download/       # source files + sidecars + historical versions
workspace/3_analyze/        # Azure and Docling artifacts
workspace/4_normalize/      # normalized corpus
VERSION
RELEASE_NOTES.md
```

Also preserve any non-repository secrets/configuration you need to contact REGDOCS/Azure later. Azure AI Search itself is a rebuildable publication target and does not need to be part of the local recovery backup.

If you intentionally want to simulate complete SQLite loss after a successful side-by-side test, stop every pipeline writer first, retain the original DB/backup outside the active path, remove any old `-wal`/`-shm` only after SQLite is closed, and promote the verified rebuilt DB only after comparison.

## What rebuild-create reconstructs

`rebuild create` refuses to overwrite an existing target. It creates the new ledger through the same migration chain, then reconstructs layers in evidence order:

1. verified Scout snapshot manifests + raw HTML into `raw_snapshots`;
2. Scout document manifests, including non-file Folder/Compound/Paper-only entities and container metadata;
3. current Stage 2 source files and matching sidecars into `files` and download state;
4. matching canonical Azure/Docling Stage 3 artifacts into `analyses` when document ID and source SHA-256 agree.

If Scout manifests are absent but a valid Stage 2 sidecar survives, the current file/document record is still recoverable but missing Scout evidence remains explicit. If only a source file survives, the document is explicitly `RECOVERED_MINIMAL`; unknown title/URL/filing metadata remains empty rather than being fabricated.

Stage 4 JSONL is preserved but `normalizations` rows are not yet reconstructed from bare JSONL because the current outputs do not independently prove every normalizer/config/input identity. Generation manifests are still required before that layer can be reconstructed safely.

## Rebuild comparison

Use:

```bash
python pipeline.py rebuild compare \
  --source database/regdocs.db \
  --rebuilt database/regdocs.rebuilt.db
```

The comparison checks:

- document ID sets;
- current `(document_id, SHA-256)` file identities;
- Scout `(source_kind, source_url, content SHA-256)` snapshot identities;
- successful Stage 3 `(document_id, source SHA-256, analyzer, version)` identities;
- container parent/member relationships;
- core document fields such as title, URL, kind, filing date, submitter/company/project, filing number and snippet; and
- SQLite integrity/foreign keys.

It reports Stage 4 normalization counts separately. Until Stage 4 generation manifests are implemented, a normalization-row difference is an expected recovery gap and does not by itself make the Stage 1-3 comparison fail.

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

Execute a selective authoritative REGDOCS detail refresh for queued records:

```bash
python pipeline.py recover scout \
  --execute \
  --db database/regdocs.rebuilt.db \
  --priority HIGH \
  --limit 100
```

The selective recovery path only processes queued numeric REGDOCS item IDs. For each successful fetch it preserves the fresh detail HTML under `workspace/1_scout/raw/regdocs/recovery-detail/`, inserts a `raw_snapshots` row, parses current detail metadata with the existing Scout parser, and updates only facts supported by that response. It marks a recovery task `PARTIAL` when facts such as container relationships are still unproven rather than claiming the document is fully observed.

This is intentionally different from rerunning the historical date crawl: recovery repairs known surviving document identities first, while missing historical search/container evidence remains visible until separately reacquired.
