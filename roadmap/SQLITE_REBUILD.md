# SQLite Rebuild and Partial-Recovery Roadmap

This note expands the SQLite ledger disaster-recovery item in the main
[`ROADMAP.md`](../ROADMAP.md).

The design goal is **layered, evidence-based reconstruction**. A missing
`database/regdocs.db` must not turn preserved source files or expensive analysis
artifacts into unusable data simply because an earlier pipeline layer is absent.

The rebuild path should recover every fact that surviving artifacts can prove,
explicitly report what cannot be recovered, and never manufacture missing Scout
metadata, relationships, URLs, timestamps, or run history.

## Core principle

Rebuildability is not all-or-nothing.

```text
surviving artifacts
        |
        v
inventory + verify
        |
        +--> facts that can be proven ----------> rebuild ledger rows
        |
        +--> facts that cannot be proven -------> explicit recovery gaps
        |
        `--> ambiguous/corrupt artifacts --------> quarantine / fail closed
```

A rebuild should therefore distinguish:

- **complete reconstruction** — the durable artifacts contain enough evidence to
  restore the normal corpus identity and provenance for the layer;
- **partial reconstruction** — useful downstream state can be restored, but some
  upstream evidence or metadata is unavailable;
- **unrecoverable facts** — data existed only in the lost ledger or a missing
  artifact and must remain unknown; and
- **ambiguous state** — conflicting or insufficient evidence must be reported and
  not guessed.

## Why partial recovery has value

The expensive and operationally valuable data is not limited to Scout metadata.
A surviving Stage 2 source file can be hashed and identified locally. Surviving
Azure Content Understanding or Docling artifacts may represent thousands of
pages of paid or compute-expensive analysis. Surviving Stage 4 normalized output
may still be useful for inspection and search publication.

Loss of Stage 1 evidence should therefore reduce provenance completeness, not
force the system to discard otherwise verifiable Stage 2–4 data.

The design must still preserve the distinction between **having a source file**
and **having authoritative REGDOCS acquisition evidence for that file**.

## Recovery tiers

### Tier A — Scout evidence + source files + downstream artifacts

Available:

```text
workspace/1_scout/raw/
workspace/2_download/files/
workspace/3_analyze/...
workspace/4_normalize/...
```

Target: the fullest artifact-based reconstruction possible.

Recover, where supported by the artifacts:

- document identity and REGDOCS metadata;
- Folder/Compound relationships;
- source URLs and raw snapshot evidence;
- current and historical file versions;
- source SHA-256 identities;
- Azure and Docling analysis rows;
- normalization rows/generations; and
- current corpus provenance needed to resume Stages 2–5.

Historical operational `runs` and `errors` remain a separate backup concern and
need not be reproduced exactly.

### Tier B — source files + Stage 2 sidecars, but Scout raw evidence is missing

This is an important recovery case because current Stage 2 sidecars already
project substantial SQLite state, including document ID, title, source URL,
item kind, filing date, submitter, company, project, filing number, snippet,
SHA-256, local file facts, pipeline statuses/timestamps, and the full
`documents.metadata` object.

Target: reconstruct most current `documents` and `files` state from sidecars and
source bytes, then attach any surviving Stage 3/4 artifacts.

Do **not** claim that Stage 1 raw acquisition evidence still exists. In
particular:

- `raw_snapshots` cannot be recreated when their preserved HTML/gzip evidence is
  absent;
- a synthetic successful Scout run must not be invented;
- source metadata recovered from a Stage 2 sidecar must be labelled as recovered
  from that sidecar rather than from a newly observed REGDOCS page; and
- relationships should only be restored when the sidecar metadata actually
  carries sufficient relationship evidence.

This recovery mode should still be capable of resuming analysis, normalization,
and publication without reacquiring or reanalyzing unchanged source files.

### Tier C — source files exist, but Scout evidence and Stage 2 sidecars are missing

Current Stage 2 naming gives a useful deterministic join key:

```text
workspace/2_download/files/<document-id>.<extension>
```

A rebuild can therefore prove at least:

- document ID when the filename conforms to the managed Stage 2 convention;
- current local source path;
- file type/extension detectable from the bytes;
- file size; and
- SHA-256 of the surviving bytes.

It **cannot** safely infer or fabricate fields such as:

- REGDOCS title;
- authoritative source URL;
- filing date;
- submitter/company/project;
- filing number;
- Folder/Compound membership;
- original Scout status or crawl run; or
- raw snapshot history.

The rebuilt ledger may create a minimal document/file identity only if the
current schema and downstream stages can represent that state safely. If they
cannot, introduce an explicit recovered/minimal state rather than filling
required columns with plausible-looking fake values.

Surviving Stage 3 manifests/artifacts may then enrich this minimal identity only
with fields those artifacts themselves prove.

### Tier D — Stage 3 artifacts survive but the source file is missing

If a Stage 3 artifact is self-describing enough to prove document ID, source
SHA-256, provider, analyzer/version/configuration, and artifact integrity, retain
that analysis in the recovery inventory.

However, the ledger must mark the source bytes as missing. The presence of an
analysis artifact is not proof that the original source file is still available.

This state may be useful for forensic inspection or later reconciliation if the
source file is restored, but it must not silently become a normal current-source
record.

### Tier E — only normalized Stage 4 output survives

A Stage 4 generation may be sufficient to rebuild a read-only/searchable
projection if its generation manifest and provenance are intact.

It is not a substitute for source evidence. Mark the corpus as degraded and do
not claim that Stage 1–3 source/analyzer artifacts remain available.

This tier is primarily useful for emergency inspection/export while the
underlying durable corpus is restored.

## Make future artifacts independently reconstructable

The recovery design becomes much stronger if every expensive or authoritative
layer has a small durable manifest next to the large artifact.

### Stage 2

Move deterministic source sidecars from optional convenience toward a standard
durable recovery artifact for every successfully managed current source file.
At minimum preserve:

```text
document_id
source_url, when known
title / key REGDOCS metadata, when known
source_file_path
sha256
size_bytes
mime_type / extension
downloaded_at
sidecar_schema_version
source metadata / relationship evidence needed for recovery
```

The sidecar must distinguish fields copied from Scout evidence from facts
measured directly from the local source file.

### Stage 3 Azure / Docling

Every analysis should have an artifact-side manifest sufficient to reconstruct
an `analyses` row without SQLite:

```text
document_id
source_sha256
source_path or stable source identity
provider
analyzer_id
provider/analyzer version
API/package version
parser/projection version
configuration fingerprint
range/part identity for large Azure PDFs
native artifact paths
artifact hashes
page count / range information
conversion status and preserved warnings/errors
processed_at
```

For ranged Azure analysis, each accepted range should be independently
recoverable and the assembled result should declare exactly which ranges it
contains.

### Stage 4

A normalized generation manifest should prove:

```text
generation_id
normalizer contract/configuration identity
input analysis identities
provider selection per document
output paths and SHA-256 hashes
document/page/chunk/table/provenance counts
created_at
release_version
```

Stage 5 remains a rebuildable publication target and should not be required to
reconstruct Stages 1–4.

## Rebuild inventory and plan before writing a database

The future utility should first scan the filesystem and produce a recovery plan.
For example:

```text
python pipeline/regdocs_db_rebuild.py --inventory

Scout snapshots                 0
Current source files         7,958
Stage 2 sidecars             7,842
Azure analyses               7,010
Docling analyses             6,975
Normalized generations           1

Recovery classification
  full source metadata       7,842
  minimal file identity        116
  Azure analysis recoverable 7,010
  Docling analysis recoverable 6,975
  Scout raw evidence             0
```

Then:

```text
python pipeline/regdocs_db_rebuild.py --plan
```

should report what will be reconstructed and what will remain unavailable before
any database is created.

Only after review should:

```text
python pipeline/regdocs_db_rebuild.py \
  --rebuild-new-db database/regdocs.rebuilt.db
```

write a new ledger.

Never overwrite the active/only database by default.

## Recovery provenance

Recovered rows need provenance of the rebuild itself. The implementation should
prefer an additive recovery/rebuild record rather than falsifying historical
pipeline runs.

A conceptual recovery observation is:

```text
rebuild_id
entity_type           # document, file, analysis, normalization
entity_identity
recovered_from        # scout_snapshot, stage2_sidecar, source_bytes,
                      # azure_manifest, docling_manifest, stage4_manifest
completeness          # complete, partial, minimal
missing_facts_json
verified_hashes_json
recovered_at
release_version
```

Exact schema is deferred, but normal application code must be able to distinguish
an ordinarily observed/acquired row from one reconstructed after ledger loss.

Do not backfill fake `runs` rows merely to satisfy foreign keys. If historical
run IDs are unavailable, migrations/rebuild logic should support explicit
recovery provenance without pretending a network or analyzer operation occurred.

## Merge/reconciliation after partial rebuild

A partial rebuild should be improvable later.

Example:

```text
Day 1: database lost
       source PDFs + Stage 3 survive
       -> rebuild minimal documents/files + analyses

Day 2: Stage 2 sidecars restored from backup
       -> enrich document metadata without changing source SHA identity

Day 3: Scout raw archive restored
       -> restore raw snapshot evidence / relationships and verify against
          recovered document identities
```

Reconciliation must be monotonic where possible: newly restored authoritative
evidence can fill previously unknown fields, but must not silently overwrite a
conflicting proven identity. Conflicts go to an audit report/quarantine path.

## Validation and fault-injection matrix

Test at least these disaster cases in copied fixtures, never by deleting the
operator's only corpus:

1. DB deleted, all durable artifacts present;
2. DB + Scout raw deleted, Stage 2 sidecars and later artifacts present;
3. DB + Scout raw + sidecars deleted, managed source files and Stage 3 present;
4. DB + source files deleted, Stage 3 artifacts present;
5. DB + Stage 1–3 deleted, manifested Stage 4 generation present;
6. one artifact hash corrupted;
7. document ID/path conflicts with a manifest identity; and
8. an Azure ranged document is missing one range artifact.

For each case verify that the rebuild:

- restores only facts supported by surviving evidence;
- reports missing provenance explicitly;
- preserves expensive Stage 3 results whenever their identity can be verified;
- never invokes REGDOCS, Azure Content Understanding, Docling, or Azure AI Search
  during the rebuild itself;
- fails closed on conflicting identities;
- produces deterministic recovery reports; and
- permits later reconciliation when missing artifacts are restored.

## Priority

This is valuable, but the first implementation should be narrow rather than a
large general-purpose recovery framework.

Recommended order:

1. make Stage 2 sidecars standard/durable for successful current source files;
2. make Stage 3 Azure and Docling artifacts self-describing with small manifests;
3. add `--inventory` / `--verify-rebuildable` reporting;
4. rebuild `documents`, `files`, and `analyses` into a new SQLite database;
5. add Stage 4 generation reconstruction once manifests are stable;
6. add partial/minimal-source recovery when Scout artifacts are absent;
7. add reconciliation of later-restored artifacts; and
8. keep ordinary SQLite backups as the fastest recovery path throughout.

The practical success criterion is:

> If SQLite disappears, preserve as much verified corpus value as the surviving
> artifacts support, and never pay to recreate expensive Stage 3 work merely
> because the ledger was lost.
