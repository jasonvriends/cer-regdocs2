# REGDOCS Atlas v1 product contract

## Product promise

REGDOCS Atlas helps a CER researcher find, verify, collect, compare, and understand evidence that is otherwise buried inside REGDOCS filings.

The Canada Energy Regulator REGDOCS source remains authoritative. Atlas keeps a visible path from retrieved evidence and derived regulatory intelligence back to the source document and page wherever the source data permits it.

This file describes **v1**, not a roadmap. A control belongs in the v1 UI only when its end-to-end data path is implemented.

## V1 user jobs

1. **Ask a question** in ordinary language and get a Microsoft Foundry answer grounded in retrieved CER evidence.
2. **Find evidence** by exact identifiers, company, project, filing, content type, keyword, concept, or a combination of filters.
3. **Verify the source** by opening the cited passage inside Atlas's normalized HTML document viewer and following the original REGDOCS link when available.
4. **Build an evidence set** by saving source passages to the Shelf, questioning only that set, and exporting it as CSV.
5. **Understand chronology** through a scoped regulatory timeline.
6. **Understand relationships** through a scoped entity/relationship graph.
7. **Inspect regulatory statements** through Findings & claims.
8. **Inspect regulatory duties** through Commitments & obligations, including explicit deadline/status views.
9. **Understand coverage and provenance** through live corpus metadata, answer-run details, diagnostics, and source links.

## V1 UI capabilities

### Grounded Ask

Atlas retrieves evidence from Azure AI Search before synthesis. The Ask route can use keyword, hybrid vector, and semantic retrieval according to deployment configuration.

The UI distinguishes:

- **Retrieved evidence** — passages Search found;
- **Cited evidence** — passages the validated Foundry answer actually cited.

A successful answer exposes an expandable run summary with:

- whether Microsoft Foundry was used;
- Foundry deployment;
- retrieval mode and any fallback;
- semantic-ranking state;
- retrieved/cited evidence counts;
- retry count;
- Search, Foundry, and total timing;
- live corpus index/date coverage.

If synthesis fails after retrieval, retrieved evidence can still be shown without being mislabeled as cited evidence.

### Search and filters

The Search backend supports:

- keyword retrieval;
- hybrid vector retrieval;
- optional semantic reranking;
- document ID;
- filing ID / filing number;
- company;
- project;
- page;
- content type (text/table/figure);
- application type;
- commodity;
- document type;
- file type;
- role;
- relevance/newest/oldest/document-order sorting where applicable.

The chat composer exposes the high-value filters needed by the research workflow. Exact Search API fields remain available server-side for future clients without requiring another data pipeline.

### Source/document viewer

Atlas uses a normalized HTML reconstruction instead of embedding a PDF.

The reader:

- loads all indexed chunks for a document in `chunk_index` order;
- groups content by normalized page number;
- jumps to a requested page;
- highlights the selected evidence passage and useful terms;
- renders text chunks;
- renders tabular chunks as HTML tables when row/column separators are available;
- shows extracted figure text for figure chunks;
- lets the user add the selected passage to the Shelf;
- provides **Original in REGDOCS** when the normalized source has a source URL;
- clearly states that HTML layout can differ from the original source.

The viewer requires only the Stage 5 Search data described in [`DATA-CONTRACT.md`](DATA-CONTRACT.md). A duplicate PDF upload is not required for the v1 application.

### Shelf

The Shelf is a browser-side research evidence set. A saved item keeps its Search chunk/document/page/source identity.

Users can:

- add evidence from cited/retrieved source cards;
- add the selected passage from the document reader;
- reopen saved evidence;
- remove evidence;
- ask a question constrained to the saved chunk IDs;
- export the saved evidence as CSV.

Server-side saved/shared workspaces are not part of v1.

### Regulatory timeline

The timeline reads `regdocs-events` and requires an explicit document, filing, company, or project scope. Event records retain date basis/precision, origin, confidence, review state, and available source evidence.

### Relationship graph

The graph reads `regdocs-relations` and `regdocs-entities` for an explicit research scope. Relationship edges retain available source evidence and review/provenance metadata.

### Findings & claims

The view reads `regdocs-claims` and exposes extracted statement, claimant/subject where present, evidence, confidence, origin, extractor version, and review state.

A “regulator finding” label is used conservatively from explicit extracted claimant/type information; Atlas does not silently turn party claims into regulator findings.

### Commitments & obligations

The view reads `regdocs-obligations` and exposes obligation type, obligated party, action, deadline/status where present, source evidence, confidence, origin, extractor version, and review state.

“Outstanding” is based on explicit extracted status such as open/pending/outstanding/incomplete/due/overdue. Atlas does not independently make a legal determination that an obligation remains unsatisfied.

### Coverage

Coverage is read from the currently configured Search index rather than hard-coded dates. It reports the active index, indexed chunk count, and represented filing-date range.

### Diagnostics and errors

Operators have:

- shallow service/configuration diagnostics;
- protected live diagnostics for Search, hybrid/semantic retrieval, document retrieval, Foundry, and the five intelligence indexes;
- structured Ask telemetry without question text;
- user-visible `ATLAS-...` references for server faults;
- Log Analytics lookup by error reference.

## V1 data architecture

```text
CER REGDOCS
   ↓
Stages 1–4
   ↓
normalized five-file package
   ├── Stage 5 → regdocs-chunks-hybrid
   │               ├── Ask retrieval
   │               ├── source cards
   │               ├── HTML document viewer
   │               ├── Shelf evidence
   │               └── coverage
   │
   └── Stage 6 → deterministic + Microsoft Foundry extraction
                   ├── regdocs-entities
                   ├── regdocs-relations
                   ├── regdocs-events
                   ├── regdocs-claims
                   └── regdocs-obligations
```

Stage 6 is the final data-processing stage. Deployment verification is not another pipeline stage.

See [`DATA-CONTRACT.md`](DATA-CONTRACT.md) for the exact feature-to-data contract.

## Grounding rules

Atlas is evidence-first:

- Azure AI Search is the retrieval layer and corpus source for Ask.
- Foundry is the synthesis/extraction layer, not the authoritative record.
- citations must resolve to retrieved evidence;
- Stage 6 model records must cite valid chunk IDs from the exact extraction input;
- model-derived intelligence remains `unreviewed` until reviewed;
- material decisions should be checked against the original REGDOCS source.

## Capability status for v1

The v1 product is designed to ship with these controls:

```text
Ask
Filters
Retrieved/cited sources
HTML source viewer
Shelf + Shelf-only Ask + CSV export
Timeline
Relationship graph
Findings & claims
Commitments & obligations
Coverage
Diagnostics/error tracing
```

The UI intentionally does **not** advertise unfinished controls for reviewed Schedule A datasets, arbitrary data-product generation, shared server-side workspaces, or embedded original-page PDF rendering.

## V1 completion

The project is complete when the finite acceptance list in [`../COMPLETION.md`](../COMPLETION.md) passes against the production deployment.

New capabilities after that point are a later release, not additional stages required to complete v1.
