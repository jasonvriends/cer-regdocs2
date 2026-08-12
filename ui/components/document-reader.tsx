"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import type { AtlasDocumentViewResponse, AtlasSearchResult } from "@/lib/azure-search";

type DocumentApiResponse = AtlasDocumentViewResponse & { error?: string };

const WINDOW_SIZE = 60;
const QUERY_WORDS_TO_IGNORE = new Set([
  "and", "or", "the", "a", "an", "to", "of", "in", "on", "for", "with", "what", "where", "when", "who", "how", "is", "are", "was", "were",
]);

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightTerms(query: string) {
  const phrases = [...query.matchAll(/"([^"]+)"/g)].map((match) => match[1].trim());
  const unquoted = query.replace(/"[^"]+"/g, " ");
  const words = unquoted
    .split(/[^\p{L}\p{N}-]+/u)
    .map((word) => word.trim())
    .filter((word) => word.length > 1 && !QUERY_WORDS_TO_IGNORE.has(word.toLowerCase()));
  return [...new Set([...phrases, ...words])].sort((a, b) => b.length - a.length).slice(0, 16);
}

function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return text;
  const expression = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "giu");
  const exact = new Set(terms.map((term) => term.toLocaleLowerCase()));
  return text.split(expression).map((part, index) =>
    exact.has(part.toLocaleLowerCase()) ? <mark key={`${part}-${index}`}>{part}</mark> : <Fragment key={`${index}-${part.slice(0, 12)}`}>{part}</Fragment>,
  );
}

function tableRows(content: string) {
  const rows = content.split("\n").map((row) => row.split("\t"));
  const width = Math.max(0, ...rows.map((row) => row.length));
  return width > 1 ? rows : null;
}

function ChunkContent({ chunk, terms }: { chunk: AtlasSearchResult; terms: string[] }) {
  if (chunk.chunk_type === "table") {
    const rows = tableRows(chunk.content ?? "");
    if (rows) {
      return (
        <div className="atlas-table-wrap">
          <table className="atlas-document-table">
            <thead><tr>{rows[0].map((cell, index) => <th key={index}><HighlightedText terms={terms} text={cell} /></th>)}</tr></thead>
            <tbody>{rows.slice(1).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}><HighlightedText terms={terms} text={cell} /></td>)}</tr>)}</tbody>
          </table>
        </div>
      );
    }
  }

  if (chunk.chunk_type === "figure") {
    return (
      <figure className="atlas-figure-text">
        <div className="atlas-figure-placeholder" aria-hidden="true">▧</div>
        <figcaption><span>Extracted figure text</span><HighlightedText terms={terms} text={chunk.content ?? "No figure text was extracted."} /></figcaption>
      </figure>
    );
  }

  return <div className="whitespace-pre-wrap"><HighlightedText terms={terms} text={chunk.content ?? "No normalized text is available."} /></div>;
}

function groupByPage(results: AtlasSearchResult[]) {
  const groups = new Map<number, AtlasSearchResult[]>();
  for (const chunk of results) {
    const page = chunk.page_start ?? 0;
    groups.set(page, [...(groups.get(page) ?? []), chunk]);
  }
  return [...groups.entries()].sort(([left], [right]) => left - right);
}

export function DocumentReader({
  selected,
  query,
  onAddToWorkspace,
}: {
  selected: AtlasSearchResult;
  query: string;
  onAddToWorkspace: (result: AtlasSearchResult) => void;
}) {
  const [documentView, setDocumentView] = useState<AtlasDocumentViewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageInput, setPageInput] = useState("");
  const [focusPage, setFocusPage] = useState<number | null>(selected.page_start ?? null);
  const terms = useMemo(() => highlightTerms(query), [query]);
  const pages = useMemo(() => groupByPage(documentView?.results ?? []), [documentView]);

  async function loadWindow(options: { offset?: number; page?: number }) {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ documentId: selected.document_id, top: String(WINDOW_SIZE) });
    if (options.offset !== undefined) params.set("offset", String(options.offset));
    if (options.page !== undefined) params.set("page", String(options.page));
    try {
      const response = await fetch(`/api/document-view?${params.toString()}`, { cache: "no-store" });
      const payload = (await response.json()) as DocumentApiResponse;
      if (!response.ok) throw new Error(payload.error || "Document view failed");
      setDocumentView(payload);
      setFocusPage(options.page ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Document view failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setPageInput(selected.page_start ? String(selected.page_start) : "");
    void loadWindow(selected.page_start ? { page: selected.page_start } : { offset: 0 });
    // Loading is keyed to the selected search passage, not changes to callbacks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected.chunk_id]);

  useEffect(() => {
    if (!documentView || loading) return;
    const target = document.getElementById(`reader-chunk-${selected.chunk_id}`)
      ?? (focusPage ? document.getElementById(`reader-page-${focusPage}`) : null)
      ?? document.getElementById(`reader-page-${documentView.results[0]?.page_start ?? 0}`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [documentView, focusPage, loading, selected.chunk_id]);

  function jumpToPage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const page = Number.parseInt(pageInput, 10);
    if (Number.isInteger(page) && page > 0) void loadWindow({ page });
  }

  const first = documentView ? documentView.offset + 1 : 0;
  const last = documentView ? documentView.offset + documentView.count : 0;
  const metadata = documentView?.results[0] ?? selected;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-teal-700"><span>HTML document view</span><span className="rounded-full bg-teal-50 px-2 py-0.5">Search match highlighted</span></div>
            <div className="mt-1 max-w-3xl truncate text-sm font-semibold">{metadata.title || `Document ${selected.document_id}`}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <form className="flex" onSubmit={jumpToPage}><label className="sr-only" htmlFor="reader-page-input">Jump to page</label><input className="w-20 rounded-l-md border border-r-0 border-slate-300 px-2 py-1.5 text-xs" id="reader-page-input" inputMode="numeric" onChange={(event) => setPageInput(event.target.value)} placeholder="Page" value={pageInput} /><button className="rounded-r-md border border-slate-300 px-2.5 py-1.5 text-xs font-semibold hover:bg-slate-50" type="submit">Go</button></form>
            {metadata.source_url ? <a className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50" href={metadata.source_url} rel="noreferrer" target="_blank">Original in REGDOCS ↗</a> : null}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
          <div>Document {selected.document_id}{metadata.filing_number ? ` · Filing ${metadata.filing_number}` : ""}{metadata.filing_date ? ` · ${metadata.filing_date}` : ""}</div>
          {documentView ? <div>Showing sections {first.toLocaleString()}–{last.toLocaleString()} of {documentView.totalCount.toLocaleString()}</div> : null}
        </div>
      </div>

      <div className="atlas-reader-canvas min-h-0 flex-1 overflow-auto px-4 py-6 md:px-8">
        {error ? <div className="mx-auto mb-5 max-w-3xl rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">{error}</div> : null}
        {loading ? <div className="mx-auto max-w-[850px] animate-pulse rounded-sm border border-slate-300 bg-white p-12 shadow-sm"><div className="h-4 w-1/3 rounded bg-slate-200" /><div className="mt-8 h-3 rounded bg-slate-100" /><div className="mt-3 h-3 rounded bg-slate-100" /><div className="mt-3 h-3 w-4/5 rounded bg-slate-100" /></div> : null}
        {!loading && documentView ? <div className="mx-auto max-w-[850px] space-y-6">{pages.map(([page, chunks]) => <section aria-label={page ? `Document page ${page}` : "Unpaginated document content"} className="atlas-document-page" id={`reader-page-${page}`} key={page}><div className="atlas-page-running-head"><span>{metadata.filing_number || `Document ${selected.document_id}`}</span><span>{page ? `Page ${page}` : "Page not identified"}</span></div><div className="atlas-page-content">{chunks.map((chunk) => { const isMatch = chunk.chunk_id === selected.chunk_id; return <section className={`atlas-document-chunk ${isMatch ? "atlas-document-match" : ""}`} id={`reader-chunk-${chunk.chunk_id}`} key={chunk.chunk_id}>{chunk.heading ? <h2>{chunk.heading}</h2> : null}{isMatch ? <div className="atlas-match-label">Search result · {page ? `page ${page}` : "page unknown"}</div> : null}<ChunkContent chunk={chunk} terms={terms} />{isMatch ? <button className="mt-4 rounded-md bg-teal-800 px-3 py-2 text-xs font-bold text-white" onClick={() => onAddToWorkspace(chunk)} type="button">+ Add this passage to workspace</button> : null}</section>;})}</div><div className="atlas-page-number">{page || "—"}</div></section>)}</div> : null}
        {!loading && error && !documentView ? <section className="atlas-document-page mx-auto max-w-[850px]"><div className="atlas-page-running-head"><span>Document {selected.document_id}</span><span>{selected.page_start ? `Page ${selected.page_start}` : "Page not identified"}</span></div><div className="atlas-page-content"><section className="atlas-document-chunk atlas-document-match"><div className="atlas-match-label">Selected search result</div><ChunkContent chunk={selected} terms={terms} /><button className="mt-4 rounded-md bg-teal-800 px-3 py-2 text-xs font-bold text-white" onClick={() => onAddToWorkspace(selected)} type="button">+ Add this passage to workspace</button></section></div></section> : null}
      </div>

      {documentView ? <div className="flex items-center justify-between border-t border-slate-200 bg-white px-4 py-3"><button className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold disabled:opacity-40" disabled={loading || documentView.offset === 0} onClick={() => void loadWindow({ offset: Math.max(0, documentView.offset - WINDOW_SIZE) })} type="button">← Previous section</button><div className="text-center text-[11px] leading-4 text-slate-500">HTML reconstruction from extracted content<br />Layout may differ from the source document.</div><button className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold disabled:opacity-40" disabled={loading || documentView.offset + documentView.count >= documentView.totalCount} onClick={() => void loadWindow({ offset: documentView.offset + WINDOW_SIZE })} type="button">Next section →</button></div> : null}
    </div>
  );
}
