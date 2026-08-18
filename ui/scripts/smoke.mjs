#!/usr/bin/env node

const baseUrl = (process.env.ATLAS_BASE_URL || "").replace(/\/$/, "");
const query = process.env.ATLAS_SMOKE_QUERY || "fair market access";
const askQuestion = process.env.ATLAS_SMOKE_QUESTION || "What has the Commission said about fair market access?";

if (!baseUrl) {
  console.error("ATLAS_BASE_URL is required, for example https://app-regdocs.example.com");
  process.exit(2);
}

function ok(message) {
  console.log(`✓ ${message}`);
}

function fail(message, detail) {
  console.error(`✗ ${message}${detail ? `: ${detail}` : ""}`);
  process.exitCode = 1;
}

async function json(path, init) {
  const response = await fetch(`${baseUrl}${path}`, init);
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`${path} returned non-JSON (${response.status}): ${text.slice(0, 300)}`);
  }
  if (!response.ok) throw new Error(`${path} returned ${response.status}: ${JSON.stringify(payload).slice(0, 500)}`);
  return payload;
}

async function ask() {
  const response = await fetch(`${baseUrl}/api/ask`, {
    method: "POST",
    headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
    body: JSON.stringify({ question: askQuestion, workspaceChunkIds: [], filters: {} }),
  });
  if (!response.ok || !response.body) throw new Error(`/api/ask returned ${response.status}`);

  const text = await response.text();
  const events = text.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const error = events.find((event) => event.type === "error");
  if (error) throw new Error(error.error || "Ask stream failed");
  const citations = events.findLast((event) => event.type === "citations")?.citations || [];
  const answer = events.filter((event) => event.type === "delta").map((event) => event.delta).join("");
  const done = events.find((event) => event.type === "done");
  if (!answer.trim()) throw new Error("Ask returned no grounded answer text");
  if (!citations.length) throw new Error("Ask returned no citations");
  if (!done) throw new Error("Ask stream never completed");
  if (done.foundry?.used !== true) throw new Error("Ask completed without Foundry usage metadata");
  if (done.citationValidation !== "passed") throw new Error(`citation validation=${done.citationValidation || "missing"}`);
  return { citations, done };
}

async function main() {
  try {
    const health = await json("/api/health");
    if (health.status !== "ok") throw new Error(`health status=${health.status}`);
    ok("health endpoint");

    const diagnostics = await json("/api/diagnostics?deep=1");
    if (diagnostics.status !== "ok") throw new Error(`diagnostics status=${diagnostics.status}`);
    if (diagnostics.checks?.foundryChat?.ok !== true) throw new Error("Foundry chat diagnostic failed");
    if (diagnostics.checks?.keywordSearch?.ok !== true) throw new Error("keyword search diagnostic failed");
    if (diagnostics.configuration?.hybrid && diagnostics.checks?.hybridSearch?.ok !== true) throw new Error("hybrid search diagnostic failed");
    for (const key of ["entitiesIndex", "relationsIndex", "eventsIndex"]) {
      if (diagnostics.checks?.[key]?.ok !== true) throw new Error(`${key} diagnostic failed`);
    }
    ok("deep diagnostics: Search + Foundry + intelligence indexes");

    const search = await json(`/api/search?q=${encodeURIComponent(query)}&top=5&mode=keyword`);
    if (!Array.isArray(search.results) || !search.results.length) throw new Error(`no search results for ${JSON.stringify(query)}`);
    const first = search.results[0];
    ok(`keyword search returned ${search.results.length} result(s)`);

    const evidence = await json(`/api/evidence/${encodeURIComponent(first.chunk_id)}`);
    if (evidence.chunk_id !== first.chunk_id) throw new Error("evidence lookup returned the wrong chunk");
    ok("evidence lookup");

    const document = await json(`/api/document-view?documentId=${encodeURIComponent(first.document_id)}&top=5${first.page_start ? `&page=${first.page_start}` : ""}`);
    if (!Array.isArray(document.results) || !document.results.length) throw new Error("document view returned no chunks");
    ok("HTML document-view backend");

    const timeline = await json(`/api/timeline?documentId=${encodeURIComponent(first.document_id)}&top=20`);
    if (!Array.isArray(timeline.events)) throw new Error("timeline response is missing its events array");
    ok(`timeline API contract (${timeline.events.length} event(s) for sampled document)`);

    const graph = await json(`/api/graph?documentId=${encodeURIComponent(first.document_id)}&top=50`);
    if (!Array.isArray(graph.edges) || !Array.isArray(graph.nodes)) throw new Error("graph response is missing nodes or edges arrays");
    ok(`relationship graph API contract (${graph.edges.length} edge(s) for sampled document)`);

    const grounded = await ask();
    ok(`Foundry grounded Ask completed with ${grounded.citations.length} citation(s)`);
    console.log(`  retrieval=${grounded.done.retrievalMode}; model=${grounded.done.model}; foundry=${grounded.done.foundry?.deployment || "unknown"}; totalMs=${grounded.done.timings?.totalMs ?? "unknown"}`);
  } catch (error) {
    fail("Atlas smoke test", error instanceof Error ? error.message : String(error));
  }

  if (process.exitCode) process.exit(process.exitCode);
}

await main();
