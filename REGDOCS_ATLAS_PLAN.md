# REGDOCS Atlas — Product, Architecture, and Delivery Plan

> **Purpose:** Keep the project focused on what we are building, why we are building it, what belongs in each phase, and what should be deferred until later.
>
> This is a living project roadmap. Update it whenever a material product or architecture decision changes.

---

# 1. North Star

## Working product name

**REGDOCS Atlas**

Alternative working names may be explored later, but the product concept should remain the same:

> **A modern regulatory research workbench built on top of the Canada Energy Regulator's public REGDOCS record.**

The goal is **not** to build another generic "chat with your documents" interface.

The goal is to demonstrate that public regulatory records can be:

- systematically acquired;
- organized into meaningful regulatory structures;
- searched and explored visually;
- inspected with strong provenance;
- transformed into evidence-grounded AI experiences; and
- eventually used as the foundation for production-grade regulatory intelligence.

---

# 2. Why We Are Building This

The immediate goal is an **internal proof of value**.

We want a polished prototype that can be shown internally to:

1. demonstrate the value of modernizing access to public REGDOCS material;
2. show that the acquisition problem is technically tractable;
3. show that regulatory records become substantially more useful when represented as projects, filings, dossiers, timelines, and relationships instead of flat documents;
4. demonstrate how AI can operate on selected, traceable evidence rather than act as an ungrounded chatbot; and
5. generate enough interest and internal buy-in to justify a funded project.

The prototype is successful when the conversation changes from:

> "This is an interesting experiment."

to:

> **"What would it take to make this a real project?"**

---

# 3. Product Principles

## 3.1 Evidence first

AI should act on identifiable source material.

Every AI-generated statement should eventually be traceable to:

- a document;
- a page or section where possible;
- a REGDOCS identifier;
- a source URL;
- a file hash/version; and
- the acquisition record that produced it.

## 3.2 AI is a capability, not the homepage

Do not make the main interface a chat window.

The primary interaction model should be:

- browse;
- search;
- filter;
- inspect;
- compare;
- select evidence;
- build a timeline;
- build a brief;
- find relationships.

Conversational AI may exist later as a **secondary contextual tool**.

## 3.3 Preserve regulatory structure

Do not flatten everything into independent chunks.

Preserve useful structure such as:

- projects;
- companies;
- filings;
- document identifiers;
- Folder membership;
- Compound Document membership;
- filing chronology;
- document types;
- source relationships;
- extracted references later.

## 3.4 Acquisition and application are separate systems

The acquisition pipeline should remain independently auditable.

The public application should consume a published projection of the acquisition corpus.

The frontend should never become the source of truth for acquisition state.

## 3.5 Prototype for value, not theoretical scale

The prototype does **not** need every REGDOCS record.

A polished experience over a few thousand real documents is more persuasive than a rough experience over hundreds of thousands.

## 3.6 Make it feel like a research product

Visual inspiration should come from:

- research tools;
- document intelligence products;
- modern developer tools;
- Bloomberg-style information density;
- Linear-style polish;
- GitHub-style traceability;
- high-quality search products.

Avoid the visual language of generic AI chatbot templates.

---

# 4. What Exists Today

## Stage 1 — Scout

`regdocs_1_scout.py`

Purpose:

- search REGDOCS by date;
- discover document/item records;
- collect exposed metadata;
- discover explicit Folder and Compound Document members;
- traverse nested containers safely;
- collect live facets;
- fetch item detail pages;
- preserve raw HTML source responses;
- maintain pipeline status, runs, progress, and errors.

Primary output:

```text
regdocs.db
raw/regdocs/
_audit/
```

The scout is the **catalogue and provenance stage**.

---

## Stage 2 — Download

`regdocs_2_download.py`

Purpose:

- select downloadable records from the scout ledger;
- reconcile existing files;
- download eligible source files;
- identify actual file types;
- validate responses;
- calculate SHA-256 hashes;
- archive replaced versions;
- update file state in SQLite;
- optionally create deterministic metadata sidecars.

Primary output:

```text
downloads/
regdocs.db
_audit/
```

The downloader is the **source-file acquisition and versioning stage**.

---

# 5. Current Source of Truth

The local acquisition source of truth is:

```text
regdocs.db
```

plus:

```text
raw/
downloads/
```

The SQLite database is a pipeline ledger, not the public application's final query database.

The current five-table acquisition schema is:

```text
documents
runs
errors
raw_snapshots
files
```

This design should remain independent from the public application schema.

---

# 6. High-Level System Architecture

The project has two architectural planes.

## 6.1 Acquisition plane

Runs outside the public web application.

```text
Canada Energy Regulator REGDOCS
              |
              v
      regdocs_1_scout.py
              |
              v
         regdocs.db
        /          \
       v            v
raw/regdocs/   regdocs_2_download.py
                    |
                    v
                downloads/
                    |
                    v
             publish / transform
```

During the prototype, this can run:

- manually on a development machine;
- from a controlled workstation;
- from a simple scheduled worker later.

It should **not** run on Vercel.

---

## 6.2 Public application plane

Required architecture:

```text
Users
  |
  v
Vercel
+----------------------------------+
| Frontend only                    |
|                                  |
| - React / Next.js static UI      |
| - Pages and navigation           |
| - Client-side Supabase SDK       |
| - Static assets                  |
+----------------+-----------------+
                 |
                 | HTTPS
                 v
Supabase
+----------------------------------+
| Postgres                         |
| - Published REGDOCS data         |
| - Search-oriented schema         |
| - SQL functions                  |
| - RLS                            |
+----------------------------------+
| Storage                          |
| - Derived assets                 |
| - Thumbnails                     |
| - Extracted document artifacts   |
+----------------------------------+
| Edge Functions                   |
| - AI calls                       |
| - Privileged logic               |
| - External integrations          |
+----------------------------------+
| pgvector                         |
| - Embeddings later               |
+----------------------------------+
| Scheduled jobs                   |
| - backend scheduling if needed   |
+----------------------------------+
```

### Non-negotiable frontend/backend boundary

**Vercel is frontend-only.**

Do not add:

- Next.js API routes;
- Vercel Functions;
- Vercel Edge Functions;
- server-side business logic;
- service-role keys;
- AI API secrets;
- database passwords.

**Supabase owns application backend behavior.**

Use Supabase for:

- Postgres;
- authorization;
- storage;
- Edge Functions;
- AI API calls;
- secret handling;
- future scheduling;
- future authentication if required.

This follows the project's `ARCHITECTURE.md`.

---

# 7. Proposed Repository Structure

Target structure:

```text
repository/
│
├── acquisition/
│   ├── regdocs_1_scout.py
│   ├── regdocs_2_download.py
│   ├── regdocs_publish.py
│   └── README.md
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── lib/
│   │   │   └── supabase/
│   │   ├── pages/ or app/
│   │   └── types/
│   ├── public/
│   └── package.json
│
├── supabase/
│   ├── migrations/
│   ├── functions/
│   ├── seed.sql
│   └── tests/
│
├── docs/
│   ├── PRODUCT_PLAN.md
│   ├── ARCHITECTURE.md
│   └── ...
│
├── regdocs.db                 # local / ignored as appropriate
├── raw/                       # local acquisition evidence
├── downloads/                 # local acquired files
├── README.md
└── ARCHITECTURE.md
```

The exact layout may evolve, but the logical separation should remain.

---

# 8. Publishing Layer

## Build next: `regdocs_publish.py`

The next pipeline component should publish a **clean application projection** from SQLite into Supabase.

```text
regdocs.db
    |
    v
regdocs_publish.py
    |
    +--> normalize application data
    +--> upsert changed records
    +--> preserve stable IDs
    +--> publish relationships
    +--> publish corpus statistics
    |
    v
Supabase Postgres
```

The publisher should be:

- idempotent;
- resumable;
- deterministic;
- incremental where practical;
- explicit about what data is application-facing.

Supabase should **not** simply become a byte-for-byte clone of the five-table acquisition ledger.

---

# 9. Proposed Public Application Data Model

The application schema should optimize for exploration and search.

Initial conceptual schema:

```text
projects
--------
id
name
slug
metadata


companies
---------
id
name
slug
metadata


filings
-------
id
filing_number
project_id
company_id
filing_date
title
metadata


documents
---------
id
title
source_url
item_kind
filing_date
filing_id
project_id
company_id
mime_type
extension
sha256
metadata


document_relationships
----------------------
parent_document_id
child_document_id
relationship_type
position
metadata


document_facets
---------------
document_id
category
value


corpus_stats
------------
metric
value
updated_at
```

Later:

```text
document_pages
document_chunks
entities
entity_mentions
document_references
```

The exact normalized schema should be established through migrations.

---

# 10. Prototype Scope

The prototype should deliberately use a **curated subset**.

Suggested target:

```text
1–3 projects / proceedings
20–100 filings
1,000–5,000 documents
hundreds or thousands of source files
```

Selection criteria:

- meaningful regulatory history;
- multiple filings over time;
- folders / compound documents;
- multiple document types;
- enough complexity to demonstrate relationships;
- enough source text to later demonstrate RAG;
- public and suitable for internal demonstration.

Do not make full-corpus ingestion a blocker for the prototype.

---

# 11. Core User Experience

The product should be organized around five primary experiences.

---

## 11.1 Explore / Search

Purpose:

> Make REGDOCS feel like a modern research database.

Example:

```text
┌──────────────────────────────────────────────────────────────┐
│ REGDOCS ATLAS                                                │
│                                                              │
│ [ Search projects, filings, companies, documents...       ] │
├──────────────────────────────────────────────────────────────┤
│ Filters                 │ Results                            │
│                         │                                    │
│ Project                 │ Environmental Assessment           │
│ □ Project A             │ C12345 · A987654                  │
│ □ Project B             │ 14 Mar 2025 · PDF · 214 pages     │
│                         │                                    │
│ Type                    │ ...potential effects to woodland...│
│ □ Decision              │                                    │
│ □ Evidence              │ [Open] [Pin] [Related]            │
│ □ Application           │                                    │
│                         │                                    │
│ Date                    │                                    │
│ 2022 ───────── 2026     │                                    │
└──────────────────────────────────────────────────────────────┘
```

Initial capabilities:

- keyword search;
- filtering;
- sorting;
- project filter;
- filing filter;
- company filter;
- date range;
- document type / facet filters;
- shareable URLs.

Later:

- full-text search;
- semantic search;
- hybrid retrieval;
- result snippets from extracted text.

---

## 11.2 Project / Filing Dossier

Purpose:

> Show regulatory structure instead of a flat document list.

Example:

```text
C12345 — Application for Example Project

Application
├── Cover Letter
├── Application
├── Environmental Assessment
│   ├── Appendix A
│   ├── Appendix B
│   └── Maps
├── Engineering
└── Consultation Records
```

Capabilities:

- Folder / Compound Document tree;
- filing metadata;
- project/company context;
- document counts;
- document types;
- source links;
- later: AI actions on selected subtrees.

---

## 11.3 Regulatory Timeline

Purpose:

> Turn filing dates into an understandable chronology.

Example:

```text
March 4
Application submitted
12 documents
      |
      v
March 28
CER Information Request
8 questions
      |
      v
April 17
Applicant response
24 documents
      |
      v
May 12
Intervenor evidence
      |
      v
August 19
Decision
```

Initial timeline may use:

- filing dates;
- filing titles;
- document counts;
- inferred filing groupings from existing metadata.

Later:

- AI-generated event descriptions;
- extracted regulatory actions;
- decision/response relationships;
- important-event ranking.

---

## 11.4 Document X-Ray

Purpose:

> Make each source document an inspectable research object.

Initial view:

```text
Environmental and Socio-Economic Assessment
A1234567

PDF · 214 pages

Filed             March 14, 2025
Filing            C123456
Company            Example Pipeline Ltd.
Project            Example Expansion
Document Type      Environmental Report

[View source] [Open dossier] [Related documents]
```

Later enrich with:

- table of contents;
- extracted sections;
- page thumbnails;
- entities;
- places;
- organizations;
- regulations;
- maps / figures;
- tables;
- extracted commitments;
- document summary;
- page-level search.

---

## 11.5 Evidence Brief

Purpose:

> Demonstrate AI as an evidence-grounded research capability.

The user selects a bounded set of documents or passages first.

Actions:

```text
[Build brief]
[Compare]
[Build timeline]
[Find themes]
[Find related]
[Extract commitments]
```

Example output:

```text
REGULATORY BRIEF

Issue
-----
The proceeding concerned ...

Applicant position
------------------
...

Commission concerns
-------------------
...

Participant evidence
--------------------
...

Key chronology
--------------
...

Evidence
--------
A123456 · p.17
A123891 · pp.42–44
A124011 · p.8
```

Every material claim should eventually link to source evidence.

---

# 12. Additional Proposed Features

These are not all Phase 1 requirements.

## Evidence Board

Allow users to pin documents/passages into a workspace.

```text
┌────────────────────┐  ┌────────────────────┐
│ CER Decision       │  │ Applicant Response │
│ pages 31–34        │  │ pages 8–12         │
└────────────────────┘  └────────────────────┘

        [Compare selected evidence]
```

Potential future features:

- pin;
- reorder;
- annotate;
- compare;
- summarize;
- export brief;
- share collection.

---

## Relationship Graph

Visualize connections between:

```text
Project
  |
Company
  |
Filing
  |
Documents
  |
Referenced documents / orders / activities
```

Start with known structural relationships.

Later add relationships extracted from document text.

---

## Corpus Health

A polished operational page that demonstrates the engineering underneath the product.

Example:

```text
REGDOCS CORPUS

Documents discovered       2,783
Downloadable files         2,416
Successfully acquired      2,404
Retryable failures            12

PDF                         1,943
Word                          241
Spreadsheets                   89
Other                         131

Raw source snapshots        8,921
Downloaded files hashed      100%

Last refresh
7 August 2026
```

Provenance panel:

```text
✓ Original REGDOCS URL preserved
✓ Source metadata snapshot preserved
✓ Container membership preserved
✓ Source file SHA-256 preserved
✓ Acquisition history preserved
```

This page is especially useful for internal demonstrations.

---

# 13. UI Direction

## Avoid

- chatbot-first layouts;
- giant empty chat screens;
- generic Azure/OpenAI demo templates;
- overly sparse AI dashboards;
- unexplained confidence scores;
- AI answers without visible source evidence.

## Prefer

- dense but clean information layouts;
- split panes;
- timelines;
- trees;
- filters;
- command-palette search;
- expandable source citations;
- side drawers for contextual actions;
- strong typography;
- subtle motion;
- breadcrumbs;
- inspectable metadata;
- URL-addressable application state.

Suggested routes:

```text
/
 /search
 /projects
 /projects/:slug
 /filings/:id
 /documents/:id
 /timeline
 /corpus
```

Later:

```text
/documents/:id?page=83
/evidence/:collection_id
/relationships
```

---

# 14. Search Evolution

Search should evolve in stages.

## Search v1 — metadata

Use Postgres to search/filter:

- title;
- filing number;
- project;
- company;
- date;
- facets;
- document kind.

This is enough for the first useful UI.

## Search v2 — extracted full text

Add:

- document text;
- page text;
- section text;
- highlighting;
- snippets.

## Search v3 — hybrid retrieval

Combine:

```text
keyword / full-text relevance
+
metadata filters
+
vector similarity
```

Embeddings can be stored with `pgvector`.

Do not add vector search before there is extracted text worth embedding.

---

# 15. RAG / AI Evolution

AI should be added only after retrieval and provenance are working.

## RAG Stage A — bounded summarization

Input:

- one selected document;
- one selected folder;
- one selected filing.

Output:

- summary;
- key topics;
- important dates.

## RAG Stage B — evidence comparison

Input:

- user-selected documents/passages.

Output:

- agreements;
- disagreements;
- changes;
- positions;
- commitments;
- evidence citations.

## RAG Stage C — dossier intelligence

Input:

- a filing/project dossier.

Output:

- chronology;
- issues;
- parties;
- positions;
- regulatory concerns;
- outcomes;
- source citations.

## RAG Stage D — contextual ask

Only later introduce:

```text
Ask about this dossier
```

as a drawer or contextual panel.

Do not make general chat the central application experience.

---

# 16. Document Intelligence Stage

This is a later pipeline stage.

Working name:

```text
Stage 3 — Process / Document Intelligence
```

Responsibilities:

- native text extraction;
- OCR for scanned documents;
- page segmentation;
- layout analysis;
- section extraction;
- table extraction;
- figure/image extraction;
- language detection;
- page thumbnails;
- quality signals;
- normalized output.

Potential tools may be evaluated later.

The acquisition pipeline should not become coupled to one extraction vendor.

---

# 17. Proposed Canonical Processed Document Model

Conceptual only:

```json
{
  "document_id": "4710492",
  "source_sha256": "...",
  "processor_version": "...",
  "title": "...",
  "source_url": "...",
  "filing_number": "...",
  "project": "...",
  "company": "...",
  "pages": [
    {
      "page_number": 1,
      "text": "...",
      "sections": [],
      "tables": [],
      "figures": []
    }
  ],
  "chunks": [
    {
      "chunk_id": "4710492:p12:c03",
      "page_number": 12,
      "section": "...",
      "content": "...",
      "metadata": {}
    }
  ]
}
```

Processing cache identity should eventually include:

```text
document_id
+
source_sha256
+
processor_version
```

This prevents unnecessary reprocessing of unchanged source files.

---

# 18. Derived Asset Strategy

For the prototype, do not automatically re-host every original source PDF.

Prefer:

- keep authoritative source link to REGDOCS;
- store metadata in Supabase;
- store derived assets in Supabase Storage.

Potential derived assets:

```text
derived/
  <document-id>/
    cover.webp
    pages/
      001.webp
      002.webp
    figures/
    extraction.json
```

This keeps the prototype lighter while supporting a richer UI.

Re-hosting original source documents should be treated as a deliberate later policy/architecture decision.

---

# 19. Security Model for the Public Prototype

Because the initial corpus is public:

- no login is required for normal browsing;
- public data is read-only;
- frontend uses only public Supabase configuration;
- RLS should still be enabled;
- public `SELECT` policies may be used where justified;
- browser clients must not receive privileged credentials.

Privileged operations remain in Supabase Edge Functions.

If user accounts are added later:

- use Supabase Auth;
- enforce authorization in Postgres/RLS;
- do not trust frontend role checks.

---

# 20. Prototype Phases

---

## Phase 0 — Foundations

### Goal

Preserve what already works and establish project structure.

### Deliverables

- [x] Stage 1 scout
- [x] Stage 2 downloader
- [x] SQLite acquisition ledger
- [x] raw source archive
- [x] file hashing/version logic
- [x] architecture decision: Vercel frontend-only
- [x] architecture decision: Supabase backend
- [ ] organize repository directories
- [ ] establish Git hygiene / ignored corpus files
- [ ] choose prototype project/proceeding subset
- [ ] establish Supabase local/project environments
- [ ] establish frontend shell

### Definition of done

The existing pipeline remains runnable and documented after repository restructuring.

---

## Phase 1 — Publish the Corpus

### Goal

Make selected REGDOCS data queryable by the application.

### Deliverables

- [ ] design initial Supabase application schema
- [ ] create version-controlled migrations
- [ ] create public read-only RLS policies
- [ ] implement `regdocs_publish.py`
- [ ] publish projects
- [ ] publish companies
- [ ] publish filings
- [ ] publish documents
- [ ] publish container relationships
- [ ] publish useful facets
- [ ] publish corpus statistics
- [ ] verify idempotent re-publishing
- [ ] preserve stable REGDOCS IDs

### Definition of done

A clean Supabase database can be rebuilt from the selected local acquisition corpus and queried directly from the frontend.

---

## Phase 2 — Build the Research UI

### Goal

Make the corpus compelling without AI.

### Deliverables

- [ ] homepage / observatory
- [ ] global search
- [ ] faceted filters
- [ ] project page
- [ ] filing dossier page
- [ ] Folder / Compound tree
- [ ] regulatory timeline
- [ ] document metadata page
- [ ] links to authoritative REGDOCS source
- [ ] corpus health page
- [ ] responsive design
- [ ] shareable URLs
- [ ] polished loading/empty/error states

### Definition of done

A user can discover a project, navigate its filing structure, inspect documents, understand chronology, and verify provenance without using AI.

This should already be internally demo-worthy.

---

## Phase 3 — Document Intelligence

### Goal

Turn source files into structured content.

### Deliverables

- [ ] choose extraction approach
- [ ] process selected PDFs/documents
- [ ] store page-level text
- [ ] preserve page numbers
- [ ] generate page thumbnails where useful
- [ ] extract headings/sections
- [ ] extract tables where practical
- [ ] quality flags
- [ ] extraction versioning
- [ ] incremental processing based on SHA-256
- [ ] publish extracted data to Supabase

### Definition of done

Users can search inside documents and inspect meaningful page/section content with traceable source locations.

---

## Phase 4 — Full-Text Search

### Goal

Improve discovery before adding embeddings.

### Deliverables

- [ ] Postgres full-text search
- [ ] extracted text snippets
- [ ] page-level result locations
- [ ] highlighting
- [ ] ranking tuning
- [ ] retain metadata filters

### Definition of done

Users can search regulatory concepts inside document content while preserving structured filters and source context.

---

## Phase 5 — Semantic / Hybrid Search

### Goal

Add semantic retrieval where keyword search is insufficient.

### Deliverables

- [ ] chunking strategy
- [ ] deterministic chunk IDs
- [ ] embeddings pipeline
- [ ] `pgvector`
- [ ] vector indexing
- [ ] hybrid ranking strategy
- [ ] evaluation dataset
- [ ] retrieval quality testing
- [ ] citation metadata per chunk

### Definition of done

Conceptually related evidence can be retrieved reliably without sacrificing metadata constraints or source traceability.

---

## Phase 6 — Evidence-Grounded AI

### Goal

Deliver the prototype's main AI proof of value.

### Deliverables

- [ ] Supabase Edge Function for model calls
- [ ] bounded summarization
- [ ] evidence comparison
- [ ] dossier brief
- [ ] chronology generation
- [ ] extracted commitments
- [ ] source citations
- [ ] link citations to page/document views
- [ ] streaming UI if useful
- [ ] basic AI evaluation
- [ ] failure / insufficient-evidence behavior

### Definition of done

A user can select a bounded evidence set and generate a useful regulatory brief whose claims can be checked against visible source evidence.

---

## Phase 7 — Advanced Research Features

Possible later features:

- [ ] Evidence Board
- [ ] saved collections
- [ ] relationship graph
- [ ] entity extraction
- [ ] cited-reference graph
- [ ] related-document recommendations
- [ ] change detection
- [ ] compare document versions
- [ ] export brief
- [ ] contextual "Ask this dossier"
- [ ] bilingual experience
- [ ] alerts / monitoring
- [ ] collaborative annotations

These should not delay the core internal prototype.

---

# 21. Internal Demo Narrative

The demo should tell a story.

## Step 1 — Start with REGDOCS as a corpus

Show:

- real public regulatory records;
- a selected real proceeding/project;
- corpus size;
- acquisition provenance.

## Step 2 — Search

Search for a meaningful regulatory topic.

Show:

- metadata;
- filtering;
- fast navigation;
- clear result context.

## Step 3 — Open a dossier

Show:

- filing structure;
- Compound Documents;
- folders;
- related documents;
- regulatory chronology.

## Step 4 — Open a document

Show:

- metadata;
- source link;
- hash/provenance;
- extracted content if Phase 3 is complete.

## Step 5 — Select evidence

Pick a bounded set of documents or passages.

## Step 6 — Build a brief

Generate:

- issue summary;
- chronology;
- positions;
- commitments;
- citations.

## Step 7 — Show provenance

Open one of the cited sources directly.

## Step 8 — Show the architecture

Explain:

```text
REGDOCS
  ↓
auditable acquisition
  ↓
structured corpus
  ↓
modern research interface
  ↓
evidence-grounded AI
```

Do not lead the demo with architecture.

Lead with the user experience.

---

# 22. What We Are Deliberately Not Building Yet

Prototype non-goals:

- enterprise authentication;
- full organization-wide production architecture;
- full REGDOCS historical corpus;
- production SLA;
- large-scale ingestion orchestration;
- complex user permissions;
- user uploads;
- payments;
- notifications;
- collaboration;
- comprehensive analytics;
- production records-management workflows;
- a generic chatbot;
- an autonomous agent;
- automatic legal conclusions;
- a replacement for authoritative REGDOCS records.

These may become funded-project requirements later.

---

# 23. Production Questions to Defer Until Funding

The prototype should make these questions easier to answer, not prematurely solve all of them.

Potential funded-project decisions:

- enterprise cloud platform;
- Azure vs other approved hosting;
- production search technology;
- production AI/model hosting;
- security assessment;
- privacy review;
- records management;
- legal review;
- accessibility requirements;
- bilingual requirements;
- identity/access management;
- production monitoring;
- disaster recovery;
- source refresh SLA;
- full-corpus storage policy;
- document re-hosting policy;
- AI evaluation/governance;
- data retention;
- support ownership;
- product ownership;
- operating budget.

Prototype wording for internal discussion:

> The prototype architecture is optimized for rapid experimentation and low operational overhead. A production implementation would be subject to enterprise architecture, security, accessibility, privacy, records-management, bilingual, operational, and cloud-platform requirements.

---

# 24. Success Criteria

The prototype is successful if internal reviewers can clearly see that:

## Acquisition

- REGDOCS can be systematically collected;
- provenance can be preserved;
- acquisition can be refreshed incrementally;
- source file identity/version can be verified.

## Research experience

- regulatory records can be easier to browse than in a flat document index;
- filing structure is valuable;
- timeline views are valuable;
- project/dossier context is valuable.

## Search

- structured metadata materially improves discovery;
- full-text/semantic retrieval has a credible path;
- source filters can constrain retrieval.

## AI

- AI can create useful research products from bounded evidence;
- citations make outputs inspectable;
- RAG has value beyond a generic chat interface.

## Project viability

- the prototype suggests an obvious funded next step;
- the architecture can evolve without throwing away the acquisition work.

---

# 25. Build Priority

When deciding what to work on next, use this priority order:

```text
1. Does it improve the internal demo?
2. Does it make the corpus easier to understand?
3. Does it strengthen provenance or trust?
4. Does it unlock the next phase?
5. Is it required for the current phase's definition of done?
```

If the answer is no to all five, defer it.

---

# 26. Immediate Next Actions

Recommended next sequence:

```text
1. Select the prototype project/proceeding corpus.
2. Create the Supabase application schema.
3. Build regdocs_publish.py.
4. Publish the selected corpus.
5. Create the Vercel frontend shell.
6. Build Search.
7. Build Filing / Dossier view.
8. Build Timeline.
9. Build Document view.
10. Build Corpus Health.
11. Demo the non-AI product.
12. Add document extraction.
13. Add full-text search.
14. Add semantic retrieval.
15. Add Evidence Brief.
```

The first internal demo does **not** need to wait until Step 15.

---

# 27. Decision Log

Add significant decisions here as they are made.

| Date | Decision | Reason |
|---|---|---|
| 2026-08-07 | Keep SQLite as acquisition source of truth | Portable, auditable, independent of application backend |
| 2026-08-07 | Use Vercel for frontend only | Fast iteration and polished prototype delivery |
| 2026-08-07 | Use Supabase as complete application backend | Postgres, RLS, Storage, Edge Functions, future pgvector |
| 2026-08-07 | Keep acquisition outside Vercel | Scout/download are long-running workloads |
| 2026-08-07 | Publish a projection instead of replacing SQLite | Keeps acquisition and application concerns separate |
| 2026-08-07 | AI will be contextual/evidence-first, not homepage chat | Differentiates product and improves traceability |
| 2026-08-07 | Prototype can use a curated corpus | Optimize for proof of value, not corpus completeness |

---

# 28. Open Questions

Track unresolved decisions here.

- Which project/proceeding(s) should anchor the internal prototype?
- What exact fields should be normalized into the Supabase schema?
- Which metadata should remain `jsonb`?
- Should original PDFs be mirrored in prototype storage or linked from REGDOCS only?
- Which document intelligence stack should be tested first?
- What chunking model best preserves page/section provenance?
- Which model/provider should be used for the first AI brief?
- What minimum citation quality is acceptable for the demo?
- What UI library/design system should be used?
- How much bilingual functionality is needed in the prototype?
- What internal audience should the first demo target?

---

# 29. Guardrail Against Scope Drift

Before adding a feature, ask:

> **Does this help demonstrate the value of turning REGDOCS into a modern, evidence-grounded regulatory research experience?**

If not, put it in the backlog.

The prototype is not meant to prove that every production requirement has already been solved.

It is meant to prove that the project is worth funding.

---

# 30. Final Product Vision

Long-term, REGDOCS Atlas could evolve into:

```text
Public regulatory records
        |
        v
Auditable acquisition
        |
        v
Structured regulatory knowledge base
        |
        +--> metadata search
        +--> full-text search
        +--> semantic retrieval
        +--> project / filing dossiers
        +--> timelines
        +--> relationship graphs
        +--> evidence boards
        |
        v
Evidence-grounded regulatory intelligence
        |
        +--> briefs
        +--> comparisons
        +--> chronologies
        +--> commitments
        +--> issue analysis
        +--> contextual Q&A
```

The prototype should establish the first credible path toward that vision without pretending the full production system already exists.
