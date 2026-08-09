"use client";

import { FormEvent, useMemo, useState } from "react";
import type { AtlasSearchResult } from "@/lib/azure-search";

type SearchResponse = {
  query: string;
  count: number;
  results: AtlasSearchResult[];
  error?: string;
};

function pageLabel(result: AtlasSearchResult) {
  if (!result.page_start) return "Page unknown";
  if (!result.page_end || result.page_end === result.page_start) return `Page ${result.page_start}`;
  return `Pages ${result.page_start}–${result.page_end}`;
}

function excerpt(text?: string | null, length = 360) {
  if (!text) return "No text preview is available for this chunk.";
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > length ? `${normalized.slice(0, length)}…` : normalized;
}

export function AtlasWorkbench() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AtlasSearchResult[]>([]);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => results.find((result) => result.chunk_id === selectedChunkId) ?? results[0] ?? null,
    [results, selectedChunkId],
  );

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(trimmed)}&top=20`, {
        cache: "no-store",
      });
      const payload = (await response.json()) as SearchResponse;
      if (!response.ok) throw new Error(payload.error || "Search failed");

      setResults(payload.results);
      setSelectedChunkId(payload.results[0]?.chunk_id ?? null);
    } catch (caught) {
      setResults([]);
      setSelectedChunkId(null);
      setError(caught instanceof Error ? caught.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-100 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-4">
        <div className="mx-auto flex max-w-[1800px] items-center gap-5">
          <div className="min-w-fit">
            <div className="text-lg font-semibold tracking-tight">REGDOCS Atlas</div>
            <div className="text-xs text-slate-500">Evidence-first regulatory research</div>
          </div>

          <form className="flex flex-1 gap-2" onSubmit={submitSearch}>
            <input
              aria-label="Search regulatory records"
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm outline-none ring-blue-500 transition focus:ring-2"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search regulatory records…"
              value={query}
            />
            <button
              className="rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={loading || !query.trim()}
              type="submit"
            >
              {loading ? "Searching…" : "Search"}
            </button>
          </form>

          <span className="hidden rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 md:inline">
            Phase 1 · no login
          </span>
        </div>
      </header>

      <div className="mx-auto grid min-h-[calc(100vh-77px)] max-w-[1800px] grid-cols-1 bg-white lg:grid-cols-[340px_minmax(420px,1fr)_360px]">
        <section className="border-r border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Search</div>
            <div className="mt-1 text-sm text-slate-700">
              {results.length ? `${results.length} retrieved chunks` : "Keyword retrieval from Azure AI Search"}
            </div>
          </div>

          {error ? (
            <div className="m-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{error}</div>
          ) : null}

          {!results.length && !error ? (
            <div className="p-5 text-sm leading-6 text-slate-500">
              Search the Stage 5 index to populate evidence here. Results retain document, page, and source identities.
            </div>
          ) : null}

          <div className="max-h-[calc(100vh-150px)] overflow-y-auto">
            {results.map((result) => {
              const active = selected?.chunk_id === result.chunk_id;
              return (
                <button
                  className={`block w-full border-b border-slate-100 p-4 text-left transition ${
                    active ? "bg-blue-50" : "hover:bg-slate-50"
                  }`}
                  key={result.chunk_id}
                  onClick={() => setSelectedChunkId(result.chunk_id)}
                  type="button"
                >
                  <div className="text-xs font-medium text-blue-700">{pageLabel(result)}</div>
                  <div className="mt-1 line-clamp-2 text-sm font-semibold text-slate-900">
                    {result.heading || result.title || `Document ${result.document_id}`}
                  </div>
                  <div className="mt-2 line-clamp-3 text-xs leading-5 text-slate-600">{excerpt(result.content, 220)}</div>
                  <div className="mt-2 truncate text-[11px] text-slate-400">
                    {result.project || result.company || `Document ${result.document_id}`}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="min-w-0 bg-slate-100">
          <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Evidence</div>
              <div className="mt-1 max-w-3xl truncate text-sm font-medium">
                {selected?.title || (selected ? `Document ${selected.document_id}` : "No document selected")}
              </div>
            </div>
            {selected?.source_url ? (
              <a
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                href={selected.source_url}
                rel="noreferrer"
                target="_blank"
              >
                REGDOCS source ↗
              </a>
            ) : null}
          </div>

          <div className="flex min-h-[calc(100vh-133px)] items-start justify-center overflow-auto p-6">
            {selected ? (
              <article className="w-full max-w-[820px] rounded-sm border border-slate-300 bg-white px-12 py-14 shadow-sm">
                <div className="mb-8 flex items-start justify-between gap-6 border-b border-slate-200 pb-5">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-widest text-slate-400">Document {selected.document_id}</div>
                    <h1 className="mt-2 text-xl font-semibold leading-7">{selected.heading || selected.title || "Retrieved evidence"}</h1>
                  </div>
                  <div className="min-w-fit rounded-md bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800">{pageLabel(selected)}</div>
                </div>

                <div className="rounded-md border-l-4 border-blue-500 bg-blue-50/60 p-5 text-[15px] leading-7 text-slate-800">
                  {excerpt(selected.content, 2200)}
                </div>

                <div className="mt-8 border-t border-slate-200 pt-4 text-xs leading-5 text-slate-500">
                  <strong className="text-slate-700">Viewer scaffold:</strong> this currently renders the retrieved normalized text. The next viewer step is the original PDF/page renderer plus Stage 4 polygon overlays so a citation can jump to an exact source region.
                </div>
              </article>
            ) : (
              <div className="mt-24 max-w-md text-center">
                <div className="text-base font-semibold text-slate-700">Evidence viewer</div>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  Select a search result to inspect its page-scoped evidence. PDF rendering and polygon highlights are the next UI milestone.
                </p>
              </div>
            )}
          </div>
        </section>

        <aside className="border-l border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Ask</div>
            <div className="mt-1 text-sm text-slate-700">Grounded research assistant</div>
          </div>

          <div className="flex h-[calc(100vh-133px)] flex-col p-4">
            <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm leading-6 text-slate-600">
              The chat surface is reserved now, but Foundry is deliberately not wired yet. The first Ask implementation will retrieve bounded Atlas evidence and require clickable source citations.
            </div>

            {selected ? (
              <div className="mt-4 rounded-lg border border-slate-200 p-3">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Current evidence</div>
                <div className="mt-1 text-sm font-medium">{selected.heading || selected.title || selected.chunk_id}</div>
                <div className="mt-1 text-xs text-slate-500">{pageLabel(selected)}</div>
              </div>
            ) : null}

            <div className="mt-auto pt-4">
              <textarea
                aria-label="Ask Atlas"
                className="min-h-24 w-full resize-none rounded-lg border border-slate-300 bg-slate-50 p-3 text-sm text-slate-500"
                disabled
                placeholder="Ask a question about the retrieved evidence…"
              />
              <button
                className="mt-2 w-full rounded-lg bg-slate-200 px-4 py-2.5 text-sm font-medium text-slate-500"
                disabled
                type="button"
              >
                Ask · Foundry not connected
              </button>
            </div>
          </div>
        </aside>
      </div>
    </main>
  );
}
