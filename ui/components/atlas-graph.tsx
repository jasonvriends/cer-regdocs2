"use client";

import { useEffect, useMemo, useState } from "react";
import type { AtlasEntity, AtlasGraph as GraphData, IntelligenceScope } from "@/lib/intelligence";

type GraphResponse = GraphData & { error?: string };

const TYPE_ORDER = ["organization", "project", "filing", "document"];
const TYPE_COLOR: Record<string, string> = {
  organization: "#2563eb",
  project: "#7c3aed",
  filing: "#d97706",
  document: "#059669",
};

function scopeParams(scope: IntelligenceScope) {
  const params = new URLSearchParams();
  if (scope.documentId) params.set("documentId", scope.documentId);
  if (scope.filingId) params.set("filingId", scope.filingId);
  if (scope.filingNumber) params.set("filingNumber", scope.filingNumber);
  if (scope.company) params.set("company", scope.company);
  if (scope.project) params.set("project", scope.project);
  params.set("top", "120");
  return params;
}

function shortLabel(node: AtlasEntity) {
  return node.name.length > 34 ? `${node.name.slice(0, 32)}…` : node.name;
}

export function AtlasGraph({
  scope,
  onOpenEvidence,
}: {
  scope: IntelligenceScope;
  onOpenEvidence?: (chunkId: string) => void;
}) {
  const [graph, setGraph] = useState<GraphData>({ nodes: [], edges: [], truncated: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestKey = useMemo(() => scopeParams(scope).toString(), [scope]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(`/api/graph?${requestKey}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as GraphResponse;
        if (!response.ok) throw new Error(payload.error || "Graph request failed");
        setGraph(payload);
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setGraph({ nodes: [], edges: [], truncated: false });
        setError(caught instanceof Error ? caught.message : "Graph request failed");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [requestKey]);

  const layout = useMemo(() => {
    const visibleNodes = graph.nodes.slice(0, 80);
    const groups = new Map<string, AtlasEntity[]>();
    for (const node of visibleNodes) {
      const type = TYPE_ORDER.includes(node.entity_type) ? node.entity_type : "other";
      groups.set(type, [...(groups.get(type) || []), node]);
    }
    const columns = [...TYPE_ORDER, "other"].filter((type) => groups.has(type));
    const positions = new Map<string, { x: number; y: number }>();
    columns.forEach((type, column) => {
      const nodes = groups.get(type) || [];
      nodes.forEach((node, row) => {
        positions.set(node.id, {
          x: 100 + column * (760 / Math.max(columns.length - 1, 1)),
          y: 70 + row * Math.min(52, 520 / Math.max(nodes.length, 1)),
        });
      });
    });
    return { visibleNodes, positions };
  }, [graph.nodes]);
  const nodeNames = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node.name])),
    [graph.nodes],
  );

  return (
    <div className="w-full max-w-[1100px] rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Regulatory graph</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Evidence-backed organizations, projects, filings, and documents in the active scope.</p>
        </div>
        <div className="text-right text-xs text-slate-500 dark:text-slate-400">
          <div>{loading ? "Loading…" : `${graph.nodes.length} nodes · ${graph.edges.length} edges`}</div>
          {graph.truncated ? <div className="mt-1 text-amber-600 dark:text-amber-300">Scoped result truncated</div> : null}
        </div>
      </div>

      {error ? <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">{error}</div> : null}
      {!loading && !error && !graph.nodes.length ? (
        <div className="mt-8 rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          Choose a filing, company, project, or document scope to load its graph.
        </div>
      ) : null}

      {graph.nodes.length ? (
        <div className="mt-5 overflow-x-auto rounded-lg bg-slate-50 dark:bg-slate-950">
          <svg aria-label="Regulatory relationship graph" className="min-w-[900px]" role="img" viewBox="0 0 960 640">
            {graph.edges.map((edge) => {
              const source = layout.positions.get(edge.source_id);
              const target = layout.positions.get(edge.target_id);
              if (!source || !target) return null;
              return <line key={edge.id} stroke="currentColor" strokeOpacity="0.18" strokeWidth="1.5" x1={source.x} x2={target.x} y1={source.y} y2={target.y} />;
            })}
            {layout.visibleNodes.map((node) => {
              const point = layout.positions.get(node.id)!;
              return (
                <g key={node.id} transform={`translate(${point.x} ${point.y})`}>
                  <circle fill={TYPE_COLOR[node.entity_type] || "#64748b"} r="7" />
                  <text className="fill-slate-700 text-[10px] dark:fill-slate-300" dominantBaseline="middle" x="12">{shortLabel(node)}</text>
                  <title>{node.entity_type}: {node.name}</title>
                </g>
              );
            })}
          </svg>
        </div>
      ) : null}

      {graph.edges.length ? (
        <div className="mt-4 grid gap-2 md:grid-cols-2">
          {graph.edges.slice(0, 8).map((edge) => (
            <div className="rounded-md border border-slate-200 px-3 py-2 text-xs dark:border-slate-800" key={edge.id}>
              <div className="truncate text-slate-700 dark:text-slate-200">
                {nodeNames.get(edge.source_id) || edge.source_id}
                <span className="mx-1.5 text-slate-400">{edge.relationship_type.replaceAll("_", " ")}</span>
                {nodeNames.get(edge.target_id) || edge.target_id}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                <span>{edge.origin.replaceAll("_", " ")}</span>
                {edge.evidence_chunk_ids?.[0] && onOpenEvidence ? (
                  <button
                    className="font-medium text-blue-600 hover:underline dark:text-blue-400"
                    onClick={() => onOpenEvidence(edge.evidence_chunk_ids![0])}
                    type="button"
                  >
                    Open evidence
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3 text-[11px] text-slate-500 dark:text-slate-400">
        {TYPE_ORDER.map((type) => <span className="flex items-center gap-1.5" key={type}><span className="h-2.5 w-2.5 rounded-full" style={{ background: TYPE_COLOR[type] }} />{type}</span>)}
      </div>
    </div>
  );
}
