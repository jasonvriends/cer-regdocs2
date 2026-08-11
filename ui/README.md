# REGDOCS Atlas UI

REGDOCS Atlas is the web research workbench for the Stage 5 Azure AI Search indexes and Stage 6 regulatory-intelligence artifacts.

The Python pipeline remains responsible for acquiring, analyzing, normalizing, and publishing REGDOCS data. The `ui/` application is a separate Next.js application that reads the published `regdocs-chunks` index through server-side API routes.

## What is implemented

The current workbench includes:

- full-stack Next.js/TypeScript application under `ui/`;
- persistent System / Light / Dark appearance selection;
- three-pane Search / Evidence / Ask-Analyze layout;
- server-side Azure AI Search access using App Service managed identity or a local query key;
- no Azure Search credentials shipped to the browser;
- global corpus search;
- Filing Dossier, Company, Project, and Document X-Ray search lenses;
- What Happened, Tables, Figures, Red Flags, and Obligations retrieval presets;
- exact filing ID, filing number, document ID, company, project, and page filters;
- Azure Search facets for companies, projects, document types, roles, commodities, application types, chunk types, and file types;
- total result counts;
- relevance, filing-date, and chunk-order sorting;
- evidence pinning for the current browser session;
- normalized document/page evidence viewer;
- document, regulatory timeline, and relationship-graph research views;
- scoped deterministic filing chronology and regulatory entity relationships;
- grounded Ask streaming through Microsoft Foundry Responses with validated `chunk_id` citations;
- lexical, semantic, or hybrid retrieval, with query embeddings generated server-side;
- health endpoint at `GET /api/health`.

## Required configuration

The endpoint and index name select the Azure AI Search index:

```text
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_INDEX=regdocs-current
AZURE_SEARCH_RETRIEVAL_MODE=hybrid
AZURE_SEARCH_ENTITIES_INDEX=regdocs-entities
AZURE_SEARCH_RELATIONS_INDEX=regdocs-relations
AZURE_SEARCH_EVENTS_INDEX=regdocs-events
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT=<responses-model-deployment>
FOUNDRY_EMBEDDING_DEPLOYMENT=<embedding-deployment>
```

`FOUNDRY_SAFETY_SALT` is optional but recommended for a multi-user deployment; the server uses it to hash the requester address into a privacy-preserving Responses API safety identifier. Ask also applies a process-local burst limit, which should be supplemented by the platform gateway for a scaled deployment.

Authentication is chosen automatically for both Azure AI Search and Foundry:

- In Azure App Service, leave `AZURE_SEARCH_API_KEY` and `FOUNDRY_API_KEY` unset. Grant the web app managed identity `Search Index Data Reader` and the appropriate Foundry project inference role.
- For local development, either sign in with Azure CLI and use an identity with those roles, or set read-only/inference keys in `.env.local`.

An Azure Search admin key works locally but grants permissions the UI does not need. Never put a key in a variable beginning with `NEXT_PUBLIC_`; Next.js exposes `NEXT_PUBLIC_*` values to browser code.

The graph and timeline APIs use Azure Search when `AZURE_SEARCH_ENDPOINT` is configured. For a local preview, set `REGDOCS_INTELLIGENCE_DIR` to a Stage 6 output directory; the default fallback is `../workspace/6_enrich` relative to `ui/`.

## Local development

Prerequisites:

- Node.js 22 or newer;
- an Azure AI Search index produced by Stage 5.

From the repository root:

```bash
cd ui
npm install
cp .env.local.example .env.local
```

Edit `.env.local` with the real Search endpoint, index name, and (if needed) query key, then run:

```bash
npm run dev
```

Open the local Next.js URL.

## Verify before deployment

```bash
cd ui
npm install
npm run typecheck
npm run build
```

With the application running:

```bash
curl http://localhost:3000/api/health
curl 'http://localhost:3000/api/search?q=caribou&top=5&retrievalMode=hybrid'
curl 'http://localhost:3000/api/search?q=*&chunkType=table&top=5'
curl 'http://localhost:3000/api/timeline?documentId=<document-id>'
curl 'http://localhost:3000/api/graph?filingId=<filing-id>'
```

A configured health response looks like:

```json
{
  "service": "regdocs-atlas-ui",
  "status": "ok",
  "azureSearch": {
    "endpointConfigured": true,
        "authentication": "managed_identity",
    "indexName": "regdocs-current",
    "retrievalMode": "hybrid"
  },
  "foundry": {
    "endpointConfigured": true,
    "modelConfigured": true,
    "embeddingModelConfigured": true,
    "askReady": true,
    "hybridReady": true
  }
}
```

The health response never includes an API key.

## Azure App Service runtime

Use a Linux App Service with Node.js 24 LTS and the startup command `node server.js`. Configure:

```text
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_SEARCH_RETRIEVAL_MODE
AZURE_SEARCH_ENTITIES_INDEX
AZURE_SEARCH_RELATIONS_INDEX
AZURE_SEARCH_EVENTS_INDEX
FOUNDRY_PROJECT_ENDPOINT
FOUNDRY_MODEL_DEPLOYMENT
FOUNDRY_EMBEDDING_DEPLOYMENT
FOUNDRY_EMBEDDING_DIMENSIONS
```

Enable the web app's managed identity and assign it `Search Index Data Reader` on the Search service. No Python pipeline process is required in the web application.

The production build uses Next.js standalone output. A deployment package must contain the contents of `.next/standalone` at its root, including `server.js`, plus `.next/static` copied into `.next/standalone/.next/static`.

If searches return `403` after role propagation, confirm that Azure AI Search allows role-based access under **Settings > Keys**. If the Search service restricts public network access, the App Service also needs an allowed outbound path or private-network integration.

## Search API

The browser calls `GET /api/search`. The route builds Azure OData filters on the server instead of accepting arbitrary client-supplied filter expressions.

Examples:

```text
/api/search?q=pipeline%20abandonment
/api/search?q=*&company=Trans%20Mountain%20Pipeline%20ULC
/api/search?q=*&filingId=<filing-id>
/api/search?q=*&documentId=<document-id>&sort=chunk
/api/search?q=groundwater&documentId=<document-id>&page=42
/api/search?q=cost&chunkType=table
/api/search?q=route%20map&chunkType=figure
/api/search?q=investigation&role=Applicant&commodity=Oil
```

Supported query parameters are:

- `q`
- `top` (maximum 50)
- `sort=relevance|newest|oldest|chunk`
- `retrievalMode=lexical|semantic|hybrid`
- `page`
- `documentId`
- `filingId`
- `filingNumber`
- `company`
- `project`
- `chunkType`
- `applicationType`
- `commodity`
- `documentType`
- `fileType`
- `role`

Facet/filter parameters can be repeated to request multiple values.

## Current boundary

The timeline distinguishes deterministic filing dates from model-extracted occurrence dates. The graph contains deterministic document, filing, organization, and project relationships and can include explicitly merged model relationships. The pipeline now has a scoped, resumable structured-extraction pilot for claims, obligations, occurrence chronology, and relationships; its records remain `unreviewed` and are excluded from publishing unless deliberately merged. Contradiction analysis and a human-review workbench remain later enrichment milestones.

The source viewer resolves exact normalized chunks and pages. Serving original page imagery with Stage 4 provenance polygons remains a later viewer milestone.
