import {
  authorizedDiagnosticsToken,
  diagnosticsTokenConfigured,
  queryErrorFromLogAnalytics,
  validErrorId,
} from "@/lib/observability";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (!diagnosticsTokenConfigured()) {
    return Response.json({ error: "Operator error lookup is not configured." }, { status: 503 });
  }

  const authorization = request.headers.get("authorization");
  const bearer = authorization?.startsWith("Bearer ") ? authorization.slice(7).trim() : null;
  if (!authorizedDiagnosticsToken(bearer)) {
    return Response.json({ error: "Operator authorization is required." }, { status: 401 });
  }

  const errorId = new URL(request.url).searchParams.get("errorId")?.trim().toUpperCase() || "";
  if (!validErrorId(errorId)) {
    return Response.json({ error: "Enter a valid Atlas error reference." }, { status: 400 });
  }

  try {
    const errors = await queryErrorFromLogAnalytics(errorId);
    return Response.json({ errorId, count: errors.length, errors });
  } catch (error) {
    console.error("REGDOCS diagnostics error lookup failed", error);
    return Response.json(
      { error: error instanceof Error ? error.message : "Log Analytics lookup failed." },
      { status: 502 },
    );
  }
}
