# Database migration and recovery

REGDOCS Atlas treats `database/regdocs.db` as an operational ledger. The durable recovery boundary for the pilot is intentionally **source through Stage 3**:

```text
Stage 1 Scout evidence       durable
Stage 2 source files         durable
Stage 3 analyzer artifacts   durable / expensive to reproduce
SQLite ledger                rebuildable from those layers
Stage 4 Normalize            rerun locally from Stage 3
Stage 5 Azure AI Search      republish from Stage 4
```

The current corpus has been side-by-side rebuilt and compared successfully through Stage 3 without another REGDOCS crawl, source download, or Azure analysis request.

## Safe schema migration

Preview and apply migrations with:

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify
```

For an existing database, migration takes the orchestration lock, refuses to run beside active stage writers, creates a consistent SQLite backup by default, applies only pending named migrations, and runs schema, integrity, and foreign-key verification.

The migration registry is `schema_migrations`. `PRAGMA user_version` is not the authoritative registry because the historical Scout implementation already used it.

Current migration chain:

```text
001_base_ledger
002_analyses
003_normalizations
004_release_tracking
005_recovery_tracking
006_recovery_state
```

## Durable Stage 1 evidence

Scout raw HTML is stored under `workspace/1_scout/raw/`. Content-addressed HTML alone cannot reproduce every observation field that lived in SQLite, so `rebuild prepare` also exports document and snapshot manifests:

```text
workspace/1_scout/manifests/documents/
workspace/1_scout/manifests/snapshots/
workspace/1_scout/manifests/export-summary.json
```

Run:

```bash
python pipeline.py rebuild prepare
```

By default this verifies each referenced Scout gzip against compressed size, uncompressed size, and SHA-256. After a previous full verification, a faster refresh can use:

```bash
python pipeline.py rebuild prepare --no-verify-raw
```

The preferred Scout command refreshes manifests after normal acquisition runs. During reconstruction, old snapshot IDs are mapped to newly generated SQLite IDs; historical `runs` rows are not fabricated.

## Durable Stage 2 evidence

Current source files live under `workspace/2_download/files/`. Stage 2 writes deterministic metadata sidecars by default:

```text
workspace/2_download/files/4710492.pdf
workspace/2_download/files/4710492.metadata.json
```

A sidecar preserves the current document/file facts needed to pair the source bytes with their SHA-256 identity. Existing sidecars can be refreshed without downloading files:

```bash
python pipeline.py download --sidecars-only
```

## Durable Stage 3 evidence

Azure and Docling native analysis artifacts live under `workspace/3_analyze/`.

`rebuild prepare` exports one manifest for each successful current-file analysis under:

```text
workspace/3_analyze/manifests/analyses/
```

Each manifest preserves the successful analysis identity and counts while hashing the actual analyzer artifact. This is important because a historical provider JSON file should not have to satisfy a newer parser assumption merely to prove that the original successful analysis existed.

The manifest identity includes:

```text
document_id
source file SHA-256
analyzer_id
API/provider version
artifact path + SHA-256
page/table/section/warning counts
original analysis/run references where available
```

A recovery therefore verifies the surviving artifact bytes and restores the proven analysis identity without resubmitting Azure work.

## Inventory and recovery plan

Inspect durable state with:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
```

Useful inventory fields include:

```text
scout_document_manifests
scout_snapshot_manifests
current_source_files
stage2_sidecars
azure_canonical_analysis_json
docling_canonical_analysis_json
stage3_analysis_manifests
normalized_outputs_present
```

Tier A means the self-describing Scout recovery layer and current sources are available. Raw Scout HTML without manifests is reported separately rather than being overstated as a complete Tier A source.

## Safest rebuild test

Do not delete the working DB first. Build a second ledger beside it:

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

`rebuild create` refuses to overwrite an existing target.

It reconstructs, in evidence order:

1. verified Scout snapshots into `raw_snapshots`;
2. Scout document manifests, including non-file Folder/Compound/Paper-only entities and container relationships;
3. current Stage 2 sources and sidecars into file/download state; and
4. successful Stage 3 identities from verified analysis manifests, with canonical artifact parsing retained as a fallback.

Unknown recovery facts remain unknown. They are never invented.

## What a successful comparison means

`rebuild compare` checks:

- document ID sets;
- current `(document_id, SHA-256)` file identities;
- Scout `(source_kind, source_url, content SHA-256)` identities;
- successful Stage 3 `(document_id, source SHA-256, analyzer, version)` identities;
- container parent/member relationships;
- core document metadata; and
- SQLite integrity and foreign keys.

For the current pilot corpus the target condition is:

```text
source_and_stage3_equivalent: true
```

A normalization count difference is intentional and is reported separately as:

```text
normalization_recovery_expected_gap: true
```

That does **not** mean Stage 4 data must become another disaster-recovery layer. Normalize is local and rebuildable from the restored Stage 3 artifacts.

## After a database-loss recovery

After promoting a verified rebuilt DB, regenerate local derivatives as needed:

```bash
python pipeline.py normalize --provider azure
python pipeline.py index
```

Use Docling instead when that is the selected Stage 3 provider.

This pilot intentionally avoids a per-normalization recovery-manifest system because rerunning Normalize is cheap, local, and non-billable compared with reacquiring Stage 1/2 data or resubmitting Stage 3 Azure analysis.

## Recovery state and selective Scout repair

Artifact-rebuilt documents can record:

```text
OBSERVED
RECOVERED_COMPLETE
RECOVERED_PARTIAL
RECOVERED_MINIMAL
```

with supporting state in:

```text
documents.scout_refresh_needed
documents.recovery_missing_facts_json
recovery_provenance
recovery_tasks
```

Inspect queued Scout repairs without network requests:

```bash
python pipeline.py recover scout --db database/regdocs.rebuilt.db
python pipeline.py recover scout --db database/regdocs.rebuilt.db --priority HIGH
python pipeline.py recover scout --db database/regdocs.rebuilt.db --priority HIGH --ids-only
```

Execute selective authoritative detail refresh only when needed:

```bash
python pipeline.py recover scout \
  --execute \
  --db database/regdocs.rebuilt.db \
  --priority HIGH \
  --limit 100
```

Selective repair preserves the fetched detail HTML and updates only facts supported by that response. It does not pretend that current detail evidence reconstructs missing historical search/container observations.

## What to preserve

For disaster recovery, preserve at least:

```text
workspace/1_scout/
workspace/2_download/
workspace/3_analyze/
workspace/4_normalize/   # useful derivative; rerunnable if lost
VERSION
RELEASE_NOTES.md
```

Keep secrets/configuration separately. Azure AI Search is a derivative publication target and is not a backup source of truth.
