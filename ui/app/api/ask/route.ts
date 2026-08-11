import { createHash } from "node:crypto";
import {
  getRegdocsChunk,
  searchRegdocs,
  type AtlasSearchResult,
  type AtlasSearchSort,
} from "@/lib/azure-search";
import { citationFor, requestGroundedFoundryStream } from "@/lib/foundry";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type AskRequest = {
  question?: unknown;
  evidenceIds?: unknown;
  scope?: {
    documentId?: unknown;
    filingId?: unknown;
    filingNumber?: unknown;
    company?: unknown;
    project?: unknown;
  };
};

const RATE_WINDOW_MS = 60_000;
const RATE_LIMIT = 30;
const rateBuckets = new Map<string, { started: number; count: number }>();

function requesterAddress(request: Request) {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip")?.trim() || "local";
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

function optionalString(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function ndjson(value: unknown) {
  return `${JSON.stringify(value)}\n`;
}

export async function POST(request: Request) {
  if (!allowRequest(requesterAddress(request))) {
    return Response.json({ error: "Too many Ask requests; try again shortly." }, { status: 429 });
  }
  let body: AskRequest;
  try {
    body = (await request.json()) as AskRequest;
  } catch {
    return Response.json({ error: "Request body must be valid JSON." }, { status: 400 });
  }
  const question = optionalString(body.question);
  if (!question) return Response.json({ error: "Question is required." }, { status: 400 });
  if (question.length > 4_000) {
    return Response.json({ error: "Question must be 4,000 characters or fewer." }, { status: 400 });
  }
  const evidenceIds = Array.isArray(body.evidenceIds)
    ? [...new Set(body.evidenceIds.filter((item): item is string => typeof item === "string" && Boolean(item.trim())).map((item) => item.trim()))].slice(0, 20)
    : [];
  const scope = body.scope && typeof body.scope === "object" ? body.scope : {};

  try {
    const [retrieved, pinned] = await Promise.all([
      searchRegdocs({
        query: question,
        top: 10,
        sort: "relevance" as AtlasSearchSort,
        retrievalMode: process.env.AZURE_SEARCH_RETRIEVAL_MODE === "hybrid" ? "hybrid" :
          process.env.AZURE_SEARCH_RETRIEVAL_MODE === "semantic" ? "semantic" : "lexical",
        documentIds: optionalString(scope.documentId) ? [optionalString(scope.documentId)!] : undefined,
        filingIds: optionalString(scope.filingId) ? [optionalString(scope.filingId)!] : undefined,
        filingNumbers: optionalString(scope.filingNumber) ? [optionalString(scope.filingNumber)!] : undefined,
        companies: optionalString(scope.company) ? [optionalString(scope.company)!] : undefined,
        projects: optionalString(scope.project) ? [optionalString(scope.project)!] : undefined,
      }),
      Promise.all(evidenceIds.map((chunkId) => getRegdocsChunk(chunkId))),
    ]);
    const byId = new Map<string, AtlasSearchResult>();
    for (const item of [...pinned.filter((item): item is AtlasSearchResult => Boolean(item)), ...retrieved.results]) {
      if (item.content) byId.set(item.chunk_id, item);
    }
    const evidence = [...byId.values()].slice(0, 16);
    if (!evidence.length) {
      return Response.json({ error: "No evidence matched this question and scope." }, { status: 422 });
    }

    const upstream = await requestGroundedFoundryStream(
      question,
      evidence,
      request.signal,
      safetyIdentifier(request),
    );
    if (!upstream.ok || !upstream.body) {
      const detail = (await upstream.text()).slice(0, 1_000);
      console.error("Foundry request failed", upstream.status, detail);
      return Response.json({ error: "Foundry answer generation failed." }, { status: 502 });
    }

    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    const citations = new Map(evidence.map((item) => [item.chunk_id, citationFor(item)]));
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const reader = upstream.body!.getReader();
        let buffer = "";
        let answer = "";
        const processFrame = (frame: string) => {
          const raw = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n")
            .trim();
          if (!raw || raw === "[DONE]") return;
          const event = JSON.parse(raw) as {
            type?: string;
            delta?: string;
            error?: { message?: string };
          };
          if (event.type === "response.output_text.delta" && event.delta) {
            answer += event.delta;
            controller.enqueue(encoder.encode(ndjson({ type: "delta", delta: event.delta })));
          } else if (event.type === "error" || event.type === "response.failed" || event.type === "response.incomplete") {
            throw new Error("Foundry did not complete the answer stream");
          }
        };
        try {
          while (true) {
            const { done, value } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            let boundary = buffer.search(/\r?\n\r?\n/);
            while (boundary >= 0) {
              const frame = buffer.slice(0, boundary);
              const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] || "\n\n";
              buffer = buffer.slice(boundary + separator.length);
              processFrame(frame);
              boundary = buffer.search(/\r?\n\r?\n/);
            }
            if (done) {
              if (buffer.trim()) processFrame(buffer);
              break;
            }
          }
          const citedIds = [...answer.matchAll(/\[\[([^\]\s]+)\]\]/g)].map((match) => match[1]);
          const validated = [...new Set(citedIds)].flatMap((id) => {
            const citation = citations.get(id);
            return citation ? [citation] : [];
          });
          for (const citation of validated) {
            controller.enqueue(encoder.encode(ndjson({ type: "citation", citation })));
          }
          controller.enqueue(
            encoder.encode(ndjson({ type: "done", evidenceCount: evidence.length, citationCount: validated.length })),
          );
          controller.close();
        } catch (error) {
          controller.enqueue(
            encoder.encode(ndjson({ type: "error", error: "Answer stream failed." })),
          );
          controller.close();
        } finally {
          reader.releaseLock();
        }
      },
      cancel() {
        void upstream.body?.cancel();
      },
    });
    return new Response(stream, {
      headers: {
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
      },
    });
  } catch (error) {
    console.error("REGDOCS grounded Ask failed", error);
    const message = error instanceof Error ? error.message : "Grounded Ask failed";
    const configurationError = message.includes("FOUNDRY_") || message.includes("AZURE_SEARCH_ENDPOINT");
    return Response.json(
      { error: configurationError ? "Foundry or Azure Search is not configured." : "Grounded Ask failed." },
      { status: configurationError ? 503 : 502 },
    );
  }
}
