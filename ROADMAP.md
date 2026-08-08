# REGDOCS Atlas Roadmap

This file tracks future product direction, priorities, decisions, and open
questions. It is intentionally not an operations manual.

For current behavior and commands, use:

- [README.md](README.md) for repository orientation and quick start;
- [Stage 1 — Scout](pipeline/regdocs_1_scout.md);
- [Stage 2 — Download](pipeline/regdocs_2_download.md);
- [Stage 3 — Analyze](pipeline/regdocs_3_analyze.md); and
- [Stage 4 — Normalize](pipeline/regdocs_4_normalize.md).

If this roadmap disagrees with implemented behavior, the stage documentation
and code are authoritative.

## North star

REGDOCS Atlas is a modern regulatory research workbench built on the Canada
Energy Regulator's public REGDOCS record.

The objective is not another generic “chat with your documents” interface. The
objective is to make public regulatory evidence easier to:

- acquire and verify;
- organize into projects, filings, dossiers, and relationships;
- search and inspect with visible provenance;
- compare and assemble into research products; and
- use safely in evidence-grounded AI workflows.

The near-term goal is an internal proof of value strong enough to answer:

> What would it take to make this a funded product?

## Product principles

1. **Evidence first.** Material claims should resolve to a document, source
   version, page or source region where possible, and authoritative REGDOCS URL.
2. **Research before chat.** Browsing, search, filters, dossiers, timelines,
   comparison, and evidence selection are primary. Conversational AI is
   contextual and secondary.
3. **Preserve regulatory structure.** Do not flatten projects, filings,
   containers, chronology, document types, and source relationships into an
   undifferentiated chunk collection.
4. **Keep outputs portable.** The local pipeline remains independently
   reproducible and auditable. Downstream research and indexing work consumes
   versioned artifacts without replacing the acquisition source of truth.
5. **Prototype for value.** A polished experience over a carefully selected
   real corpus is more persuasive than premature full-corpus scale.
6. **Be honest about completeness.** Missing files, omitted source structures,
   partial runs, model limits, and uncertain evidence must remain visible.

## Current baseline

The repository now contains a runnable four-stage local pipeline:

```text
1. Scout      REGDOCS metadata and raw HTML evidence
2. Download   validated, hashed, versioned source files
3. Analyze    Azure Content Understanding JSON and Markdown
4. Normalize  deterministic documents/pages/chunks/tables/provenance JSONL
```

The pipeline has:

- one shared SQLite ledger under `database/`;
- persistent stage artifacts under `workspace/`;
- version-controlled scripts and runbooks under `pipeline/`;
- repository-relative stored paths;
- pinned direct Python dependencies;
- automatic resumable `Content-Range` analysis for PDFs over 300 pages while
  preserving one Stage 2 source identity; and
- offline self-tests for Stages 1 and 2.

This baseline proves the acquisition and transformation path. It is not yet an
unattended production system or a complete, validated research corpus.

## Roadmap horizons

The horizons below describe outcomes and validation gates, not a delivery
stack. They assume a local workbench unless a later decision establishes a
different operating model.

### Now — Make the pipeline safe and repeatable

**Outcome:** bounded runs can be operated and recovered without avoidable
security, billing, concurrency, or corpus-integrity risk.

Focus:

- enforce trusted HTTPS destinations and validate redirects in Stages 1 and 2;
- stop persisting unnecessary cookies/headers and use explicit private file
  permissions;
- centralize schema migrations, configuration, locking, and stale-run recovery;
- make Stage 2 file promotion and ledger updates crash-recoverable;
- require bounded Stage 3 selection or explicit full-run acknowledgement;
- preflight Stage 3 page/byte volume and projected cost before billable work;
- retain provider usage and per-document/per-run cost metadata;
- persist accepted Azure operation IDs and preserve attempts append-only;
- make inspection and no-op modes genuinely read-only where promised;
- write Stage 4 as atomic, manifested generations;
- prevent filtered normalization from replacing the canonical corpus by
  default; and
- add fixture, regression, concurrency, and fault-injection tests, including
  large-PDF range-boundary and restart cases.

Exit condition:

- interruption and restart paths are tested;
- concurrent writers cannot duplicate billable work or corrupt artifacts;
- ledger state and committed artifacts cannot describe different generations;
  and
- integrity and security audits pass before larger runs.

### Next — Establish a validated reference corpus

**Outcome:** a selected real-world corpus can be rebuilt deterministically from
preserved evidence and evaluated for completeness.

Focus:

- choose representative projects or proceedings;
- define explicit selection and completeness criteria;
- version normalized JSONL contracts and generation manifests;
- build the corpus reproducibly through all four stages;
- measure missing files, failed analyses, omitted source structures, and
  provenance coverage;
- produce corpus-health and integrity reports; and
- validate stable identities and repeatable output.

Exit condition:

- unchanged inputs produce logically or byte-identical normalized output;
- every normalized record resolves to preserved source evidence; and
- known gaps are measured and visible rather than silently omitted.

### Then — Add local indexing and discovery

**Outcome:** the validated corpus is searchable locally without AI or external
services.

Focus:

- build rebuildable, versioned metadata and full-text indexes;
- support facets, snippets, highlighting, and page/chunk source locations;
- refresh indexes incrementally from manifested normalized generations;
- make corpus completeness, index health, and provenance inspectable; and
- create representative discovery queries and a keyword relevance baseline.

Exit condition:

- a clean environment can rebuild the indexes from versioned artifacts;
- representative queries return source-locatable evidence; and
- index state resolves to one normalized generation.

### After indexing — Validate research workflows

**Outcome:** core regulatory research tasks work independently of any chosen
interface.

Focus:

- build project, proceeding, and filing dossiers;
- expose Folder and Compound Document structure;
- support timelines and document/source-version inspection;
- support bounded evidence selection and passage or document comparison;
- preserve portable saved research state; and
- use the simplest useful local surface, such as a CLI, notebook, or local
  tool, while validating the workflows.

Exit condition:

- a researcher can find a proceeding, understand its structure and chronology,
  select evidence, inspect exact sources, and reproduce the research state
  without AI.

### Later — Measure and improve retrieval

**Outcome:** retrieval quality is measured before semantic complexity is
added.

Sequence:

1. create representative relevance judgments and metrics;
2. establish a metadata, filter, and full-text baseline;
3. diagnose normalization and indexing gaps exposed by that baseline;
4. test semantic retrieval only for demonstrated gaps; and
5. adopt hybrid ranking only if it measurably improves the baseline while
   retaining metadata and provenance constraints.

Exit condition:

- ranking results are reproducible and evaluated; and
- returned evidence resolves to exact pages or source regions.

### After retrieval — Add bounded, cited AI workflows

**Outcome:** bounded evidence produces useful research outputs with verifiable
support.

Sequence:

1. summarize one document, folder, or filing;
2. compare user-selected evidence;
3. generate a cited chronology or issue brief;
4. extract commitments, positions, and concerns; and
5. add contextual question answering only after citation reliability is
   demonstrated.

Provider credentials must remain outside research artifacts and generated
output. Each material claim must be cited or explicitly qualified, citations
must resolve locally and to authoritative REGDOCS evidence, and insufficient
evidence must produce an explicit non-answer.

### Backlog — Advanced capabilities

- saved evidence collections;
- relationship and cited-reference graphs;
- document-version comparison and change detection;
- entity and place exploration;
- related-document recommendations;
- portable brief export;
- bilingual research workflows;
- alerts and source monitoring; and
- local annotations if a real need emerges.

These items must not delay the safe, validated, searchable corpus.

## Prototype scope

A useful first prototype corpus may be approximately:

```text
1–3 projects or proceedings
20–100 filings
1,000–5,000 documents
```

Selection should favor meaningful history, multiple filing/document types,
container structure, chronology, and enough analyzed content to evaluate search
and evidence workflows. Full historical ingestion is not a prerequisite.

## Demo narrative

The internal demonstration should lead with user value:

1. introduce a real proceeding and corpus health;
2. search for a meaningful regulatory issue;
3. open the filing dossier and chronology;
4. inspect a document and its source provenance;
5. select a bounded evidence set;
6. build a cited brief or comparison; and
7. open a citation back to the original evidence.

Pipeline detail supports the story but should not lead it.

## Non-goals for the prototype

- a full historical REGDOCS mirror;
- production service levels or enterprise orchestration;
- a replacement for authoritative REGDOCS records;
- generic chat, autonomous agents, or automatic legal conclusions;
- production records-management workflows; and
- solving every production security, privacy, legal, retention, accessibility,
  bilingual, governance, and operating-model question before proof of value.

Those concerns become funded-project requirements if the prototype succeeds.

## Success measures

The prototype succeeds when reviewers can see that:

- REGDOCS evidence can be acquired, refreshed, versioned, and audited;
- regulatory structure improves discovery over a flat document list;
- dossiers and timelines reveal useful context;
- search finds relevant material with inspectable source locations;
- bounded AI produces useful research outputs with verifiable citations; and
- the local workbench supports repeatable research without weakening its
  provenance foundation.

## Immediate next actions

1. Close the Stage 1–4 hardening items that block safe unattended or full
   runs.
2. Add representative fixtures and test interruption, recovery, concurrency,
   parser drift, range boundaries, and artifact publication.
3. Select the reference project/proceeding corpus and define completeness.
4. Freeze the first normalized-artifact and generation-manifest contract.
5. Rebuild the reference corpus and produce a corpus-health report.
6. Create representative discovery queries and relevance judgments.
7. Establish a local metadata/full-text retrieval baseline against them.
8. Validate dossier, timeline, inspection, evidence-selection, and comparison
   workflows on the reference corpus.

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-08-07 | Keep SQLite as the local pipeline source of truth | Portable and auditable |
| 2026-08-07 | Make AI contextual and evidence-first | Improves traceability and differentiates the research experience |
| 2026-08-07 | Use a curated prototype corpus | Optimizes for proof of value rather than premature scale |
| 2026-08-08 | Use four explicit local pipeline stages | Separates discovery, source acquisition, external analysis, and deterministic normalization |
| 2026-08-08 | Separate code, ledger, and operational artifacts | Makes persistence and ownership visible in `pipeline/`, `database/`, and `workspace/` |
| 2026-08-08 | Keep current operations out of the roadmap | README and adjacent stage runbooks remain the implementation documentation |
| 2026-08-08 | Organize the roadmap by outcome gates | Keeps future work independent of abandoned hosting and database choices |
| 2026-08-08 | Analyze PDFs over 300 pages with Azure `Content-Range` rather than splitting source PDFs | Preserves one source SHA/provenance identity while making large analyses resumable |

## Open questions

- Which project or proceeding should anchor the first internal demonstration?
- What is the minimum complete, versioned normalized-corpus contract?
- What normalization coverage threshold is acceptable before indexing?
- Which local discovery surface is useful first: CLI, notebook, or another
  local tool?
- What retrieval evaluation set and success metrics should gate hybrid search?
- What citation accuracy and completeness should gate generated briefs?
- How much bilingual and accessibility functionality is required for the first
  demonstration?
- Who is the first internal audience, and which workflow matters most to them?
- Which operational, security, governance, accessibility, and bilingual
  requirements gate broader internal adoption?

## Scope guardrail

Before adding work, ask:

1. Does it improve the internal demonstration?
2. Does it make the corpus easier to understand?
3. Does it strengthen provenance, safety, or trust?
4. Does it unlock the next roadmap outcome?
5. Is it required for the current outcome's definition of done?

If none apply, defer it.
