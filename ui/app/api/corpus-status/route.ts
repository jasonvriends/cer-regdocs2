import { getCorpusStatus } from "@/lib/corpus-status";
import { reportServerError, withErrorReference } from "@/lib/observability";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const status = await getCorpusStatus();
    return Response.json(status, {
      headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" },
    });
  } catch (error) {
    const errorId = reportServerError("api.corpus-status", error);
    return Response.json(
      { error: withErrorReference("Corpus status could not be loaded.", errorId), errorId },
      { status: 502 },
    );
  }
}
