import { createHash } from "node:crypto";
import {
  answerWithFoundry,
  streamWithFoundry,
  validCitations,
  type AtlasCitation,
} from "@/lib/foundry";
import {
  getChunksByIds,
  searchRegdocs,
  type AtlasSearchMode,
  type AtlasSearchRequest,
  type AtlasSearchResult,
} from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type AskBody = {
  question?: unknown;
  workspaceChunkIds?: unknown;
  filters?: Partial<AtlasSearchRequest>;
};

type RetrievalResult = {
  evidence: AtlasSearchResult[];
  mode: AtlasSearchMode;
  fallbackFrom?: AtlasSearchMode;
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

function msSince(started: number) {
  return Math.round(performance.now() - started);
}

function askTelemetry(value: Record<string, unknown>) {
  console.info("REGDOCS ask telemetry", JSON.stringify({ operation: "atlas.ask", ...value }));
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

function sourceTitle(item: AtlasSearchResult) {
  return item.heading || item.title || `Document ${item.document_id}`;
}

function evidenceCitations(evidence: AtlasSearchResult[]): AtlasCitation[] {
  return evidence.map((item, index) => ({
    id: `S${index + 1}`,
    chunkId: item.chunk_id,
    title: sourceTitle(item),
    documentId: item.document_id,
    filingNumber: item.filing_number ?? null,
    pageStart: item.page_start ?? null,
    pageEnd: item.page_end ?? null,
    sourceUrl: item.source_url ?? null,
    resolvedUrl: item.resolved_url ?? null,
    fileType: item.file_types?.[0] ?? null,
    excerpt: (item.content ?? "").replace(/\s+/g, " ").trim().slice(0, 320),
  }));
}

function preferredAskMode(question: string): AtlasSearchMode {
  const quotedPhrase = /["“][^"”]{3,}["”]/.test(question);
  const likelyRecordIdentifier = /\b[A-Z]{1,5}[- ]?\d{4,}\b/.test(question);
  return quotedPhrase || likelyRecordIdentifier ? "keyword" : "hybrid";
}

async function retrieveCorpusEvidence(
  question: string,
  filters: Partial<AtlasSearchRequest>,
): Promise<RetrievalResult> {
  const primaryMode = preferredAskMode(question);
  const secondaryMode: AtlasSearchMode = primaryMode === "hybrid" ? "keyword" : "hybrid";
  const searchRequest = {
    query: question,
    top: 12,
    sort: "relevance" as const,
    ...filters,
  };

  try {
    const primary = await searchRegdocs({ ...searchRequest, mode: primaryMode });
    if (primary.results.length) {
      return { evidence: primary.results, mode: primaryMode };
    }

    try {
      const secondary = await searchRegdocs({ ...searchRequest, mode: secondaryMode });
      return {
        evidence: secondary.results,
        mode: secondaryMode,
        fallbackFrom: primaryMode,
      };
    } catch (fallbackError) {
      console.warn("REGDOCS Ask secondary retrieval mode failed", fallbackError);
      return { evidence: [], mode: primaryMode };
    }
  } catch (primaryError) {
    if (primaryMode !== "hybrid") throw primaryError;
    console.warn("REGDOCS Ask hybrid retrieval failed; falling back to keyword", primaryError);
    const fallback = await searchRegdocs({ ...searchRequest, mode: "keyword" });
    return {
      evidence: fallback.results,
      mode: "keyword",
      fallbackFrom: "hybrid",
    };
  }
}

function retryableGroundingFailure(error: unknown) {
  const message = error instanceof Error ? error.message : "";
  return message.includes("empty answer") || message.includes("without valid evidence citations");
}

function userFacingStreamFailure(error: unknown) {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("without valid evidence citations")) {
    return {
      code: "citation_validation_failed",
      error: "I found relevant CER evidence, but the generated answer could not be verified against its citations. The retrieved sources are still shown below.",
    };
  }
  if (message.includes("empty answer")) {
    return {
      code: "empty_answer",
      error: "I found relevant CER evidence, but the answer service returned no usable synthesis. The retrieved sources are still shown below.",
    };
  }
  if (message.includes("not configured")) {
    return {
      code: "answer_service_not_configured",
      error: "I found relevant CER evidence, but the answer service is not configured correctly. The retrieved sources are still shown below.",
    };
  }
  return {
    code: "answer_service_failed",
    error: "I found relevant CER evidence, but the synthesized answer could not be completed. The retrieved sources are still shown below.",
  };
}

async function collectGroundedAnswer(
  question: string,
  evidence: AtlasSearchResult[],
  safetyId: string | undefined,
  signal: AbortSignal,
) {
  const streamed = await streamWithFoundry(question, evidence, safetyId, signal);
  let answer = "";
  for await (const event of streamed.events) {
    if (event.type === "response.output_text.delta") answer += event.delta;
  }
  if (!answer.trim()) throw new Error("Microsoft Foundry returned an empty answer");
  const citations = validCitations(answer, streamed.citations);
  if (!citations.length) {
    throw new Error("Microsoft Foundry returned an answer without valid evidence citations");
  }
  return { answer: answer.trim(), citations, model: streamed.model };
}

export async function POST(request: Request) {
  const requestStarted = performance.now();
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
  const retrievalStarted = performance.now();

  try {
    const retrieved: RetrievalResult = workspaceChunkIds.length
      ? { evidence: await getChunksByIds(workspaceChunkIds), mode: "keyword" }
      : await retrieveCorpusEvidence(question, safeFilters(body.filters));
    const retrievalMs = msSince(retrievalStarted);
    const evidence = retrieved.evidence;

    if (!evidence.length) {
      askTelemetry({
        status: "no_evidence",
        scope: workspaceChunkIds.length ? "workspace" : "corpus",
        retrievalMode: workspaceChunkIds.length ? "exact-workspace" : retrieved.mode,
        retrievalFallbackFrom: workspaceChunkIds.length ? null : retrieved.fallbackFrom ?? null,
        retrievalMs,
        totalMs: msSince(requestStarted),
        foundryUsed: false,
      });
      return Response.json({ error: "No CER evidence matched this question and scope." }, { status: 404 });
    }

    const retrievalMode = workspaceChunkIds.length ? "exact-workspace" : retrieved.mode;
    const retrievalFallbackFrom = workspaceChunkIds.length ? null : retrieved.fallbackFrom ?? null;
    const common = {
      scope: workspaceChunkIds.length ? "workspace" : "corpus",
      retrievalMode,
      retrievalFallbackFrom,
      evidenceCount: evidence.length,
      semanticApplied: retrievalMode === "hybrid" && Boolean(process.env.AZURE_SEARCH_SEMANTIC_CONFIGURATION?.trim()),
      coverage: { primaryStart: "2026-01-01", primaryEnd: "2026-07-31" },
    };
    const safetyId = safetyIdentifier(request);
    const configuredFoundryModel = process.env.FOUNDRY_MODEL_DEPLOYMENT?.trim() || null;

    if (request.headers.get("accept")?.includes("application/x-ndjson")) {
      const encoder = new TextEncoder();
      const sources = evidenceCitations(evidence);
      const streamBody = new ReadableStream({
        async start(controller) {
          controller.enqueue(encoder.encode(ndjson({ type: "citations", citations: sources })));
          const foundryStarted = performance.now();
          let retryCount = 0;
          try {
            let grounded;
            try {
              grounded = await collectGroundedAnswer(question, evidence, safetyId, request.signal);
            } catch (firstError) {
              if (!retryableGroundingFailure(firstError) || request.signal.aborted) throw firstError;
              retryCount = 1;
              console.warn("REGDOCS grounded answer validation failed; retrying once", firstError);
              grounded = await collectGroundedAnswer(question, evidence, safetyId, request.signal);
            }

            const foundryMs = msSince(foundryStarted);
            const totalMs = msSince(requestStarted);
            controller.enqueue(encoder.encode(ndjson({ type: "delta", delta: grounded.answer })));
            controller.enqueue(encoder.encode(ndjson({ type: "citations", citations: grounded.citations })));
            controller.enqueue(encoder.encode(ndjson({
              type: "done",
              model: grounded.model,
              foundry: { used: true, deployment: grounded.model },
              citationValidation: "passed",
              citationCount: grounded.citations.length,
              retryCount,
              timings: { retrievalMs, foundryMs, totalMs },
              ...common,
            })));
            askTelemetry({
              status: "success",
              ...common,
              foundryUsed: true,
              foundryDeployment: grounded.model,
              citationValidation: "passed",
              citationCount: grounded.citations.length,
              retryCount,
              retrievalMs,
              foundryMs,
              totalMs,
            });
          } catch (error) {
            const foundryMs = msSince(foundryStarted);
            const failure = userFacingStreamFailure(error);
            console.error("REGDOCS grounded answer stream failed", error);
            askTelemetry({
              status: failure.code,
              ...common,
              foundryUsed: true,
              foundryDeployment: configuredFoundryModel,
              citationValidation: failure.code === "citation_validation_failed" ? "failed" : "not_completed",
              retryCount,
              retrievalMs,
              foundryMs,
              totalMs: msSince(requestStarted),
            });
            controller.enqueue(encoder.encode(ndjson({
              type: "error",
              ...failure,
              foundry: { used: true, deployment: configuredFoundryModel },
              retryCount,
              timings: { retrievalMs, foundryMs, totalMs: msSince(requestStarted) },
              ...common,
            })));
          } finally {
            controller.close();
          }
        },
      });
      return new Response(streamBody, {
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": "application/x-ndjson; charset=utf-8",
        },
      });
    }

    const foundryStarted = performance.now();
    let retryCount = 0;
    let result;
    try {
      result = await answerWithFoundry(question, evidence, safetyId);
    } catch (firstError) {
      if (!retryableGroundingFailure(firstError)) throw firstError;
      retryCount = 1;
      console.warn("REGDOCS grounded answer validation failed; retrying once", firstError);
      result = await answerWithFoundry(question, evidence, safetyId);
    }
    const foundryMs = msSince(foundryStarted);
    const totalMs = msSince(requestStarted);
    askTelemetry({
      status: "success",
      ...common,
      foundryUsed: true,
      foundryDeployment: result.model,
      citationValidation: "passed",
      citationCount: result.citations.length,
      retryCount,
      retrievalMs,
      foundryMs,
      totalMs,
    });
    return Response.json({
      ...result,
      ...common,
      foundry: { used: true, deployment: result.model },
      citationValidation: "passed",
      citationCount: result.citations.length,
      retryCount,
      timings: { retrievalMs, foundryMs, totalMs },
    });
  } catch (error) {
    console.error("REGDOCS grounded answer failed", error);
    const message = error instanceof Error ? error.message : "Grounded answer failed";
    const configurationError = message.includes("not configured");
    askTelemetry({
      status: configurationError ? "configuration_error" : "failed",
      foundryUsed: false,
      totalMs: msSince(requestStarted),
    });
    return Response.json(
      { error: configurationError ? "Microsoft Foundry or Azure AI Search is not configured." : "The grounded answer could not be generated." },
      { status: configurationError ? 503 : 502 },
    );
  }
}
