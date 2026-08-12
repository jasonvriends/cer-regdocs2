# REGDOCS Atlas product direction

## Product promise

REGDOCS Atlas helps any CER user find, verify, collect, compare, and export regulatory evidence that is otherwise buried inside filings.

The authoritative source remains REGDOCS. Atlas must preserve a visible link from every result, extracted row, and generated statement to the original document and page.

## Primary user jobs

1. **Find one known item:** a filing number, document ID, company, project, title, or quoted passage.
2. **Discover an unknown item:** use a plain-language description when the user does not know CER terminology or where the evidence was filed.
3. **Understand a record:** see the matching passage in context, then inspect the whole document, filing, project, or company history.
4. **Build an evidence set:** collect passages into a focused Research Workspace and search within that set.
5. **Reuse regulatory data:** turn recurring tables and forms into reviewed, source-linked datasets and download CSV files.
6. **Answer a question:** synthesize only retrieved evidence, cite exact pages, disclose corpus coverage, and say when the evidence is insufficient.

## Why the Research Workspace exists

The Research Workspace is the product's equivalent of BERDI's shelf, extended for evidence-based research. It is not a shopping cart.

It allows a user to:

- collect page-level passages during a broad search;
- search within the collected set;
- keep the set's document and page identity;
- export a flat evidence CSV now;
- later share or save a workspace;
- constrain a Microsoft Foundry answer to only this evidence.

An item should never enter the workspace without a source document ID, page reference, and source URL when REGDOCS provides one.

## Capability labels

Every feature must have one visible readiness state:

- **Live:** works end to end with the current production data.
- **Pilot:** works on a named subset and includes its coverage and known limitations.
- **Planned:** explains the intended output but cannot be mistaken for a working control.

The current UI supports Azure AI Search keyword retrieval, filters, a page-like HTML document reconstruction with match highlighting, page-scoped evidence, Research Workspace search, evidence CSV export, hybrid vector retrieval, and Foundry grounded answers. Hybrid and Foundry are configuration-gated and still require deployment evaluation before being labelled broadly live. Reviewed Schedule A exports remain planned.

## Search architecture

### Current

The Stage 5 `regdocs-chunks` index contains normalized Azure layout text, tables, figures, registry metadata, page ranges, and provenance paths. It supports lexical search, exact scopes, facets, and deterministic sorting.

### Integrated: hybrid retrieval

The versioned publisher adds an embedding field, matching query vectorizer, and semantic configuration to a replacement index without touching the lexical production index. A hybrid query executes keyword and vector retrieval together and optionally applies semantic reranking. Exact identifiers, quoted phrases, and filters remain available on the lexical path; hybrid retrieval is most valuable for concept searches and unfamiliar language.

Evaluate against a CER-specific relevance set before making hybrid the default. Include known-item, concept, table, OCR, bilingual, and hard-negative queries. Track recall at 10, normalized discounted cumulative gain, citation correctness, zero-result rate, latency, and cost.

### Integrated: Microsoft Foundry

The cited-answer route uses Azure AI Search as the retrieval layer and Microsoft Foundry as the synthesis layer, not the database or source of truth.

The route must:

- honor all active scopes and filters;
- allow a workspace-only mode;
- return document/page citations that the UI can open;
- show the primary corpus date window with every answer;
- distinguish sourced statements from model inference;
- decline or qualify answers when retrieval is insufficient;
- log retrieval and citations for evaluation without exposing secrets.

## Data products and Schedule A

“Schedule A” is a document label, not one universal schema. The first pilot should therefore be a two-stage system:

1. **Candidate discovery:** find title, heading, and body mentions; cluster candidates by form family and filing context.
2. **Reviewed extraction:** define a schema for one family, extract rows with page provenance, validate types and units, review exceptions, then publish CSV plus a data dictionary.

Each published row needs:

- stable row and source identifiers;
- original value and normalized value;
- document ID, filing number, page, and source URL;
- extraction model/version;
- review status and confidence or exception reason;
- dataset coverage dates and refresh timestamp.

The same catalogue pattern can later support conditions, commitments, information requests, consultation logs, watercourse tables, financial schedules, incident timelines, and regulatory instruments.

## Corpus statistics contract

Never display an unlabeled “documents” number. Use these definitions:

- **Registry record:** a filing, folder, compound document, or file returned by REGDOCS collection.
- **Searchable document:** a downloaded file analyzed and published to the search index.
- **Page:** an analyzer page record.
- **Table:** an analyzer-detected table; this is not necessarily one logical table across multiple pages.
- **Passage:** a normalized text, table, or figure chunk returned by search.

Coverage metrics should eventually come from a generated, deployable manifest rather than constants in the browser bundle. The manifest should include the primary complete date window, linked-record range, counts, generation time, index version, and known gaps.

## Delivery order

1. Instrument search queries, zero-result searches, filter use, source opens, workspace adds, and exports with privacy-conscious analytics.
2. Generate the coverage manifest during normalization/index publication and expose it through an API.
3. Add saved/shareable workspaces with stable URLs and an explicit retention policy.
4. Publish and evaluate the integrated vector index; make hybrid the default only after it clears the relevance and latency thresholds.
5. Evaluate the integrated Foundry route for citation correctness, unsupported claims, retrieval sufficiency, latency, and cost.
6. Pilot one Schedule A family and publish its reviewed CSV/data dictionary.
7. Add an optional original-page comparison beside the accessible HTML reader and provenance polygon highlights on the source image.

## Product success measures

- percentage of tasks that reach a verified source page;
- median time to first useful passage;
- successful known-item lookup rate;
- zero-result and reformulation rates;
- workspace-to-source-open and workspace-to-export rates;
- search relevance and citation correctness on the CER evaluation set;
- structured extraction precision, recall, and review burden;
- user confidence in coverage and source traceability.
