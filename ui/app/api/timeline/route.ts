import { getTimeline, hasIntelligenceScope, type IntelligenceScope } from "@/lib/intelligence";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function scope(url: URL): IntelligenceScope {
  return {
    documentId: url.searchParams.get("documentId")?.trim() || undefined,
    filingId: url.searchParams.get("filingId")?.trim() || undefined,
    filingNumber: url.searchParams.get("filingNumber")?.trim() || undefined,
    company: url.searchParams.get("company")?.trim() || undefined,
    project: url.searchParams.get("project")?.trim() || undefined,
  };
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const requestedScope = scope(url);
  if (!hasIntelligenceScope(requestedScope)) {
    return Response.json(
      { error: "Timeline requests require a document, filing, company, or project scope." },
      { status: 400 },
    );
  }
  const requestedTop = Number.parseInt(url.searchParams.get("top") || "200", 10);
  try {
    const events = await getTimeline(requestedScope, Number.isFinite(requestedTop) ? requestedTop : 200);
    return Response.json({ count: events.length, events });
  } catch (error) {
    console.error("REGDOCS timeline failed", error);
    return Response.json({ error: "Timeline retrieval failed." }, { status: 502 });
  }
}
