"use client";

import { useEffect, useMemo, useState } from "react";
import type { AtlasEvent, IntelligenceScope } from "@/lib/intelligence";

type TimelineResponse = { events?: AtlasEvent[]; error?: string };

function scopeParams(scope: IntelligenceScope) {
  const params = new URLSearchParams();
  if (scope.documentId) params.set("documentId", scope.documentId);
  if (scope.filingId) params.set("filingId", scope.filingId);
  if (scope.filingNumber) params.set("filingNumber", scope.filingNumber);
  if (scope.company) params.set("company", scope.company);
  if (scope.project) params.set("project", scope.project);
  return params;
}

export function AtlasTimeline({
  scope,
  onOpenEvidence,
}: {
  scope: IntelligenceScope;
  onOpenEvidence?: (chunkId: string) => void;
}) {
  const [events, setEvents] = useState<AtlasEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestKey = useMemo(() => scopeParams(scope).toString(), [scope]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`/api/timeline?${requestKey}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as TimelineResponse;
        if (!response.ok) throw new Error(payload.error || "Timeline request failed");
        setEvents(payload.events || []);
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setEvents([]);
        setError(caught instanceof Error ? caught.message : "Timeline request failed");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [requestKey]);

  return (
    <div className="w-full max-w-[900px] rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Regulatory timeline</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Filing activity is deterministic; model-extracted occurrence dates are labeled separately and remain reviewable.
          </p>
        </div>
        <div className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {loading ? "Loading…" : `${events.length} events`}
        </div>
      </div>

      {error ? <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">{error}</div> : null}
      {!loading && !error && !events.length ? (
        <div className="mt-8 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          No timeline events are available for this scope yet.
        </div>
      ) : null}

      <div className="relative mt-6 space-y-0 before:absolute before:bottom-3 before:left-[5.25rem] before:top-3 before:w-px before:bg-slate-200 dark:before:bg-slate-700">
        {events.map((event) => (
          <article className="relative grid grid-cols-[4.5rem_1.5rem_1fr] gap-2 py-3" key={event.id}>
            <time className="pt-0.5 text-right text-xs font-medium text-slate-500 dark:text-slate-400">{event.date_start}</time>
            <div className="relative z-10 mx-auto mt-1 h-3 w-3 rounded-full border-2 border-white bg-blue-600 ring-2 ring-blue-100 dark:border-slate-900 dark:bg-blue-400 dark:ring-blue-950" />
            <div className="min-w-0 pb-3">
              <div className="text-sm font-semibold">{event.title}</div>
              <div className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-300">{event.summary}</div>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                <span>{event.event_type.replaceAll("_", " ")}</span>
                {event.filing_number ? <span>· {event.filing_number}</span> : null}
                {event.document_id ? <span>· Document {event.document_id}</span> : null}
                <span>· {event.date_basis.replaceAll("_", " ")}</span>
                {event.chunk_id && onOpenEvidence ? (
                  <button
                    className="font-medium text-blue-600 hover:underline dark:text-blue-400"
                    onClick={() => onOpenEvidence(event.chunk_id!)}
                    type="button"
                  >
                    Open evidence
                  </button>
                ) : null}
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
