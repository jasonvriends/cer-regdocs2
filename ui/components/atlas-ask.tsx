"use client";

import { FormEvent, useRef, useState } from "react";
import type { AtlasSearchResult } from "@/lib/azure-search";
import type { GroundedCitation } from "@/lib/foundry";
import type { IntelligenceScope } from "@/lib/intelligence";

type StreamEvent =
  | { type: "delta"; delta: string }
  | { type: "citation"; citation: GroundedCitation }
  | { type: "done"; evidenceCount: number; citationCount: number }
  | { type: "error"; error: string };

export function AtlasAsk({
  scope,
  evidence,
  selected,
  onOpenEvidence,
}: {
  scope: IntelligenceScope;
  evidence: AtlasSearchResult[];
  selected: AtlasSearchResult | null;
  onOpenEvidence: (result: AtlasSearchResult) => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<GroundedCitation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim() || loading) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setAnswer("");
    setCitations([]);
    setError(null);
    try {
      const evidenceIds = [...new Set([...evidence.map((item) => item.chunk_id), selected?.chunk_id].filter((item): item is string => Boolean(item)))];
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question.trim(), evidenceIds, scope }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const payload = (await response.json()) as { error?: string };
        throw new Error(payload.error || "Ask request failed");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const item = JSON.parse(line) as StreamEvent;
          if (item.type === "delta") setAnswer((current) => current + item.delta);
          if (item.type === "citation") setCitations((current) => current.some((citation) => citation.chunkId === item.citation.chunkId) ? current : [...current, item.citation]);
          if (item.type === "error") throw new Error(item.error);
        }
        if (done) break;
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : "Ask request failed");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function openCitation(citation: GroundedCitation) {
    try {
      const response = await fetch(`/api/evidence/${encodeURIComponent(citation.chunkId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Evidence lookup failed");
      onOpenEvidence((await response.json()) as AtlasSearchResult);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Evidence lookup failed");
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-xs leading-5 text-blue-900 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-200">
        Atlas retrieves scoped evidence first, then asks Foundry to answer only from those passages. Generated citations are validated against retrieved chunk IDs.
      </div>

      {answer ? <div className="mt-4 whitespace-pre-wrap text-sm leading-6 text-slate-700 dark:text-slate-200">{answer}</div> : null}
      {citations.length ? (
        <div className="mt-4 space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Validated citations</div>
          {citations.map((citation, index) => (
            <button className="block w-full rounded-lg border border-slate-200 p-3 text-left hover:border-blue-400 hover:bg-blue-50 dark:border-slate-700 dark:hover:border-blue-600 dark:hover:bg-blue-950/40" key={citation.chunkId} onClick={() => void openCitation(citation)} type="button">
              <div className="text-xs font-semibold text-blue-700 dark:text-blue-300">[{index + 1}] {citation.pageStart ? `Page ${citation.pageStart}` : "Page unknown"}</div>
              <div className="mt-1 line-clamp-2 text-xs text-slate-600 dark:text-slate-300">{citation.title}</div>
            </button>
          ))}
        </div>
      ) : null}
      {error ? <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">{error}</div> : null}

      <form className="mt-auto pt-4" onSubmit={ask}>
        <textarea aria-label="Ask Atlas" className="min-h-24 w-full resize-none rounded-lg border border-slate-300 bg-white p-3 text-sm outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-700 dark:bg-slate-950 dark:placeholder:text-slate-500" onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about the retrieved evidence…" value={question} />
        <div className="mt-2 flex gap-2">
          {loading ? <button className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm dark:border-slate-700" onClick={() => controllerRef.current?.abort()} type="button">Stop</button> : null}
          <button className="flex-1 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:text-slate-950" disabled={loading || !question.trim()} type="submit">{loading ? "Answering…" : "Ask with Foundry"}</button>
        </div>
      </form>
    </div>
  );
}
