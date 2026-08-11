export const runtime = "nodejs";

export async function GET() {
  const endpointConfigured = Boolean(process.env.AZURE_SEARCH_ENDPOINT?.trim());
  const apiKeyConfigured = Boolean(process.env.AZURE_SEARCH_API_KEY?.trim());
  const indexName =
    process.env.AZURE_SEARCH_INDEX?.trim() ||
    process.env.AZURE_SEARCH_INDEX_NAME?.trim() ||
    "regdocs-chunks";
  const authentication = apiKeyConfigured ? "api_key" : "managed_identity";
  const retrievalMode = process.env.AZURE_SEARCH_RETRIEVAL_MODE?.trim() || "lexical";
  const foundryEndpointConfigured = Boolean(process.env.FOUNDRY_PROJECT_ENDPOINT?.trim());
  const foundryModelConfigured = Boolean(process.env.FOUNDRY_MODEL_DEPLOYMENT?.trim());
  const embeddingModelConfigured = Boolean(process.env.FOUNDRY_EMBEDDING_DEPLOYMENT?.trim());
  const localIntelligenceConfigured = Boolean(process.env.REGDOCS_INTELLIGENCE_DIR?.trim());

  return Response.json(
    {
      service: "regdocs-atlas-ui",
      status: endpointConfigured ? "ok" : "configuration_required",
      azureSearch: {
        endpointConfigured,
        authentication,
        indexName,
        retrievalMode,
        semanticConfiguration:
          process.env.AZURE_SEARCH_SEMANTIC_CONFIG?.trim() || "regdocs-semantic",
        intelligenceIndexes: {
          entities: process.env.AZURE_SEARCH_ENTITIES_INDEX?.trim() || "regdocs-entities",
          relations: process.env.AZURE_SEARCH_RELATIONS_INDEX?.trim() || "regdocs-relations",
          events: process.env.AZURE_SEARCH_EVENTS_INDEX?.trim() || "regdocs-events",
        },
      },
      foundry: {
        endpointConfigured: foundryEndpointConfigured,
        modelConfigured: foundryModelConfigured,
        embeddingModelConfigured,
        authentication: process.env.FOUNDRY_API_KEY?.trim() ? "api_key" : "managed_identity",
        askReady: foundryEndpointConfigured && foundryModelConfigured,
        hybridReady: foundryEndpointConfigured && embeddingModelConfigured,
      },
      intelligence: {
        source: endpointConfigured ? "azure_search" : localIntelligenceConfigured ? "local_jsonl" : "default_local_jsonl",
      },
    },
    { status: endpointConfigured ? 200 : 503 },
  );
}
