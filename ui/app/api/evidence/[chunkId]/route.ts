import { getRegdocsChunk } from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ chunkId: string }> },
) {
  const { chunkId } = await context.params;
  if (!chunkId.trim()) return Response.json({ error: "Chunk ID is required." }, { status: 400 });
  try {
    const result = await getRegdocsChunk(chunkId);
    return result
      ? Response.json(result)
      : Response.json({ error: "Evidence chunk not found." }, { status: 404 });
  } catch (error) {
    console.error("REGDOCS evidence lookup failed", error);
    return Response.json({ error: "Evidence lookup failed." }, { status: 502 });
  }
}
