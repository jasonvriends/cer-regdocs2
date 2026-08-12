import { getDocumentView } from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function nonNegativeInt(raw: string | null) {
  if (raw === null || raw === "") return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : undefined;
}

function positiveInt(raw: string | null) {
  const parsed = nonNegativeInt(raw);
  return parsed && parsed > 0 ? parsed : undefined;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const documentId = url.searchParams.get("documentId")?.trim();
  if (!documentId) {
    return Response.json({ error: "A documentId is required." }, { status: 400 });
  }

  try {
    const response = await getDocumentView({
      documentId,
      offset: nonNegativeInt(url.searchParams.get("offset")),
      top: positiveInt(url.searchParams.get("top")),
      page: positiveInt(url.searchParams.get("page")),
    });
    if (!response.pageFound) {
      return Response.json(
        { ...response, error: `Page ${response.requestedPage} was not found in this document.` },
        { status: 404 },
      );
    }
    return Response.json(response);
  } catch (error) {
    console.error("REGDOCS document view failed", error);
    const message = error instanceof Error ? error.message : "Document view failed";
    const configurationError = message.includes("AZURE_SEARCH_ENDPOINT");
    return Response.json(
      {
        error: configurationError
          ? "Azure AI Search is not configured for this Atlas instance."
          : "The document view could not be loaded.",
      },
      { status: configurationError ? 503 : 502 },
    );
  }
}
