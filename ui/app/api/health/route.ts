export const runtime = "nodejs";

export async function GET() {
  const endpointConfigured = Boolean(process.env.AZURE_SEARCH_ENDPOINT?.trim());
  const apiKeyConfigured = Boolean(process.env.AZURE_SEARCH_API_KEY?.trim());
  const indexName =
    process.env.AZURE_SEARCH_INDEX?.trim() ||
    process.env.AZURE_SEARCH_INDEX_NAME?.trim() ||
    "regdocs-chunks";
  const authentication = apiKeyConfigured ? "api_key" : "managed_identity";

  return Response.json(
    {
      service: "regdocs-atlas-ui",
      status: endpointConfigured ? "ok" : "configuration_required",
      azureSearch: {
        endpointConfigured,
        authentication,
        indexName,
      },
    },
    { status: endpointConfigured ? 200 : 503 },
  );
}
