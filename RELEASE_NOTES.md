# REGDOCS Atlas 0.0.1

**Status: proof of concept.** This is the only project version until an explicit version change is requested. Internal implementation work does not advance the version.

## Current POC baseline

REGDOCS Atlas provides one root CLI (`python pipeline.py ...`) and one application package (`regdocs_atlas/`) for the complete acquisition-to-search workflow:

- Scout preserves REGDOCS metadata and raw HTML evidence.
- Download preserves current source files, SHA-256 identities, and deterministic metadata sidecars.
- Analyze supports Azure Content Understanding and local Docling with single-document child-process isolation.
- Azure analysis preserves provider-native JSON/Markdown artifacts and supports large PDF Content-Range processing.
- Normalize locally produces deterministic documents/pages/chunks/tables/provenance JSONL from Stage 3 artifacts.
- Index publishes normalized chunks to Azure AI Search.
- SQLite provides the operational ledger, named migrations, backups, integrity checks, run state, errors, analysis state, and normalization state.
- A global pipeline lock and canonical `workspace/pipeline.log` coordinate normal root-CLI operation while existing stage locks remain defense-in-depth inside the current stage implementations.
- Azure Content Understanding usage/cost can be inspected from saved result usage with configurable rates; Docling is local compute.

## Durable recovery boundary

The POC treats Stages 1-3 as durable because they contain source evidence, source bytes, or expensive analysis outputs.

Durable recovery artifacts include:

- Scout document/snapshot manifests plus raw HTML;
- downloaded files plus Stage 2 sidecars;
- successful Stage 3 analysis manifests plus Azure/Docling provider artifacts.

The recovery workflow can build a new SQLite database beside the working database and compare source/Stage-3 identities before any destructive test.

The current corpus recovery proof reached exact equivalence through Stage 3 for:

- 16,823 document identities;
- 7,958 current source-file identities;
- 22,086 Scout snapshot identities;
- 15,114 container relationships; and
- 7,958 successful Stage 3 analysis identities.

Stage 4 normalization is intentionally rerun locally from preserved Stage 3 artifacts rather than adding another recovery-manifest layer. Azure AI Search is republished from Stage 4.

## Repository policy

The repository intentionally has:

- no `pipeline/` compatibility implementation tree;
- no duplicate `docs/` or `roadmap/` documentation trees;
- no GitHub Actions CI workflow;
- no dedicated automated test suite for the POC;
- one root `requirements.txt`;
- one documentation source (`README.md`);
- one version (`0.0.1`) until explicitly changed.

`workspace/` and `database/` are Git-ignored local state. They must not be deleted as part of repository cleanup, especially `workspace/3_analyze/`, because reproducing Azure analysis can be expensive.
