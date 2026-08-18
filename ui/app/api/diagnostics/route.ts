import { AzureKeyCredential, SearchClient } from "@azure/search-documents";
import { DefaultAzureCredential } from "@azure/identity";
import { answerWithFoundry } from "@/lib/foundry";
import { getDocumentView, searchRegdocs, type AtlasSearchResult } from "@/lib/azure-search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DEEP_CACHE_MS = 60_000;
let cachedDeep: { at: number; payload: DiagnosticsPayload } | null = null;

type Check = {
  ok: boolean;
  ms?: number;
  skipped?: boolean;
  detail?: string;
  count?: number | null;
  error?: string;
};

type DiagnosticsPayload = {
  service: "regdocs-atlas-ui";
  status: "ok" | "degraded" | "configuration_required";
  deep: boolean;
  cached?: boolean;
  generatedAt: string;
  configuration: {
    search: boolean;
    hybrid: boolean;
    semantic: boolean;
    foundry: boolean;
    foundryModel: string | null;
  };
  checks?: Record<string, Check>;
};

function configuration() {
  const search = Boolean(process.env.AZURE_SEARCH_ENDPOINT?.trim());
  const hybrid = Boolean(process.env.AZURE_SEARCH_VECTOR_FIELD?.trim());
  const semantic = Boolean(process.env.AZURE_SEARCH_SEMANTIC_CONFIGURATION?.trim());
  const foundryEndpoint = Boolean(process.env.FOUNDRY_PROJECT_ENDPOINT?.trim());
  const foundryModel = process.env.FOUNDRY_MODEL_DEPLOYMENT?.trim() || null;
  return {
    search,
    hybrid,
    semantic,
    foundry: foundryEndpoint && Boolean(foundryModel),
    foundryModel,
  };
}

async function timed<T>(fn: () => Promise<T>) {
  const started = performance.now();
  try {
    const value = await fn();
    return { value, check: { ok: true, ms: Math.round(performance.now() - started) } satisfies Check };
  } catch (error) {
    return {
      value: null,
      check: {
        ok: false,
        ms: Math.round(performance.now() - started),
        error: error instanceof Error ? error.message : "Unknown diagnostics failure",
      } satisfies Check,
    };
  }
}

function searchCredential() {
  const key = process.env.AZURE_SEARCH_API_KEY?.trim();
  return key ? new AzureKeyCredential(key) : new DefaultAzureCredential();
}

async function probeIndex(indexName: string): Promise<Check> {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  if (!endpoint) return { ok: false, error: "AZURE_SEARCH_ENDPOINT is not configured" };
  const result = await timed(async () => {
    const client = new SearchClient<Record<string, unknown>>(endpoint, indexName, searchCredential());
    const response = await client.search("*", { top: 1, includeTotalCount: true });
    for await (const _ of response.results) break;
    return response.count ?? null;
  });
  return result.value === null
    ? result.check
    : { ...result.check, count: result.value, detail: indexName };
}

function diagnosticEvidence(): AtlasSearchResult {
  return {
    chunk_id: "diagnostic:S1",
    document_id: "diagnostic",
    chunk_index: 1,
    chunk_type: "text",
    title: "REGDOCS Atlas diagnostic evidence",
    heading: "Diagnostic evidence",
    content: "The REGDOCS Atlas diagnostic code is ATLAS-OK.",
    page_start: 1,
    page_end: 1,
    score: 1,
  };
}

async function runDeep(): Promise<DiagnosticsPayload> {
  const config = configuration();
  const query = process.env.REGDOCS_DIAGNOSTIC_QUERY?.trim() || "Commission";
  const checks: Record<string, Check> = {};

  const keyword = await timed(() => searchRegdocs({ query, top: 1, mode: "keyword", sort: "relevance" }));
  checks.keywordSearch = keyword.value
    ? { ...keyword.check, count: keyword.value.results.length, detail: `query=${query}` }
    : keyword.check;

  let evidence = keyword.value?.results[0] ?? null;

  if (config.hybrid) {
    const hybrid = await timed(() => searchRegdocs({ query, top: 1, mode: "hybrid", sort: "relevance" }));
    checks.hybridSearch = hybrid.value
      ? { ...hybrid.check, count: hybrid.value.results.length, detail: config.semantic ? "vector + semantic" : "vector" }
      : hybrid.check;
    evidence ??= hybrid.value?.results[0] ?? null;
    checks.semanticSearch = config.semantic
      ? { ...checks.hybridSearch, detail: "semantic ranking exercised by hybrid search" }
      : { ok: true, skipped: true, detail: "semantic configuration not set" };
  } else {
    checks.hybridSearch = { ok: true, skipped: true, detail: "vector field not configured" };
    checks.semanticSearch = { ok: true, skipped: true, detail: "hybrid search not configured" };
  }

  if (evidence) {
    const document = await timed(() => getDocumentView({
      documentId: evidence!.document_id,
      top: 1,
      page: evidence!.page_start ?? undefined,
    }));
    checks.documentView = document.value
      ? { ...document.check, count: document.value.results.length, detail: evidence.document_id }
      : document.check;
  } else {
    checks.documentView = { ok: false, error: "No search result was available to test document retrieval" };
  }

  const foundry = await timed(() => answerWithFoundry(
    "According to the supplied diagnostic evidence, what is the REGDOCS Atlas diagnostic code? Include the evidence citation.",
    [diagnosticEvidence()],
  ));
  checks.foundryChat = foundry.value
    ? {
        ...foundry.check,
        ok: foundry.value.answer.includes("ATLAS-OK") && foundry.value.citations.length > 0,
        count: foundry.value.citations.length,
        detail: foundry.value.model,
      }
    : foundry.check;

  checks.entitiesIndex = await probeIndex(process.env.AZURE_SEARCH_ENTITIES_INDEX?.trim() || "regdocs-entities");
  checks.relationsIndex = await probeIndex(process.env.AZURE_SEARCH_RELATIONS_INDEX?.trim() || "regdocs-relations");
  checks.eventsIndex = await probeIndex(process.env.AZURE_SEARCH_EVENTS_INDEX?.trim() || "regdocs-events");

  const required = Object.values(checks).filter((check) => !check.skipped);
  return {
    service: "regdocs-atlas-ui",
    status: required.every((check) => check.ok) ? "ok" : "degraded",
    deep: true,
    generatedAt: new Date().toISOString(),
    configuration: config,
    checks,
  };
}

export async function GET(request: Request) {
  const config = configuration();
  const deep = new URL(request.url).searchParams.get("deep") === "1";

  if (!deep) {
    return Response.json({
      service: "regdocs-atlas-ui",
      status: config.search ? "ok" : "configuration_required",
      deep: false,
      generatedAt: new Date().toISOString(),
      configuration: config,
    } satisfies DiagnosticsPayload, { status: config.search ? 200 : 503 });
  }

  const now = Date.now();
  if (cachedDeep && now - cachedDeep.at < DEEP_CACHE_MS) {
    const payload = { ...cachedDeep.payload, cached: true };
    return Response.json(payload, { status: payload.status === "ok" ? 200 : 503 });
  }

  const payload = await runDeep();
  cachedDeep = { at: now, payload };
  return Response.json(payload, { status: payload.status === "ok" ? 200 : 503 });
}
