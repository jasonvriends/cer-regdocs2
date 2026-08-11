# REGDOCS Atlas UI

REGDOCS Atlas is the web research workbench for the Stage 5 Azure AI Search index.

The Python pipeline remains responsible for acquiring, analyzing, normalizing, and publishing REGDOCS data. The `ui/` application is a separate Next.js application that reads the published `regdocs-chunks` index through server-side API routes.

## What is implemented

The current workbench includes:

- full-stack Next.js/TypeScript application under `ui/`;
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
- health endpoint at `GET /api/health`.

The Ask surface remains intentionally disabled until the grounded Microsoft Foundry route is implemented.

## Required configuration

The endpoint and index name select the Azure AI Search index:

```text
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_INDEX=regdocs-chunks
```

Authentication is chosen automatically:

- In Azure App Service, leave `AZURE_SEARCH_API_KEY` unset and grant the web app's managed identity the read-only `Search Index Data Reader` role.
- For local development, either sign in with Azure CLI and use an identity that has `Search Index Data Reader`, or set `AZURE_SEARCH_API_KEY` to a read-only query key.

An admin key works locally but grants permissions the UI does not need. Never put a key in a variable beginning with `NEXT_PUBLIC_`; Next.js exposes `NEXT_PUBLIC_*` values to browser code.

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
        "authentication": "managed_identity",
        "indexName": "regdocs-chunks"
  }
}
```

The health response never includes an API key.

## Azure App Service runtime

Use a Linux App Service with Node.js 24 LTS and the startup command `node server.js`. Configure:

```text
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
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
