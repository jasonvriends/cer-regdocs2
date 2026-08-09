# REGDOCS Atlas 0.0.1

**Status: proof of concept.** This is the only project version until an explicit version change is requested. Internal implementation work does not advance the version.

## Current POC baseline

REGDOCS Atlas provides one root CLI (`python pipeline.py ...`) and one application package (`regdocs_atlas/`) for the complete acquisition-to-search workflow.

The public CLI is action-oriented for safety: naming a stage never performs work. Scout, Download, Azure analysis, Docling analysis, Normalize, and Index all require an explicit action before network or mutating work can begin. Examples include `scout run`, `download run`, `analyze azure run`, `analyze docling run`, `normalize run`, and `index publish`. Safe planning/status actions are separate. Azure Content Understanding work additionally requires an explicit candidate scope (`--all`, `--limit`, or `--document-id`).

`SYNTAX.md` is the authoritative public command/switch guide. It is written for operators who do not need Python knowledge, with stage-by-stage safety notes, copy/paste examples, and complete public switch tables. `README.md` remains the architectural and POC operating overview.

The pipeline baseline includes:

- Scout preserving REGDOCS metadata, raw HTML evidence, recovery manifests, and a durable date-range coverage watermark.
- Download preserving current source files, SHA-256 identities, and deterministic metadata sidecars.
- Azure Content Understanding and local Docling analysis with single-document child-process isolation.
- Azure analysis preserving provider-native JSON/Markdown artifacts and supporting large PDF Content-Range processing.
- Normalize locally producing deterministic documents/pages/chunks/tables/provenance JSONL from Stage 3 artifacts.
- Normalize supporting bounded isolated-worker concurrency with `--concurrency N` while retaining a safe default of `1`, deterministic final document order, and sequential `--stop-on-error` semantics.
- Normalize streaming successful worker shards into canonical JSONL instead of loading whole shard files into memory, and recording selection, worker, merge, and total wall-clock timing in the run summary.
- Index validating/publishing normalized chunks to Azure AI Search.
- SQLite providing the operational ledger, named migrations, backups, integrity checks, run state, errors, analysis state, and normalization state.
- A global pipeline lock and canonical `workspace/pipeline.log` coordinating normal root-CLI operation while stage locks remain defense-in-depth.
- A root-console presentation layer that gives Scout, Download, Azure, Docling, Normalize, and Index the same line-oriented operator style; transient `tqdm` redraws and timestamped logger prefixes are removed from the terminal while the raw child output remains preserved in `workspace/pipeline.log`.
- Azure Content Understanding usage/cost inspection from saved result usage with configurable rates; Docling remains local compute.

## Durable recovery boundary

The POC treats Stages 1-3 as durable because they contain source evidence, source bytes, or expensive analysis outputs.

Durable recovery artifacts include:

- Scout document/snapshot manifests plus raw HTML;
- Scout coverage metadata independent of historical SQLite run rows;
- downloaded files plus Stage 2 sidecars;
- successful Stage 3 analysis manifests plus Azure/Docling provider artifacts.

The recovery workflow can build a new SQLite database beside the working database and compare source/Stage-3 identities before any destructive test.

The current corpus recovery proof reached exact equivalence through Stage 3 for:

- 16,823 document identities;
- 7,958 current source-file identities;
- 22,086 Scout snapshot identities;
- 15,114 container relationships; and
- 7,958 successful Stage 3 analysis identities.

A flat rebuild mode is available with `python pipeline.py rebuild create --flat`. It first performs the verified manifest-backed Stage 1-3 reconstruction, then removes historical runs, errors, rebuild/recovery bookkeeping, and recovery-only document state to produce a clean operational baseline. It does not contact external services and never overwrites the active database.

Stage 4 normalization is intentionally rerun locally from preserved Stage 3 artifacts rather than adding another recovery-manifest layer. Azure AI Search is republished from Stage 4.

## Repository policy

The repository intentionally has:

- no `pipeline/` compatibility implementation tree;
- no duplicate `docs/` or `roadmap/` documentation trees;
- no GitHub Actions CI workflow;
- no dedicated automated test suite for the POC;
- one root `requirements.txt`;
- two intentionally distinct documentation files: `README.md` for architecture/operating rules and `SYNTAX.md` for the public operator guide;
- one consolidated `RELEASE_NOTES.md` baseline rather than an internal change diary;
- one version (`0.0.1`) until explicitly changed.

`workspace/` and `database/` are Git-ignored local state. They must not be deleted as part of repository cleanup, especially `workspace/3_analyze/`, because reproducing Azure analysis can be expensive.
