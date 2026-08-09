# REGDOCS Atlas release notes

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
