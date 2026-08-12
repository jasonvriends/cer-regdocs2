import { answerWithFoundry } from "@/lib/foundry";
import {
  getChunksByIds,
  searchRegdocs,
  type AtlasSearchMode,
  type AtlasSearchRequest,
} from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type AskBody = {
  question?: unknown;
  workspaceChunkIds?: unknown;
  searchMode?: unknown;
  filters?: Partial<AtlasSearchRequest>;
};

function stringArray(value: unknown, limit = 20) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean))].slice(0, limit);
}

function safeFilters(input: Partial<AtlasSearchRequest> | undefined): Partial<AtlasSearchRequest> {
  if (!input || typeof input !== "object") return {};
  return {
    documentIds: stringArray(input.documentIds, 10),
    filingIds: stringArray(input.filingIds, 10),
    filingNumbers: stringArray(input.filingNumbers, 10),
    companies: stringArray(input.companies, 10),
    projects: stringArray(input.projects, 10),
    chunkTypes: stringArray(input.chunkTypes, 5),
    applicationTypes: stringArray(input.applicationTypes, 10),
    commodities: stringArray(input.commodities, 10),
    documentTypes: stringArray(input.documentTypes, 10),
    fileTypes: stringArray(input.fileTypes, 10),
    roles: stringArray(input.roles, 10),
    page: typeof input.page === "number" && Number.isInteger(input.page) && input.page > 0 ? input.page : undefined,
  };
}

export async function POST(request: Request) {
  let body: AskBody;
  try {
    body = await request.json() as AskBody;
  } catch {
    return Response.json({ error: "The request body must be valid JSON." }, { status: 400 });
  }

  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (question.length < 3 || question.length > 2000) {
    return Response.json({ error: "Enter a question between 3 and 2,000 characters." }, { status: 400 });
  }

  const workspaceChunkIds = stringArray(body.workspaceChunkIds, 20);
  const requestedMode: AtlasSearchMode = body.searchMode === "hybrid" ? "hybrid" : "keyword";

  try {
    const evidence = workspaceChunkIds.length
      ? await getChunksByIds(workspaceChunkIds)
      : (await searchRegdocs({
          query: question,
          top: 12,
          mode: requestedMode,
          sort: "relevance",
          ...safeFilters(body.filters),
        })).results;

    if (!evidence.length) {
      return Response.json({ error: "No CER evidence matched this question and scope." }, { status: 404 });
    }

    const result = await answerWithFoundry(question, evidence);
    return Response.json({
      ...result,
      scope: workspaceChunkIds.length ? "workspace" : "corpus",
      retrievalMode: workspaceChunkIds.length ? "exact-workspace" : requestedMode,
      evidenceCount: evidence.length,
      coverage: { primaryStart: "2026-01-01", primaryEnd: "2026-07-31" },
    });
  } catch (error) {
    console.error("REGDOCS grounded answer failed", error);
    const message = error instanceof Error ? error.message : "Grounded answer failed";
    const configurationError = message.includes("not configured");
    return Response.json(
      { error: configurationError ? "Microsoft Foundry or the requested search mode is not configured." : "The grounded answer could not be generated." },
      { status: configurationError ? 503 : 502 },
    );
  }
}
