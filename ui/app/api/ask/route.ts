import { createHash } from "node:crypto";
import { answerWithFoundry, streamWithFoundry, validCitations } from "@/lib/foundry";
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

const RATE_WINDOW_MS = 60_000;
const RATE_LIMIT = 30;
const rateBuckets = new Map<string, { started: number; count: number }>();

function requesterAddress(request: Request) {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    || request.headers.get("x-real-ip")?.trim()
    || "local";
}

function allowRequest(key: string) {
  const now = Date.now();
  if (rateBuckets.size > 10_000) {
    for (const [candidate, bucket] of rateBuckets) {
      if (now - bucket.started >= RATE_WINDOW_MS) rateBuckets.delete(candidate);
    }
  }
  const current = rateBuckets.get(key);
  if (!current || now - current.started >= RATE_WINDOW_MS) {
    rateBuckets.set(key, { started: now, count: 1 });
    return true;
  }
  current.count += 1;
  return current.count <= RATE_LIMIT;
}

function safetyIdentifier(request: Request) {
  const salt = process.env.FOUNDRY_SAFETY_SALT?.trim();
  if (!salt) return undefined;
  return createHash("sha256").update(`${salt}\0${requesterAddress(request)}`).digest("hex");
}

function ndjson(value: unknown) {
  return `${JSON.stringify(value)}\n`;
}

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
  if (!allowRequest(requesterAddress(request))) {
    return Response.json({ error: "Too many Ask requests; try again shortly." }, { status: 429 });
  }
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

    const common = {
      scope: workspaceChunkIds.length ? "workspace" : "corpus",
      retrievalMode: workspaceChunkIds.length ? "exact-workspace" : requestedMode,
      evidenceCount: evidence.length,
      coverage: { primaryStart: "2026-01-01", primaryEnd: "2026-07-31" },
    };
    if (request.headers.get("accept")?.includes("application/x-ndjson")) {
      const streamed = await streamWithFoundry(question, evidence, safetyIdentifier(request), request.signal);
      const encoder = new TextEncoder();
      const body = new ReadableStream({
        async start(controller) {
          let answer = "";
          try {
            for await (const event of streamed.events) {
              if (event.type !== "response.output_text.delta") continue;
              answer += event.delta;
              controller.enqueue(encoder.encode(ndjson({ type: "delta", delta: event.delta })));
            }
            const citations = validCitations(answer, streamed.citations);
            if (!answer.trim()) throw new Error("Microsoft Foundry returned an empty answer");
            if (!citations.length) throw new Error("Microsoft Foundry returned an answer without valid evidence citations");
            controller.enqueue(encoder.encode(ndjson({ type: "citations", citations })));
            controller.enqueue(encoder.encode(ndjson({ type: "done", model: streamed.model, ...common })));
          } catch (error) {
            console.error("REGDOCS grounded answer stream failed", error);
            controller.enqueue(encoder.encode(ndjson({ type: "error", error: "The grounded answer stream could not be completed." })));
          } finally {
            controller.close();
          }
        },
      });
      return new Response(body, {
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": "application/x-ndjson; charset=utf-8",
        },
      });
    }

    const result = await answerWithFoundry(question, evidence, safetyIdentifier(request));
    return Response.json({
      ...result,
      ...common,
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
