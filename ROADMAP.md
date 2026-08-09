# REGDOCS Atlas Roadmap

This file tracks future product direction, priorities, decisions, and open
questions. It is intentionally not an operations manual.

For current behavior and commands, use:

- [README.md](README.md) for repository orientation and quick start;
- [Stage 1 — Scout](pipeline/regdocs_1_scout.md);
- [Stage 2 — Download](pipeline/regdocs_2_download.md);
- [Stage 3 — Azure](pipeline/regdocs_3_azure.md);
- [Stage 3 — Docling](pipeline/regdocs_3_docling.md);
- [Stage 4 — Normalize](pipeline/regdocs_4_normalize.md); and
- [Stage 5 — Index](pipeline/regdocs_5_index.md).

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

The repository now contains a runnable five-stage pipeline with two Stage 3
analysis backends:

```text
1. Scout      REGDOCS metadata and raw HTML evidence
2. Download   validated, hashed, versioned source files
3a. Analyze   Azure Content Understanding JSON and Markdown
3b. Analyze   local Docling native JSON plus REGDOCS compatibility projection
4. Normalize  deterministic documents/pages/chunks/tables/provenance JSONL
5. Index      Azure AI Search full-text/filter/facet chunk index
```

The pipeline has:

- one shared SQLite ledger for Stages 1–4 under `database/`;
- persistent stage artifacts under `workspace/`;
- version-controlled scripts and runbooks under `pipeline/`;
- repository-relative stored paths;
- pinned direct Python dependencies;
- automatic resumable `Content-Range` analysis for PDFs over 300 pages while
  preserving one Stage 2 source identity;
- an experimental local Docling Stage 3 backend that preserves native Docling
  output and can feed the current normalizer through a compatibility projection;
- a single-threaded Docling supervisor that isolates each document in a separate
  child process so crashes do not terminate the overall corpus run;
- Stage 4 provenance that keeps original page/polygon geometry and globally
  qualifies Azure element pointers across multiple `contents[]` entries;
- Stage 5 validation that joins each indexed chunk back to its matching
  provenance record before publication;
- an initial Azure AI Search schema for keyword search, filters, facets, page
  identity, source URLs, and compact qualified element paths; and
- offline self-tests for Stages 1 and 2.

Azure AI Search is currently a rebuildable derivative publication target, not a
replacement for the Stage 1–4 source pipeline. Stage 5.0 intentionally does not
yet add semantic ranking, vectors, or LLM answer generation.

This baseline proves the acquisition, transformation, and first retrieval path.
It is not yet an unattended production system or a complete, validated research
corpus.

## Roadmap horizons

The horizons below describe outcomes and validation gates, not a delivery
stack. They assume the preserved local corpus remains independently auditable
even when Azure services are used for analysis or search.

### Now — Make the pipeline safe and repeatable

**Outcome:** bounded runs can be operated and recovered without avoidable
security, billing, concurrency, corpus-integrity, or publication risk.

Focus:

- enforce trusted HTTPS destinations and validate redirects in Stages 1 and 2;
- stop persisting unnecessary cookies/headers and use explicit private file
  permissions;
- centralize schema migrations, configuration, locking, and stale-run recovery;
- make Stage 2 file promotion and ledger updates crash-recoverable;
- require bounded Stage 3 selection or explicit full-run acknowledgement;
- preflight Stage 3 page/byte volume and projected cost before billable work;
- capture Azure Content Understanding `usage` for every accepted analysis and
  range, including `documentPagesMinimal`, `documentPagesBasic`, and
  `documentPagesStandard`; persist those counters per document and per run,
  aggregate them in Stage 3 summaries, and expose an explicitly labeled cost
  estimate from configurable meter prices so operators do not need to rely on
  delayed Azure Cost Management totals;
- persist accepted Azure operation IDs and preserve attempts append-only;
- keep the Docling corpus runner strictly single-threaded while isolating each
  document in a child process, persisting supervisor state, and quarantining
  repeated crash/failure cases so one malformed document cannot stall a run;
- make inspection and no-op modes genuinely read-only where promised;
- write Stage 4 as atomic, manifested generations;
- prevent filtered normalization from replacing the canonical corpus by
  default;
- make Stage 5 publication resolve to an explicit normalized generation;
- replace destructive Stage 5 rebuilds with versioned search indexes plus an
  alias/pointer switch;
- add Stage 5 incremental deletion/change detection after generation identity
  exists; and
- add fixture, regression, concurrency, and fault-injection tests, including
  large-PDF range-boundary, restart, qualified-provenance, Docling child-process
  crash/restart/quarantine, and search-publication cases.

#### SQLite ledger rebuild and disaster recovery

Treat `database/regdocs.db` as a durable operational ledger that is backed up for
fast recovery, but not as the only copy of corpus truth. Loss of the SQLite file
must not require re-downloading preserved source files or rerunning expensive
Azure Content Understanding or Docling analysis when their artifacts still
exist.

Classify persisted data by recovery value:

```text
Preserve as durable corpus evidence/artifacts
  workspace/1_scout/raw/                  REGDOCS source evidence
  workspace/2_download/files/             downloaded source bytes + versions
  workspace/3_analyze/content-understanding/raw/
                                           expensive Azure native analysis
  workspace/3_analyze/docling/             expensive local analysis
  manifested workspace/4_normalize/        deterministic normalized generations

Back up for fast operational recovery
  database/regdocs.db                      SQLite ledger

Safe to recreate or expire when not needed
  locks, partial files, transient job state, temporary conversion material,
  publication scratch, and Azure AI Search indexes
```

Add a future rebuild utility, conceptually `pipeline/regdocs_db_rebuild.py`, with
safe read-only audit and explicit rebuild modes. It should reconstruct a new
ledger from preserved artifacts rather than modifying the only surviving DB in
place. The rebuild sequence should:

1. create the current schema through normal migrations;
2. reconstruct Stage 1 document identities, relationships, source URLs, and raw
   snapshot rows from preserved Stage 1 evidence/manifests;
3. scan Stage 2 current and historical source files, verify SHA-256 values, and
   reconstruct file/version rows without using filesystem mtimes as authority;
4. scan Azure and Docling Stage 3 native artifacts and artifact-side metadata to
   reconstruct successful analysis rows without making analyzer calls;
5. reconstruct Stage 4 normalization rows from generation manifests and output
   hashes;
6. verify document ID, source-file SHA, analyzer identity/version, artifact path,
   page/count metadata, and normalized-generation consistency across layers; and
7. optionally republish Stage 5 only after the rebuilt Stages 1–4 ledger passes
   integrity checks.

Make durable artifacts self-describing enough that reconstruction does not depend
on SQLite-only facts. Where a required field currently exists only in the ledger,
add a small artifact-side manifest or sidecar rather than duplicating large
payloads. Stage 3 manifests are especially important: a surviving Azure raw JSON
must be sufficient to prove which document/file SHA, analyzer, API version,
range/part identity, and output paths it represents.

Historical `runs` and `errors` may be only partially reconstructable. Preserve
normal SQLite backups for that operational history, but distinguish it from the
minimum corpus state required to resume processing. A rebuild must prioritize
current document/file identities, preserved source evidence, expensive Stage 3
results, normalization generations, and their provenance.

Target commands are conceptually:

```text
python pipeline/regdocs_db_rebuild.py --verify-rebuildable
python pipeline/regdocs_db_rebuild.py --rebuild-new-db database/regdocs.rebuilt.db
python pipeline/regdocs_db_rebuild.py --compare-ledger database/regdocs.db database/regdocs.rebuilt.db
```

The rebuild path must refuse to overwrite the active DB by default. Add a
fault-injection test that copies the durable artifacts into a clean environment,
removes the SQLite ledger, rebuilds it, and proves that the same current source
file hashes and successful Azure/Docling analyses are recovered and that Stage 4
and Stage 5 can continue without network re-acquisition or billable re-analysis.

Exit criteria for ledger recovery:

- a rebuildability audit identifies any SQLite-only corpus facts before disaster;
- deleting a test copy of `regdocs.db` does not require rerunning Stage 3;
- rebuilt current document, file-version, analysis, and normalization identities
  match the preserved artifact set;
- corruption or ambiguous artifact identities fail closed rather than guessing;
  and
- normal DB backups remain the fast recovery path while artifact reconstruction
  is a tested disaster-recovery path.

Exit condition:

- interruption and restart paths are tested;
- concurrent writers cannot duplicate billable work or corrupt artifacts;
- ledger state and committed artifacts cannot describe different generations;
- a published search index can be traced to one normalized generation; and
- integrity and security audits pass before larger unattended runs.

### Next — Establish a validated reference corpus

**Outcome:** a selected real-world corpus can be rebuilt deterministically from
preserved evidence, published to search, and evaluated for completeness.

Focus:

- choose representative projects or proceedings;
- define explicit selection and completeness criteria;
- version normalized JSONL contracts and generation manifests;
- build the corpus reproducibly through Stages 1–4;
- publish the validated snapshot through Stage 5;
- measure missing files, failed analyses, omitted source structures, and
  provenance coverage;
- produce corpus-health and integrity reports; and
- validate stable identities and repeatable output.

#### Multi-provider document analysis and canonical selection

Use the reference corpus to evaluate document analysis as a replaceable,
measurable Stage 3 capability rather than permanently coupling normalization to
one provider.

The target architecture is:

```text
                         +--> Azure Content Understanding --+
Stage 2 source document -+                                  +--> comparison/evaluation
                         +--> Docling standard/VLM ----------+
                                                                  |
                                                                  v
                                                        canonical selection
                                                                  |
                         +--> Azure adapter -----------------------+
                         |                                        |
                         +--> Docling adapter ---------------------+--> common REGDOCS analysis model
                                                                  |
                                                                  v
                                                             Stage 4 normalize
                                                                  |
                                                                  v
                                                             mixed-provider corpus
```

Preserve every analyzer's native output. Do not make Docling permanently mimic
Azure or make Azure's response schema the long-term normalized input contract.
Provider-specific adapters should project native analyzer results into a common
REGDOCS intermediate analysis model containing the concepts Stage 4 actually
needs: pages, text blocks, sections, tables, figures, geometry, reading order,
Markdown, and exact provider provenance.

Build a permanent comparison harness that can run two or more analyzers against
the exact same Stage 2 file identity and report both structural and downstream
quality measures. At minimum compare:

- success/failure and crash behavior;
- pages and text recovered;
- page-level text similarity and divergence;
- headings, sections, reading order, headers, and footers;
- tables, dimensions, cells, and table text fidelity;
- figures and other structured elements where available;
- geometry/provenance coverage;
- processing time and resource use;
- Azure billable usage/cost versus local Docling compute cost; and
- the resulting Stage 4 page/chunk/table/provenance records and retrieval quality.

Comparison results must be auditable rather than silently choosing a winner.
Store machine-readable per-document metrics and a human-readable report that
highlights divergent pages and structures for inspection.

Add an explicit canonical-analysis selection table rather than inferring the
winner from artifact existence or timestamps. A target contract is conceptually:

```text
analysis_selections
  document_id
  file_sha256
  purpose              # canonical, experiment, etc.
  analysis_id
  provider              # azure, docling, ...
  analyzer_id
  analyzer_version
  selection_method      # manual, rule, benchmark
  reason
  selected_at
```

Stage 4 should ultimately support:

```text
--analysis-provider azure
--analysis-provider docling
--analysis-provider selected
```

`selected` means that each current source document is normalized from its
explicitly designated canonical Stage 3 analysis. One normalized generation may
therefore legitimately contain Azure-derived documents beside Docling-derived
documents. Stage 5 must not need to know which analyzer produced a document;
provider identity and exact source-analysis provenance remain carried in the
normalized records.

Do not add automatic `best` selection until the comparison corpus has produced
credible metrics and failure cases. Initial canonical choices may be manual.
Later policy may route classes of documents differently, for example born-digital
PDFs to Docling and difficult scans or layouts to Azure, with fallback behavior
only when the routing rules are measurable and auditable.

Exit criteria for the multi-provider work:

- the same Stage 2 file can be analyzed independently by Azure and Docling
  without overwriting either result;
- native analyzer outputs remain preserved and inspectable;
- provider adapters expose one versioned REGDOCS analysis contract;
- comparison reports identify meaningful content/structure divergence;
- canonical selections are explicit, versioned, and explainable;
- Stage 4 can build a deterministic mixed-provider generation from those
  selections; and
- retrieval evaluation determines whether provider choice materially changes
  research quality, rather than relying only on raw extraction counts.

Exit condition:

- unchanged inputs produce logically or byte-identical normalized output;
- every normalized record resolves to preserved source evidence;
- every published search result resolves back to the normalized corpus; and
- known gaps are measured and visible rather than silently omitted.

### Then — Establish and measure the retrieval baseline

**Outcome:** the validated corpus is usefully searchable before semantic or LLM
complexity is added.

Focus:

- exercise the Stage 5 Azure AI Search keyword index with real regulatory
  queries;
- support facets, filters, snippets, highlighting, and page/chunk source
  locations in the eventual user-facing layer;
- create representative discovery queries and relevance judgments;
- measure keyword/full-text ranking quality;
- diagnose normalization or metadata gaps exposed by retrieval; and
- make corpus completeness, index health, and provenance inspectable.

Exit condition:

- a clean environment can rebuild the search index from versioned artifacts;
- representative queries return source-locatable evidence;
- retrieval metrics and failure cases are recorded; and
- index state resolves to one normalized generation.

### After the baseline — Improve retrieval only when measured

**Outcome:** semantic complexity is adopted only where it improves the measured
baseline.

Sequence:

1. test Azure semantic ranking against the keyword baseline;
2. diagnose wins and regressions by query type;
3. add embeddings and vector fields only for demonstrated semantic-recall gaps;
4. test hybrid keyword + vector ranking;
5. retain project/document/date/type filters in hybrid retrieval; and
6. adopt the simplest retrieval configuration that measurably improves the
   evaluation set while preserving source identity.

Exit condition:

- ranking results are reproducible and evaluated; and
- returned evidence resolves to exact pages or source regions.

### After retrieval — Validate research workflows

**Outcome:** core regulatory research tasks work independently of any chosen
chat interface.

Focus:

- build project, proceeding, and filing dossiers;
- expose Folder and Compound Document structure;
- support timelines and document/source-version inspection;
- support bounded evidence selection and passage or document comparison;
- preserve portable saved research state; and
- use the simplest useful interface while validating the workflows.

The interface may start with Azure Search Explorer, the Stage 5 CLI, a notebook,
or a lightweight web application. The research workflow should determine the
UI rather than the reverse.

#### Phase 1 Atlas web application direction

Build the first user-facing Atlas surface as a thin Azure-hosted research
workbench rather than adopting a generic chatbot shell.

Target Phase 1 architecture:

```text
Browser
   |
   v
Azure App Service
Next.js + TypeScript
   |
   +--> Azure AI Search       search, filters, ranked evidence
   +--> Azure Blob Storage    original source files / viewer assets
   +--> Stage 4 provenance    page, chunk, region, polygon resolution
   +--> Copilot Studio        first conversational proof / chat engine
```

Keep an Atlas-owned application boundary around every external service. The UI
should call Atlas server routes for search, document resolution, provenance, and
chat integration rather than encoding vendor-specific contracts throughout the
React components. Copilot Studio is the first conversational implementation to
test and may remain the long-term engine if it proves sufficient, but the Atlas
chat boundary must remain replaceable by a custom Foundry/assistant-ui path
without changing search result identity, citation URLs, document routes, or the
evidence viewer.

Phase 1 should deliberately have no application login, user database, saved
conversations, or per-user research state. Keep the application boundary ready
for Microsoft Entra ID SSO later, when identity-backed features such as saved
searches, research projects, evidence baskets, annotations, conversation
history, sharing, and alerts provide a reason to authenticate users.

Use one full-stack Next.js/TypeScript deployment on Azure App Service where
practical. Prefer server-side route handlers for search, document, provenance,
and conversational endpoints rather than introducing a separate FastAPI service
before a separate backend is operationally justified. Use managed identities
for service-to-service Azure access wherever supported instead of application
secrets or search keys.

Candidate Phase 1 UI stack:

```text
Next.js + TypeScript
   +--> shadcn/ui                 application shell and controls
   +--> Extend UI                first document-viewer/citation candidate
   |      \--> PDF.js fallback   if lower-level rendering/overlay control is needed
   +--> Copilot Studio           first Ask/chat implementation
          \--> custom Foundry + assistant-ui fallback/later option
```

Do not let a component-library choice become a durable data contract. The
viewer must consume Atlas document/page/provenance identities, and the chat
surface must consume Atlas citations, regardless of which React or Microsoft
component implements the presentation.

##### Front page and primary workspace

The front page should look like a regulatory research product, not a blank chat
window. Make one prominent entry point capable of both conventional search and
natural-language questions, with a clear `Search` / `Ask` mode rather than
hiding the distinction between retrieval and generation.

A Phase 1 front-page concept is:

```text
REGDOCS ATLAS

Search and investigate Canada Energy Regulator records

[ Search | Ask ]
[ caribou habitat, pipeline conditions, filing number, company...       ]

[Project] [Company] [Date] [Document type]                  Corpus status

Explore
- representative projects / proceedings
- recent or notable filings where useful
- example research questions
```

With no login in Phase 1, avoid fake personalization, empty saved-work areas, or
conversation-history chrome. Use that space for corpus scope/health, example
queries, project/proceeding entry points, and a concise explanation that every
result can be traced back to source evidence.

After a search or question, transition into the main research workspace rather
than replacing the page with a chat transcript. The preferred desktop layout is
conceptually:

```text
+----------------------+--------------------------------+----------------------+
| SEARCH / DOSSIER     | DOCUMENT / EVIDENCE            | ASK / RESEARCH       |
|                      |                                |                      |
| query + facets       | original PDF/file             | question             |
| ranked results       | exact page                    | grounded answer      |
| filing structure     | highlighted source region     | cited sources        |
| chronology/context   | metadata / original link      | follow-up questions  |
+----------------------+--------------------------------+----------------------+
```

The panels should be resizable/collapsible so the same shell works for pure
search, document reading, and conversational research. On smaller screens the
same areas may become tabs or a drill-down sequence rather than three fixed
columns.

The Phase 1 interaction loop is:

1. search or ask a regulatory question;
2. retrieve ranked Atlas chunks with metadata and page identity;
3. show conventional search results and/or a grounded answer;
4. open the exact original document page; and
5. resolve the selected result or citation through Stage 4 provenance to visibly
   highlight the supporting source region.

The document/evidence surface should render original PDFs/files rather than
reconstructing an HTML facsimile in the first prototype. Overlay normalized
page/polygon provenance on the rendered source page. Keep a normalized clean
text/table view as a possible later complementary view, not the evidentiary
source of truth.

Use Blob Storage primarily for source-document delivery and provenance-backed
viewer assets. Azure AI Search remains the knowledge/retrieval surface. Do not
create a second independently chunked Copilot knowledge corpus from the blobs
unless it materially outperforms or simplifies the existing Stage 4 -> Stage 5
retrieval path.

##### Copilot Studio first, replaceable conversational architecture

Run Copilot Studio first as the conversational proof before writing a custom
Foundry chat stack. Connect a pilot Atlas Azure AI Search index as Copilot
knowledge and test citation URL behavior, query quality, retrieval control,
embedding in the Atlas application, and whether returned citations retain
enough Atlas identity to open the exact document/page/region.

The desired citation path is:

```text
Copilot answer citation
        |
        v
Atlas citation URL containing stable chunk/document identity
        |
        v
Atlas provenance resolver
        |
        +--> Stage 4 chunk/page/polygon provenance
        +--> original source file in Blob Storage
        |
        v
open exact page and highlight supporting region
```

Copilot Studio should not become the owner of Atlas document identity,
chunking, or provenance. If Copilot Studio proves strong enough, retain it as
the conversational engine behind the Atlas chat boundary. If it does not provide
sufficient retrieval control, citation fidelity, UX control, or cost/operating
fit, replace that layer with a custom Foundry implementation and a React chat
surface such as assistant-ui while preserving the rest of the application.

Exit criteria for the Phase 1 application:

- keyword/filter search works against the published Atlas index;
- the front page supports an obvious Search/Ask entry into the research
  workspace without presenting Atlas as a generic chatbot;
- search results open source documents at the correct page;
- Stage 4 provenance can visibly highlight exact supporting regions;
- the application can run as one Azure-hosted deployment without user login;
- Copilot Studio returns grounded answers with clickable Atlas citations;
- the Copilot Studio proof establishes whether it is suitable as the longer-term
  conversational engine;
- a documented Atlas chat boundary permits a later custom Foundry/assistant-ui
  implementation without changing evidence contracts; and
- the application architecture can add Entra ID later without redesigning the
  retrieval or evidence contracts.

Exit condition:

- a researcher can find a proceeding, understand its structure and chronology,
  select evidence, inspect exact sources, and reproduce the research state
  without relying on an LLM.

### After retrieval quality — Add bounded, cited AI workflows

**Outcome:** bounded evidence produces useful research outputs with verifiable
support.

Sequence:

1. summarize one document, folder, or filing from retrieved evidence;
2. compare user-selected evidence;
3. generate a cited chronology or issue brief;
4. extract commitments, positions, concerns, organizations, and legal
   references; and
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
- the workbench supports repeatable research without weakening its provenance
  foundation.

## Immediate next actions

1. Finish and verify the final Stage 4 corpus generation.
2. Run Stage 5 local validation and publish the first `regdocs-chunks` index.
3. Create representative discovery queries and relevance judgments.
4. Measure the Azure AI Search keyword/filter baseline before adding vectors.
5. Close the Stage 1–5 hardening items that block safe unattended or full runs.
6. Add a rebuildability audit and artifact-side manifests sufficient to recover
   current corpus state without rerunning Stage 3.
7. Add representative fixtures and test interruption, recovery, concurrency,
   parser drift, range boundaries, qualified provenance, Docling process crashes,
   ledger loss/rebuild, and publication.
8. Select a deliberately varied Azure-versus-Docling benchmark set including
   born-digital, scanned, table-heavy, long, and known-problem documents.
9. Build analyzer comparison reports and inspect the divergent pages/structures.
10. Select the reference project/proceeding corpus and define completeness.
11. Freeze the first normalized-artifact and generation-manifest contract.
12. Rebuild the reference corpus, publish it to search, and produce a
    corpus-health/retrieval report.
13. Validate dossier, timeline, inspection, evidence-selection, and comparison
    workflows on the reference corpus.
14. Build the thin Phase 1 Atlas web application and validate search -> source ->
    page/region highlight before adding broader product features.
15. Run Copilot Studio first against a pilot Atlas Azure AI Search index and
    validate grounded answers, Atlas citation URLs, embedding, and exact
    page/region navigation before writing a custom Foundry chat layer.
16. Implement the initial Atlas front page and three-surface research workspace
    shell around Search, Document/Evidence, and Ask, keeping the chat adapter
    replaceable.

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-08-07 | Keep SQLite as the local pipeline source of truth | Portable and auditable |
| 2026-08-07 | Make AI contextual and evidence-first | Improves traceability and differentiates the research experience |
| 2026-08-07 | Use a curated prototype corpus | Optimizes for proof of value rather than premature scale |
| 2026-08-08 | Use explicit acquisition, analysis, normalization, and publication stages | Keeps external services downstream of preserved source evidence |
| 2026-08-08 | Separate code, ledger, and operational artifacts | Makes persistence and ownership visible in `pipeline/`, `database/`, and `workspace/` |
| 2026-08-08 | Keep current operations out of the roadmap | README and adjacent stage runbooks remain the implementation documentation |
| 2026-08-08 | Organize the roadmap by outcome gates | Keeps future work independent of interface choices |
| 2026-08-08 | Analyze PDFs over 300 pages with Azure `Content-Range` rather than splitting source PDFs | Preserves one source SHA/provenance identity while making large analyses resumable |
| 2026-08-08 | Qualify normalized Azure element pointers by `contents[]` index | Keeps exact analyzer-element provenance unambiguous across ranged large PDFs while retaining original page geometry |
| 2026-08-08 | Publish Stage 4 chunks directly to Azure AI Search with a controlled Stage 5 push API | Keeps REGDOCS chunking/provenance authoritative while making the normalized corpus searchable without Azure re-chunking it |
| 2026-08-08 | Measure keyword/filter retrieval before adding semantic, vector, or LLM layers | Prevents retrieval defects from being hidden behind more complex AI behavior |
| 2026-08-08 | Treat Stage 3 analysis as a multi-provider boundary and preserve native Azure/Docling outputs | Allows extraction quality, cost, and reliability to be measured without coupling the corpus contract to one provider |
| 2026-08-08 | Keep the Docling corpus supervisor strictly single-threaded with one document per child process | Limits resource contention while allowing crashes or native-library faults to be isolated and resumed durably |
| 2026-08-08 | Make future canonical analyzer choice explicit per source-file version rather than inferred | Enables auditable mixed-provider normalized generations and avoids accidental selection based on artifact timing or availability |
| 2026-08-08 | Require measured comparison before adding automatic `best` analyzer routing | Prevents heuristics or vendor assumptions from silently determining the authoritative normalized corpus |
| 2026-08-08 | Prototype Atlas as one no-login Next.js application on Azure App Service, with Entra ID deferred until identity-backed features exist | Minimizes Phase 1 infrastructure while preserving a straightforward path to enterprise SSO |
| 2026-08-08 | Keep original source documents plus Stage 4 page/polygon provenance as the Phase 1 evidence-viewing contract | Lets search and conversational citations resolve to exact visible evidence without reconstructing a competing document representation |
| 2026-08-08 | Use Copilot Studio as the first conversational prototype while keeping an Atlas-owned replaceable chat boundary | Tests the lowest-friction Azure-native path without coupling document identity, citations, provenance, or the front end to one conversational engine |
| 2026-08-08 | Make the Phase 1 front page Search/Ask-first and transition into a Search + Document/Evidence + Ask research workspace | Keeps research and source inspection primary while still making conversational discovery immediately available |
| 2026-08-09 | Make the SQLite ledger rebuildable from preserved corpus artifacts while retaining normal DB backups for fast recovery | Prevents ledger loss from forcing source re-acquisition or expensive Stage 3 recomputation and keeps artifact storage as an independent disaster-recovery path |

## Open questions

- Which project or proceeding should anchor the first internal demonstration?
- What is the minimum complete, versioned normalized-corpus contract?
- What normalization coverage threshold is acceptable before broader indexing?
- Which analyzer comparison metrics best predict downstream retrieval and
  provenance quality rather than merely extraction volume?
- Which document classes, if any, show a reliable enough Azure-versus-Docling
  advantage to justify automatic routing?
- What retrieval evaluation set and success metrics should gate semantic and
  hybrid search?
- Does Copilot Studio preserve enough Atlas search-result identity and citation
  control to drive exact page/region navigation, and is its retrieval/UX control
  sufficient for the longer-term conversational experience?
- What should the stable Atlas chat adapter contract contain so Copilot Studio
  can later be replaced by custom Foundry/assistant-ui without changing the UI's
  evidence and citation model?
- Should the Phase 1 evidence viewer use Extend UI, PDF.js with a custom SVG
  provenance overlay, or a combination after a focused technical spike?
- Which front-page corpus-health indicators and example project/query entry
  points are useful without cluttering the primary Search/Ask action?
- What citation accuracy and completeness should gate generated briefs?
- Which enrichment schema should represent commitments, conditions,
  organizations, legal references, and relationships?
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