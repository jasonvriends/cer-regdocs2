# REGDOCS Atlas UI

Phase 1 web application for the REGDOCS Atlas research workbench.

The UI is deliberately separate from the Python acquisition/processing pipeline. The pipeline remains responsible for Stages 1–5; this application consumes the published Azure AI Search index and, in later iterations, source files/provenance and Microsoft Foundry.

## Current scope

Implemented scaffold:

- full-stack Next.js/TypeScript application under `ui/`;
- three-pane Search / Evidence / Ask workbench;
- server-side Azure AI Search route at `GET /api/search?q=...`;
- `DefaultAzureCredential` authentication so local `az login` and Azure managed identity use the same code path;
- Stage 5 field mapping for chunk/document/page/source identity;
- health endpoint at `GET /api/health`;
- Ask surface reserved but intentionally disabled until the grounded Foundry path exists.

Not implemented yet:

- original PDF/blob delivery;
- page renderer and Stage 4 polygon highlights;
- search facets/filters;
- Foundry answer generation;
- assistant-ui and Extend UI integration;
- user identity, saved research, or Entra ID sign-in.

## Local development

Prerequisites:

- Node.js 22 or newer;
- an Azure AI Search index produced by Stage 5;
- Azure CLI authentication with data-plane read access to the Search service.

From the repository root:

```bash
cd ui
npm install
cp .env.local.example .env.local
az login
npm run dev
```

Set the real Search endpoint in `.env.local`:

```text
AZURE_SEARCH_ENDPOINT=https://<service-name>.search.windows.net
AZURE_SEARCH_INDEX=regdocs-chunks
```

The authenticated identity needs the Azure AI Search **Search Index Data Reader** role for the target service/index.

Then open the local Next.js URL and search terms that exist in the Stage 5 corpus.

## Verification

```bash
cd ui
npm run typecheck
npm run build
```

With the app running:

```bash
curl http://localhost:3000/api/health
curl 'http://localhost:3000/api/search?q=caribou&top=5'
```

The search route intentionally stays server-side. Azure credentials and Search SDK calls must not be shipped into the browser.

## Azure deployment direction

The intended Phase 1 host is Azure App Service running Node.js. Use a system-assigned managed identity and grant that identity Search Index Data Reader. No user login is required for Phase 1.

Later phases can layer Microsoft Entra ID user authentication in front of the same application without changing the Search data-plane credential model.

## Next UI milestones

1. Add filters/facets and better result snippets.
2. Serve the original source PDF/page through an Atlas route or blob-backed URL.
3. Add the document viewer and transform Stage 4 page polygons into clickable/highlightable overlays.
4. Make result/citation clicks navigate to the exact document page and evidence region.
5. Add the bounded Ask route through Foundry, then adopt assistant-ui for streaming/tool/citation rendering.
6. Evaluate Extend UI for the document primitives before writing custom PDF controls; retain PDF.js + SVG overlays as the fallback.
