import { getClaims, hasIntelligenceScope, type IntelligenceScope } from "@/lib/intelligence";
import { publicErrorResponse } from "@/lib/observability";

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
      { error: "Claims requests require a document, filing, company, or project scope." },
      { status: 400 },
    );
  }
  const requestedTop = Number.parseInt(url.searchParams.get("top") || "200", 10);
  try {
    const claims = await getClaims(requestedScope, Number.isFinite(requestedTop) ? requestedTop : 200);
    return Response.json({ count: claims.length, claims });
  } catch (error) {
    return publicErrorResponse("api.claims", error, "Claims retrieval failed.", 502, requestedScope);
  }
}
