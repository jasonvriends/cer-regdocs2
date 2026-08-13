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
  const hasScope = useMemo(() => Object.values(scope).some((value) => Boolean(value?.trim())), [scope]);

  useEffect(() => {
    if (!hasScope) {
      setEvents([]);
      setError(null);
      setLoading(false);
      return;
    }
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
  }, [hasScope, requestKey]);

  return (
    <div className="w-full rounded-xl border border-border bg-card p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Regulatory timeline</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Filing activity is deterministic; model-extracted occurrence dates are labeled separately and remain reviewable.
          </p>
        </div>
        <div className="rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
          {loading ? "Loading…" : `${events.length} events`}
        </div>
      </div>

      {error ? <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">{error}</div> : null}
      {!loading && !error && !events.length ? (
        <div className="mt-8 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          {hasScope ? "No timeline events are available for this scope yet." : "Choose a filing, company, project, or document filter, or open a source, to load its timeline."}
        </div>
      ) : null}

      <div className="relative mt-6 space-y-0 before:absolute before:bottom-3 before:left-[5.25rem] before:top-3 before:w-px before:bg-border">
        {events.map((event) => (
          <article className="relative grid grid-cols-[4.5rem_1.5rem_1fr] gap-2 py-3" key={event.id}>
            <time className="pt-0.5 text-right text-xs font-medium text-muted-foreground">{event.date_start}</time>
            <div className="relative z-10 mx-auto mt-1 h-3 w-3 rounded-full border-2 border-card bg-primary ring-2 ring-primary/15" />
            <div className="min-w-0 pb-3">
              <div className="text-sm font-semibold">{event.title}</div>
              <div className="mt-1 text-xs leading-5 text-muted-foreground">{event.summary}</div>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                <span>{event.event_type.replaceAll("_", " ")}</span>
                {event.filing_number ? <span>· {event.filing_number}</span> : null}
                {event.document_id ? <span>· Document {event.document_id}</span> : null}
                <span>· {event.date_basis.replaceAll("_", " ")}</span>
                {event.chunk_id && onOpenEvidence ? (
                  <button
                    className="font-medium text-primary hover:underline"
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
