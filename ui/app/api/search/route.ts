import { searchRegdocs, type AtlasSearchSort } from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SORTS = new Set<AtlasSearchSort>(["relevance", "newest", "oldest", "chunk"]);
const RETRIEVAL_MODES = new Set(["lexical", "semantic", "hybrid"] as const);

function values(url: URL, name: string) {
  return url.searchParams.getAll(name).map((value) => value.trim()).filter(Boolean);
}

function positiveInt(raw: string | null) {
  if (!raw) return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const query = url.searchParams.get("q")?.trim() || "*";
  const requestedTop = positiveInt(url.searchParams.get("top")) ?? 20;
  const top = Math.min(requestedTop, 50);
  const requestedSort = (url.searchParams.get("sort")?.trim() || "relevance") as AtlasSearchSort;
  const sort = SORTS.has(requestedSort) ? requestedSort : "relevance";
  const requestedRetrievalMode = url.searchParams.get("retrievalMode")?.trim();
  const retrievalMode = RETRIEVAL_MODES.has(
    requestedRetrievalMode as "lexical" | "semantic" | "hybrid",
  )
    ? (requestedRetrievalMode as "lexical" | "semantic" | "hybrid")
    : undefined;

  try {
    const response = await searchRegdocs({
      query,
      top,
      sort,
      retrievalMode,
      page: positiveInt(url.searchParams.get("page")),
      documentIds: values(url, "documentId"),
      filingIds: values(url, "filingId"),
      filingNumbers: values(url, "filingNumber"),
      companies: values(url, "company"),
      projects: values(url, "project"),
      chunkTypes: values(url, "chunkType"),
      applicationTypes: values(url, "applicationType"),
      commodities: values(url, "commodity"),
      documentTypes: values(url, "documentType"),
      fileTypes: values(url, "fileType"),
      roles: values(url, "role"),
    });

    return Response.json(response);
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
