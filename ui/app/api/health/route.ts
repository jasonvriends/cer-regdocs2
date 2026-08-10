export const runtime = "nodejs";

export async function GET() {
  const endpointConfigured = Boolean(process.env.AZURE_SEARCH_ENDPOINT?.trim());
  const apiKeyConfigured = Boolean(process.env.AZURE_SEARCH_API_KEY?.trim());
  const indexName = process.env.AZURE_SEARCH_INDEX?.trim() || "regdocs-chunks";
  const configured = endpointConfigured && apiKeyConfigured;

  return Response.json(
    {
      service: "regdocs-atlas-ui",
      status: configured ? "ok" : "configuration_required",
      azureSearch: {
        endpointConfigured,
        apiKeyConfigured,
        indexName,
      },
    },
    { status: configured ? 200 : 503 },
  );
}
