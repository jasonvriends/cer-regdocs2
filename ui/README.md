# REGDOCS Atlas UI

REGDOCS Atlas is the web research workbench for the Stage 5 Azure AI Search index and Stage 6 regulatory-intelligence artifacts.

The Python pipeline remains responsible for acquiring, analyzing, normalizing, and publishing REGDOCS data. The `ui/` application is a separate Next.js application that reads the published `regdocs-chunks` index through server-side API routes.

## What is implemented

The current workbench includes:

- full-stack Next.js/TypeScript application under `ui/`;
- BERDI-inspired discovery home with purpose-led entry points;
- three-pane Search / Source / Research Workspace layout;
- an explicit corpus coverage dashboard with count definitions;
- a Data Products catalogue and Schedule A pilot path;
- server-side Azure AI Search access using App Service managed identity or a local query key;
- hybrid keyword/vector retrieval with optional semantic reranking on a vector-enabled index;
- Microsoft Foundry grounded answers over either active search filters or exact Workspace passages;
- streamed grounded answers with validated page-level citations;
- deterministic document/page citation cards for every source marker returned by the model;
- scoped regulatory timelines that distinguish filing dates from occurrence dates;
- scoped relationship graphs for organizations, projects, filings, and documents;
- evidence links from timeline and graph records back into the active source viewer;
- persistent System, Light, and Dark appearance selection;
- no Azure Search credentials shipped to the browser;
- global corpus search;
- Filing Dossier, Company, Project, and Document X-Ray search lenses;
- What Happened, Tables, Figures, Red Flags, and Obligations retrieval presets;
- exact filing ID, filing number, document ID, company, project, and page filters;
- Azure Search facets for companies, projects, document types, roles, commodities, application types, chunk types, and file types;
- total result counts;
- relevance, filing-date, and chunk-order sorting;
- Research Workspace collection and within-workspace search for the current browser session;
- CSV export of collected passages with document, page, and source identity;
- page-like HTML document reconstruction that opens around a selected search hit;
- automatic match scrolling and query-term highlighting;
- structured HTML rendering for extracted text, tables, and figure labels;
- jump-to-page and windowed previous/next navigation for very large documents;
- health endpoint at `GET /api/health`.

Hybrid and Ask are configuration-gated: keyword search continues to work against the existing lexical index, while hybrid retrieval requires the versioned vector index and Ask requires a Foundry project/model deployment. See [`PRODUCT.md`](PRODUCT.md) for the capability contract, evaluation requirements, and Schedule A approach.

## Required configuration

The endpoint and index name select the Azure AI Search index:

```text
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_INDEX=regdocs-chunks-hybrid
AZURE_SEARCH_VECTOR_FIELD=content_vector
AZURE_SEARCH_SEMANTIC_CONFIGURATION=regdocs-semantic
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT=<chat-model-deployment>
AZURE_SEARCH_ENTITIES_INDEX=regdocs-entities
AZURE_SEARCH_RELATIONS_INDEX=regdocs-relations
AZURE_SEARCH_EVENTS_INDEX=regdocs-events
```

Authentication is chosen automatically:

- In Azure App Service, leave `AZURE_SEARCH_API_KEY` unset and grant the web app's managed identity the read-only `Search Index Data Reader` role.
- For local development, either sign in with Azure CLI and use an identity that has `Search Index Data Reader`, or set `AZURE_SEARCH_API_KEY` to a read-only query key.

An admin key works locally but grants permissions the UI does not need. Never put a key in a variable beginning with `NEXT_PUBLIC_`; Next.js exposes `NEXT_PUBLIC_*` values to browser code.

`AZURE_SEARCH_VECTOR_FIELD` enables hybrid requests. `AZURE_SEARCH_SEMANTIC_CONFIGURATION` is optional; when set, relevant hybrid queries are reranked using that index configuration. Foundry calls always use `DefaultAzureCredential`. Grant the runtime identity permission to invoke the deployed model in the Foundry project.

Timeline and Relationship graph read the three Stage 6 indexes. For local previews without those indexes, set `REGDOCS_INTELLIGENCE_DIR` to a completed Stage 6 output directory. `FOUNDRY_SAFETY_SALT` is recommended for multi-user deployments; Atlas hashes it with the requester address and sends only the derived identifier to Foundry.

## Publish the hybrid index

The existing `regdocs-chunks` index is deliberately preserved. The publisher defaults to a separate `regdocs-chunks-hybrid` index and refuses to write to the lexical production name.

Start with a dry run and a small pilot:

```bash
python tools/publish_hybrid_index.py --dry-run --limit 100

export AZURE_SEARCH_ENDPOINT="https://YOUR-SERVICE.search.windows.net"
export AZURE_SEARCH_ADMIN_KEY="YOUR-SEARCH-ADMIN-KEY"
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com"
export AZURE_OPENAI_API_KEY="YOUR-EMBEDDING-KEY"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT="text-embedding-3-small"

python tools/publish_hybrid_index.py --limit 100
```

Validate the pilot, then run without `--limit` to idempotently publish the complete normalized corpus. Embeddings and uploads are batched; rerunning uses stable Search keys and merges the same chunks. Use a new versioned index name when changing the embedding model or dimensions.

The publisher also persists vectors under `workspace/5_index/embedding-cache.sqlite`. If embedding generation or upload is interrupted, repeat the same command; completed embedding calls are reused instead of billed again. Keep the cache until the full index passes `tools/verify_ai_deployment.py`.

For keyless embedding generation, omit `AZURE_OPENAI_API_KEY` and authenticate with Azure Identity. For keyless query vectorization, grant the Search service's managed identity access to the embedding deployment. If the Search service cannot call the deployment with managed identity, set `AZURE_OPENAI_VECTORIZER_API_KEY` only in the publisher environment; Azure stores it in the index vectorizer configuration.

For the normal production path, upload `workspace/` and `database/` to an
existing private Blob container with SAS, then run [`deploy/`](deploy/) from
Azure Cloud Shell. It provisions Search, Foundry, App Service, RBAC, index
publication, verification, and UI deployment, using the same Blob container
for input, the resumable embedding cache, and the App Service ZIP.

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
curl 'http://localhost:3000/api/search?q=effects%20on%20caribou&mode=hybrid&top=5'
curl 'http://localhost:3000/api/search?q=*&chunkType=table&top=5'
curl 'http://localhost:3000/api/document-view?documentId=<document-id>&page=42'
curl 'http://localhost:3000/api/timeline?documentId=<document-id>'
curl 'http://localhost:3000/api/graph?filingId=<filing-id>'
curl -X POST http://localhost:3000/api/ask -H 'content-type: application/json' \
  -d '{"question":"What mitigation was proposed for caribou?","searchMode":"hybrid"}'
```

A configured health response looks like:

```json
{
  "service": "regdocs-atlas-ui",
  "status": "ok",
  "azureSearch": {
    "endpointConfigured": true,
        "authentication": "managed_identity",
        "indexName": "regdocs-chunks-hybrid",
        "hybridConfigured": true,
        "semanticConfigured": true
  },
  "foundry": { "configured": true }
}
```

The health response never includes an API key.

## Azure App Service runtime

Use a Linux App Service with Node.js 24 LTS and the startup command `node server.js`. Configure:

```text
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_SEARCH_VECTOR_FIELD
AZURE_SEARCH_SEMANTIC_CONFIGURATION
FOUNDRY_PROJECT_ENDPOINT
FOUNDRY_MODEL_DEPLOYMENT
FOUNDRY_SAFETY_SALT
AZURE_SEARCH_ENTITIES_INDEX
AZURE_SEARCH_RELATIONS_INDEX
AZURE_SEARCH_EVENTS_INDEX
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
/api/search?q=impacts%20to%20traditional%20land%20use&mode=hybrid
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
- `mode=keyword|hybrid`

Facet/filter parameters can be repeated to request multiple values.

## HTML document viewer

The browser does not embed or reproduce the source PDF. `GET /api/document-view` retrieves an ordered window of normalized chunks around a requested page. The UI groups those chunks into page-like sheets, renders tab-delimited table chunks as HTML tables, labels extracted figure text, scrolls to the selected search passage, and highlights query terms.

The view is optimized for reading, searching, accessibility, and evidence collection. It does not claim pixel fidelity: columns, exact line breaks, handwriting, images, and page geometry can differ from the original. Users can always open the authoritative source in REGDOCS.

## Grounded Ask architecture

`POST /api/ask` is a controlled retrieval-augmented generation route. For corpus questions, the server retrieves up to 12 passages from Azure AI Search using the active filters and selected keyword/hybrid mode. For Workspace questions, it refetches the exact chunk IDs from Search, so browser-supplied passage text is never trusted. Only those passages are sent to the Foundry model. The active UI requests NDJSON streaming and progressively renders the response; JSON remains available to non-streaming API clients.

The prompt requires a source marker such as `[S1]` on every factual claim, treats retrieved document text as untrusted content, prohibits outside knowledge, and asks the model to disclose insufficient evidence. The server maps markers back to fixed document/page/source metadata; the model cannot invent the citation cards. Important conclusions must still be verified against the authoritative REGDOCS source.

## Current boundary

Hybrid retrieval, grounded Ask, deterministic relationship graphs, and filing chronology are implemented but require Azure deployment configuration and CER-specific evaluation. Model-derived Stage 6 records remain explicitly labeled and unreviewed unless promoted through the pipeline. Find Similar, Filing DNA, contradiction analysis, a human review workbench, and reviewed Schedule A datasets remain later capabilities.

The next source-viewer milestone is optionally serving an original page image beside the HTML reconstruction and applying Stage 4 provenance polygons to the source image. The HTML view remains the accessible reading surface.
