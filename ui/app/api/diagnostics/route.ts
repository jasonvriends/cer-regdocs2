import { AzureKeyCredential, SearchClient } from "@azure/search-documents";
import { DefaultAzureCredential } from "@azure/identity";
import { answerWithFoundry } from "@/lib/foundry";
import { getDocumentView, searchRegdocs, type AtlasSearchResult } from "@/lib/azure-search";
import { getCorpusStatus } from "@/lib/corpus-status";
import { authorizedDiagnosticsToken, diagnosticsTokenConfigured } from "@/lib/observability";

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
    operatorDiagnostics: boolean;
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
    operatorDiagnostics: diagnosticsTokenConfigured(),
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

function missingFields(record: Record<string, unknown> | null, requiredFields: string[]) {
  if (!record) return [...requiredFields];
  return requiredFields.filter((field) => !Object.prototype.hasOwnProperty.call(record, field));
}

async function probeIndex(indexName: string, requiredFields: string[]): Promise<Check> {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  if (!endpoint) return { ok: false, error: "AZURE_SEARCH_ENDPOINT is not configured" };
  const result = await timed(async () => {
    const client = new SearchClient<Record<string, unknown>>(endpoint, indexName, searchCredential());
    const response = await client.search("*", {
      top: 1,
      includeTotalCount: true,
      select: requiredFields,
    });
    let sample: Record<string, unknown> | null = null;
    for await (const item of response.results) {
      sample = item.document;
      break;
    }
    return { count: response.count ?? 0, sample };
  });
  if (result.value === null) return result.check;

  const missing = missingFields(result.value.sample, requiredFields);
  const populated = result.value.count > 0;
  const ok = populated && missing.length === 0;
  return {
    ...result.check,
    ok,
    count: result.value.count,
    detail: `${indexName} · fields ${requiredFields.join(", ")}`,
    ...(ok
      ? {}
      : {
          error: !populated
            ? `${indexName} is reachable but contains no records`
            : `${indexName} is missing required v1 field(s): ${missing.join(", ")}`,
        }),
  };
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

  const corpus = await timed(() => getCorpusStatus({ refresh: true }));
  checks.corpus = corpus.value
    ? {
        ...corpus.check,
        ok: corpus.value.chunkCount > 0,
        count: corpus.value.chunkCount,
        detail: `${corpus.value.indexName} · ${corpus.value.earliestFilingDate ?? "unknown"} → ${corpus.value.latestFilingDate ?? "unknown"}`,
        ...(corpus.value.chunkCount > 0 ? {} : { error: `${corpus.value.indexName} contains no chunks` }),
      }
    : corpus.check;

  const keyword = await timed(() => searchRegdocs({ query, top: 1, mode: "keyword", sort: "relevance" }));
  checks.keywordSearch = keyword.value
    ? {
        ...keyword.check,
        ok: keyword.value.results.length > 0,
        count: keyword.value.results.length,
        detail: `query=${query}`,
        ...(keyword.value.results.length ? {} : { error: `No keyword results for diagnostic query ${JSON.stringify(query)}` }),
      }
    : keyword.check;

  let evidence = keyword.value?.results[0] ?? null;

  if (evidence) {
    const requiredChunkFields = ["chunk_id", "document_id", "chunk_index", "chunk_type", "content", "page_start", "page_end"];
    const missing = missingFields(evidence as unknown as Record<string, unknown>, requiredChunkFields);
    checks.corpusChunkContract = {
      ok: missing.length === 0,
      detail: `Stage 5 v1 fields · ${requiredChunkFields.join(", ")}`,
      ...(missing.length ? { error: `Search result is missing required document-view field(s): ${missing.join(", ")}` } : {}),
    };
  } else {
    checks.corpusChunkContract = { ok: false, error: "No Search result was available to validate the v1 chunk contract" };
  }

  if (config.hybrid) {
    const hybrid = await timed(() => searchRegdocs({ query, top: 1, mode: "hybrid", sort: "relevance" }));
    checks.hybridSearch = hybrid.value
      ? {
          ...hybrid.check,
          ok: hybrid.value.results.length > 0,
          count: hybrid.value.results.length,
          detail: config.semantic ? "vector + semantic" : "vector",
          ...(hybrid.value.results.length ? {} : { error: `No hybrid results for diagnostic query ${JSON.stringify(query)}` }),
        }
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
      top: 2,
      page: evidence!.page_start ?? undefined,
    }));
    if (document.value) {
      const rows = document.value.results;
      const first = rows[0] as unknown as Record<string, unknown> | undefined;
      const requiredViewerFields = ["chunk_id", "document_id", "chunk_index", "chunk_type", "content", "page_start", "page_end"];
      const missing = missingFields(first ?? null, requiredViewerFields);
      const wrongDocument = rows.some((row) => row.document_id !== evidence!.document_id);
      const indexes = rows
        .map((row) => row.chunk_index)
        .filter((value): value is number => typeof value === "number");
      const ordered = indexes.every((value, index) => index === 0 || value >= indexes[index - 1]);
      const ok = rows.length > 0 && missing.length === 0 && !wrongDocument && ordered;
      checks.documentView = {
        ...document.check,
        ok,
        count: rows.length,
        detail: `${evidence.document_id} · normalized HTML contract`,
        ...(ok
          ? {}
          : {
              error: rows.length === 0
                ? "Document view returned no indexed chunks"
                : missing.length
                  ? `Document view is missing required field(s): ${missing.join(", ")}`
                  : wrongDocument
                    ? "Document view returned chunks from another document"
                    : "Document view chunks are not in chunk_index order",
            }),
      };
    } else {
      checks.documentView = document.check;
    }
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

  checks.entitiesIndex = await probeIndex(
    process.env.AZURE_SEARCH_ENTITIES_INDEX?.trim() || "regdocs-entities",
    ["id", "entity_type", "name", "origin", "schema_version"],
  );
  checks.relationsIndex = await probeIndex(
    process.env.AZURE_SEARCH_RELATIONS_INDEX?.trim() || "regdocs-relations",
    ["id", "source_id", "target_id", "relationship_type", "confidence", "origin", "review_status"],
  );
  checks.eventsIndex = await probeIndex(
    process.env.AZURE_SEARCH_EVENTS_INDEX?.trim() || "regdocs-events",
    ["id", "event_type", "title", "summary", "date_start", "date_precision", "date_basis", "confidence", "origin", "review_status"],
  );
  checks.claimsIndex = await probeIndex(
    process.env.AZURE_SEARCH_CLAIMS_INDEX?.trim() || "regdocs-claims",
    ["id", "claim_type", "statement", "evidence_chunk_ids", "confidence", "origin", "review_status"],
  );
  checks.obligationsIndex = await probeIndex(
    process.env.AZURE_SEARCH_OBLIGATIONS_INDEX?.trim() || "regdocs-obligations",
    ["id", "obligation_type", "action", "evidence_chunk_ids", "confidence", "origin", "review_status"],
  );

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

  if (!diagnosticsTokenConfigured()) {
    return Response.json({ error: "Operator live diagnostics are not configured." }, { status: 503 });
  }
  const authorization = request.headers.get("authorization");
  const bearer = authorization?.startsWith("Bearer ") ? authorization.slice(7).trim() : null;
  if (!authorizedDiagnosticsToken(bearer)) {
    return Response.json({ error: "Operator authorization is required for live diagnostics." }, { status: 401 });
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
