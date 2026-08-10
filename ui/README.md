# REGDOCS Atlas UI

REGDOCS Atlas is the web research workbench for the Stage 5 Azure AI Search index.

The Python pipeline remains responsible for acquiring, analyzing, normalizing, and publishing REGDOCS data. The `ui/` application is a separate Next.js application that reads the published `regdocs-chunks` index through server-side API routes.

## What is implemented

The current workbench includes:

- full-stack Next.js/TypeScript application under `ui/`;
- three-pane Search / Evidence / Ask-Analyze layout;
- server-side Azure AI Search access using an API key;
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
- health endpoint at `GET /api/health`.

The Ask surface remains intentionally disabled until the grounded Microsoft Foundry route is implemented.

## Required configuration

Only three server-side application settings are required:

```text
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_INDEX=regdocs-chunks
AZURE_SEARCH_API_KEY=<query-key-or-admin-key>
```

A Search query key is preferred because this application only reads the index. An admin key also works but grants permissions the UI does not need.

Do not put the key in a variable beginning with `NEXT_PUBLIC_`. Next.js exposes `NEXT_PUBLIC_*` values to browser code.

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

Edit `.env.local` with the real Search endpoint, index name, and key, then run:

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
curl 'http://localhost:3000/api/search?q=caribou&top=5'
curl 'http://localhost:3000/api/search?q=*&chunkType=table&top=5'
```

A configured health response looks like:

```json
{
  "service": "regdocs-atlas-ui",
  "status": "ok",
  "azureSearch": {
    "endpointConfigured": true,
    "apiKeyConfigured": true,
    "indexName": "regdocs-chunks"
  }
}
```

The API key is never included in the health response.

## Deploy to Azure App Service

Deploy the **contents of the `ui/` directory** as the Node.js application. The runtime must support Node.js 22 or newer.

Configure these App Service application settings:

```text
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_SEARCH_API_KEY
```

Use the normal production commands:

```bash
npm install
npm run build
npm start
```

No Python pipeline process is required in the web application. The web app only needs network access to the Azure AI Search service and the three settings above.

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

This UI uses the current Stage 5 lexical index. Semantic ranking, vectors, Find Similar, Filing DNA, structured relationship graphs, chronology extraction, contradiction analysis, claim ledgers, obligation extraction, and grounded Ask require later Stage 5/Foundry enhancements.

The next source-viewer milestone is serving the original document/page and applying Stage 4 provenance polygons so a search hit can jump to the exact highlighted region.
