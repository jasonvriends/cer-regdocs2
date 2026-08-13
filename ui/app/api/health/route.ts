export const runtime = "nodejs";

export async function GET() {
  const endpointConfigured = Boolean(process.env.AZURE_SEARCH_ENDPOINT?.trim());
  const apiKeyConfigured = Boolean(process.env.AZURE_SEARCH_API_KEY?.trim());
  const indexName =
    process.env.AZURE_SEARCH_INDEX?.trim() ||
    process.env.AZURE_SEARCH_INDEX_NAME?.trim() ||
    "regdocs-chunks";
  const authentication = apiKeyConfigured ? "api_key" : "managed_identity";
  const vectorField = process.env.AZURE_SEARCH_VECTOR_FIELD?.trim();
  const semanticConfiguration = process.env.AZURE_SEARCH_SEMANTIC_CONFIGURATION?.trim();
  const foundryEndpointConfigured = Boolean(process.env.FOUNDRY_PROJECT_ENDPOINT?.trim());
  const foundryModelConfigured = Boolean(process.env.FOUNDRY_MODEL_DEPLOYMENT?.trim());
  const intelligenceIndexes = {
    entities: process.env.AZURE_SEARCH_ENTITIES_INDEX?.trim() || "regdocs-entities",
    relations: process.env.AZURE_SEARCH_RELATIONS_INDEX?.trim() || "regdocs-relations",
    events: process.env.AZURE_SEARCH_EVENTS_INDEX?.trim() || "regdocs-events",
  };

  return Response.json(
    {
      service: "regdocs-atlas-ui",
      status: endpointConfigured ? "ok" : "configuration_required",
      azureSearch: {
        endpointConfigured,
        authentication,
        indexName,
        hybridConfigured: Boolean(vectorField),
        semanticConfigured: Boolean(semanticConfiguration),
      },
      foundry: {
        configured: foundryEndpointConfigured && foundryModelConfigured,
        projectEndpointConfigured: foundryEndpointConfigured,
        modelDeploymentConfigured: foundryModelConfigured,
        safetyIdentifierConfigured: Boolean(process.env.FOUNDRY_SAFETY_SALT?.trim()),
      },
      intelligence: {
        indexes: intelligenceIndexes,
        localFallbackConfigured: Boolean(process.env.REGDOCS_INTELLIGENCE_DIR?.trim()),
      },
    },
    { status: endpointConfigured ? 200 : 503 },
  );
}
