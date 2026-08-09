import { searchRegdocs } from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const query = url.searchParams.get("q")?.trim() ?? "";
  const requestedTop = Number.parseInt(url.searchParams.get("top") ?? "20", 10);
  const top = Number.isFinite(requestedTop) ? Math.min(Math.max(requestedTop, 1), 50) : 20;

  if (!query) {
    return Response.json({ error: "Query parameter 'q' is required." }, { status: 400 });
  }

  try {
    const results = await searchRegdocs(query, top);
    return Response.json({ query, count: results.length, results });
  } catch (error) {
    console.error("REGDOCS search failed", error);
    const message = error instanceof Error ? error.message : "Search failed";
    const configurationError = message.includes("AZURE_SEARCH_ENDPOINT");

    return Response.json(
      {
        error: configurationError
          ? "Azure AI Search is not configured for this Atlas instance."
          : "Azure AI Search request failed.",
      },
      { status: configurationError ? 503 : 502 },
    );
  }
}
