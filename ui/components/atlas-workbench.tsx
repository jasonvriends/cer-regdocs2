"use client";

import { FormEvent, useMemo, useState } from "react";
import { AtlasAsk } from "@/components/atlas-ask";
import { AtlasGraph } from "@/components/atlas-graph";
import { AtlasTimeline } from "@/components/atlas-timeline";
import { ThemeSelector } from "@/components/theme-selector";
import type { IntelligenceScope } from "@/lib/intelligence";
import type {
  AtlasFacets,
  AtlasSearchResponse,
  AtlasSearchResult,
  AtlasSearchSort,
} from "@/lib/azure-search";

type SearchApiResponse = AtlasSearchResponse & { error?: string };
type LeftMode = "results" | "refine";
type RightMode = "ask" | "evidence" | "analyze";
type ViewerMode = "document" | "timeline" | "graph";
type FacetFilterKey =
  | "company"
  | "project"
  | "chunkType"
  | "applicationType"
  | "commodity"
  | "documentType"
  | "fileType"
  | "role";

type ManualFilters = {
  company: string;
  project: string;
  filingId: string;
  filingNumber: string;
  documentId: string;
  page: string;
};

type LensId =
  | "search"
  | "filing"
  | "company"
  | "project"
  | "document"
  | "what-happened"
  | "tables"
  | "figures"
  | "red-flags"
  | "obligations";

type Lens = {
  id: LensId;
  label: string;
  description: string;
  presetQuery?: string;
  chunkType?: "text" | "table" | "figure";
  sort?: AtlasSearchSort;
  opensRefine?: boolean;
};

const LENSES: Lens[] = [
  { id: "search", label: "Search", description: "Search the full regulatory corpus." },
  { id: "filing", label: "Filing Dossier", description: "Scope research to one filing.", presetQuery: "*", sort: "oldest", opensRefine: true },
  { id: "company", label: "Company", description: "Build a company dossier.", presetQuery: "*", sort: "newest", opensRefine: true },
  { id: "project", label: "Project", description: "Explore one project.", presetQuery: "*", sort: "oldest", opensRefine: true },
  { id: "document", label: "Document X-Ray", description: "Inspect one normalized document in chunk order.", presetQuery: "*", sort: "chunk", opensRefine: true },
  { id: "what-happened", label: "What Happened", description: "Retrieve chronology and event evidence.", presetQuery: "incident OR failure OR notice OR investigation OR response OR remediation OR order" },
  { id: "tables", label: "Tables", description: "Search normalized tables only.", presetQuery: "*", chunkType: "table" },
  { id: "figures", label: "Figures", description: "Search normalized figure text only.", presetQuery: "*", chunkType: "figure" },
  { id: "red-flags", label: "Red Flags", description: "Find investigation, violation, penalty, and noncompliance language.", presetQuery: '"material weakness" OR investigation OR noncompliance OR violation OR penalty' },
  { id: "obligations", label: "Obligations", description: "Find candidate duties, commitments, and filing requirements.", presetQuery: '"shall provide" OR "must submit" OR "undertakes to provide" OR "will file"' },
];

const FACET_DEFS: Array<{ field: string; filter: FacetFilterKey; label: string }> = [
  { field: "company", filter: "company", label: "Companies" },
  { field: "project", filter: "project", label: "Projects" },
  { field: "document_types", filter: "documentType", label: "Document types" },
  { field: "roles", filter: "role", label: "Roles" },
  { field: "commodities", filter: "commodity", label: "Commodities" },
  { field: "application_types", filter: "applicationType", label: "Application types" },
  { field: "chunk_type", filter: "chunkType", label: "Chunk types" },
  { field: "file_types", filter: "fileType", label: "File types" },
];

const EMPTY_FACET_FILTERS: Record<FacetFilterKey, string[]> = {
  company: [],
  project: [],
  chunkType: [],
  applicationType: [],
  commodity: [],
  documentType: [],
  fileType: [],
  role: [],
};

const EMPTY_MANUAL: ManualFilters = {
  company: "",
  project: "",
  filingId: "",
  filingNumber: "",
  documentId: "",
  page: "",
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

function titleFor(result: AtlasSearchResult) {
  return result.heading || result.title || `Document ${result.document_id}`;
}

export function AtlasWorkbench() {
  const [query, setQuery] = useState("");
  const [lensId, setLensId] = useState<LensId>("search");
  const [sort, setSort] = useState<AtlasSearchSort>("relevance");
  const [manual, setManual] = useState<ManualFilters>(EMPTY_MANUAL);
  const [facetFilters, setFacetFilters] = useState<Record<FacetFilterKey, string[]>>(EMPTY_FACET_FILTERS);
  const [facets, setFacets] = useState<AtlasFacets>({});
  const [results, setResults] = useState<AtlasSearchResult[]>([]);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<AtlasSearchResult[]>([]);
  const [leftMode, setLeftMode] = useState<LeftMode>("results");
  const [rightMode, setRightMode] = useState<RightMode>("ask");
  const [viewerMode, setViewerMode] = useState<ViewerMode>("document");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => results.find((result) => result.chunk_id === selectedChunkId) ?? results[0] ?? null,
    [results, selectedChunkId],
  );

  const activeLens = LENSES.find((lens) => lens.id === lensId) ?? LENSES[0];
  const intelligenceScope = useMemo<IntelligenceScope>(() => {
    if (manual.documentId.trim()) return { documentId: manual.documentId.trim() };
    if (manual.filingId.trim()) return { filingId: manual.filingId.trim() };
    if (manual.filingNumber.trim()) return { filingNumber: manual.filingNumber.trim() };
    if (manual.project.trim()) return { project: manual.project.trim() };
    if (manual.company.trim()) return { company: manual.company.trim() };
    if (selected?.filing_id) return { filingId: selected.filing_id };
    if (selected?.project) return { project: selected.project };
    if (selected?.company) return { company: selected.company };
    return {};
  }, [manual, selected]);
  const activeFilterCount =
    Object.values(facetFilters).reduce((count, values) => count + values.length, 0) +
    Object.values(manual).filter((value) => value.trim()).length;

  function buildParams() {
    const params = new URLSearchParams();
    params.set("q", query.trim() || "*");
    params.set("top", "30");
    params.set("sort", sort);

    const manualPairs: Array<[keyof ManualFilters, string]> = [
      ["company", "company"],
      ["project", "project"],
      ["filingId", "filingId"],
      ["filingNumber", "filingNumber"],
      ["documentId", "documentId"],
      ["page", "page"],
    ];
    for (const [field, parameter] of manualPairs) {
      const value = manual[field].trim();
      if (value) params.append(parameter, value);
    }

    for (const [parameter, values] of Object.entries(facetFilters)) {
      for (const value of values) params.append(parameter, value);
    }

    return params;
  }

  async function executeSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/search?${buildParams().toString()}`, { cache: "no-store" });
      const payload = (await response.json()) as SearchApiResponse;
      if (!response.ok) throw new Error(payload.error || "Search failed");

      setResults(payload.results);
      setTotalCount(payload.totalCount);
      setFacets(payload.facets ?? {});
      setSelectedChunkId(payload.results[0]?.chunk_id ?? null);
      setLeftMode("results");
    } catch (caught) {
      setResults([]);
      setTotalCount(null);
      setFacets({});
      setSelectedChunkId(null);
      setError(caught instanceof Error ? caught.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  function applyLens(lens: Lens) {
    setLensId(lens.id);
    if (lens.presetQuery !== undefined) setQuery(lens.presetQuery);
    if (lens.sort) setSort(lens.sort);
    if (lens.chunkType) {
      setFacetFilters((current) => ({ ...current, chunkType: [lens.chunkType!] }));
    } else if (lens.id !== "search") {
      setFacetFilters((current) => ({ ...current, chunkType: [] }));
    }
    if (lens.opensRefine) setLeftMode("refine");
  }

  function toggleFacet(filter: FacetFilterKey, value: string) {
    setFacetFilters((current) => {
      const selectedValues = current[filter];
      const next = selectedValues.includes(value)
        ? selectedValues.filter((item) => item !== value)
        : [...selectedValues, value];
      return { ...current, [filter]: next };
    });
  }

  function clearFilters() {
    setFacetFilters(EMPTY_FACET_FILTERS);
    setManual(EMPTY_MANUAL);
    setSort("relevance");
  }

  function addEvidence(result: AtlasSearchResult) {
    setEvidence((current) =>
      current.some((item) => item.chunk_id === result.chunk_id) ? current : [...current, result],
    );
    setRightMode("evidence");
  }

  function removeEvidence(chunkId: string) {
    setEvidence((current) => current.filter((item) => item.chunk_id !== chunkId));
  }

  function scopeToDocument(result: AtlasSearchResult) {
    setLensId("document");
    setQuery("*");
    setSort("chunk");
    setManual((current) => ({ ...current, documentId: result.document_id }));
    setFacetFilters((current) => ({ ...current, chunkType: [] }));
    setLeftMode("refine");
    setViewerMode("document");
  }

  function openEvidence(result: AtlasSearchResult) {
    setResults((current) => current.some((item) => item.chunk_id === result.chunk_id) ? current : [result, ...current]);
    setSelectedChunkId(result.chunk_id);
    setViewerMode("document");
  }

  async function openEvidenceById(chunkId: string) {
    try {
      const response = await fetch(`/api/evidence/${encodeURIComponent(chunkId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Evidence lookup failed");
      openEvidence((await response.json()) as AtlasSearchResult);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Evidence lookup failed");
    }
  }

  return (
    <main className="min-h-screen bg-slate-100 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto flex max-w-[1900px] items-center gap-5 px-5 py-4">
          <div className="min-w-fit">
            <div className="text-lg font-semibold tracking-tight">REGDOCS Atlas</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">Evidence-first regulatory research</div>
          </div>

          <form className="flex flex-1 gap-2" onSubmit={executeSearch}>
            <input
              aria-label="Search regulatory records"
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm outline-none ring-blue-500 transition focus:ring-2 dark:border-slate-700 dark:bg-slate-900 dark:placeholder:text-slate-500"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search regulatory records, or choose a lens below…"
              value={query}
            />
            <button
              className="rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:text-slate-950"
              disabled={loading}
              type="submit"
            >
              {loading ? "Searching…" : "Search"}
            </button>
          </form>

          <div className="flex min-w-fit gap-2">
            <select
              aria-label="Result sort"
              className="hidden rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900 lg:block"
              onChange={(event) => setSort(event.target.value as AtlasSearchSort)}
              value={sort}
            >
              <option value="relevance">Relevance</option>
              <option value="newest">Newest filing</option>
              <option value="oldest">Oldest filing</option>
              <option value="chunk">Document order</option>
            </select>
            <ThemeSelector />
          </div>
        </div>

        <div className="mx-auto flex max-w-[1900px] gap-2 overflow-x-auto px-5 pb-3">
          {LENSES.map((lens) => (
            <button
              className={`min-w-fit rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                lens.id === lensId
                  ? "border-slate-900 bg-slate-900 text-white dark:border-blue-500 dark:bg-blue-500 dark:text-slate-950"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-400 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-slate-500 dark:hover:text-white"
              }`}
              key={lens.id}
              onClick={() => applyLens(lens)}
              title={lens.description}
              type="button"
            >
              {lens.label}
            </button>
          ))}
        </div>
      </header>

      <div className="mx-auto grid min-h-[calc(100vh-126px)] max-w-[1900px] grid-cols-1 bg-white dark:bg-slate-900 lg:grid-cols-[370px_minmax(440px,1fr)_390px]">
        <section className="border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{activeLens.label}</div>
                <div className="mt-1 text-sm text-slate-700 dark:text-slate-300">
                  {totalCount !== null ? `${totalCount.toLocaleString()} matching chunks` : activeLens.description}
                </div>
              </div>
              <div className="flex rounded-lg bg-slate-100 p-1 text-xs dark:bg-slate-800">
                <button
                  className={`rounded-md px-2.5 py-1.5 ${leftMode === "results" ? "bg-white font-medium shadow-sm dark:bg-slate-700" : "text-slate-500 dark:text-slate-400"}`}
                  onClick={() => setLeftMode("results")}
                  type="button"
                >
                  Results
                </button>
                <button
                  className={`rounded-md px-2.5 py-1.5 ${leftMode === "refine" ? "bg-white font-medium shadow-sm dark:bg-slate-700" : "text-slate-500 dark:text-slate-400"}`}
                  onClick={() => setLeftMode("refine")}
                  type="button"
                >
                  Refine{activeFilterCount ? ` (${activeFilterCount})` : ""}
                </button>
              </div>
            </div>
          </div>

          {error ? (
            <div className="m-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">{error}</div>
          ) : null}

          {leftMode === "results" ? (
            <div className="max-h-[calc(100vh-198px)] overflow-y-auto">
              {!results.length && !error ? (
                <div className="p-5 text-sm leading-6 text-slate-500 dark:text-slate-400">
                  Choose a search lens or enter a query. Use <strong>Refine</strong> for filing, company, project, document, page, and facet filters.
                </div>
              ) : null}
              {results.map((result) => {
                const active = selected?.chunk_id === result.chunk_id;
                return (
                  <button
                    className={`block w-full border-b border-slate-100 p-4 text-left transition dark:border-slate-800 ${active ? "bg-blue-50 dark:bg-blue-950/50" : "hover:bg-slate-50 dark:hover:bg-slate-800/70"}`}
                    key={result.chunk_id}
                    onClick={() => setSelectedChunkId(result.chunk_id)}
                    type="button"
                  >
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-medium text-blue-700 dark:text-blue-300">{pageLabel(result)}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400">{result.chunk_type || "chunk"}</span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{titleFor(result)}</div>
                    <div className="mt-2 line-clamp-3 text-xs leading-5 text-slate-600 dark:text-slate-300">{excerpt(result.content, 230)}</div>
                    <div className="mt-2 truncate text-[11px] text-slate-400 dark:text-slate-500">{result.project || result.company || `Document ${result.document_id}`}</div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="max-h-[calc(100vh-198px)] overflow-y-auto p-4">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Exact scopes</div>
                <button className="text-xs font-medium text-blue-700 hover:underline dark:text-blue-300" onClick={clearFilters} type="button">Clear all</button>
              </div>

              <div className="mt-3 grid gap-2">
                {([
                  ["company", "Company name"],
                  ["project", "Project name"],
                  ["filingId", "Filing ID"],
                  ["filingNumber", "Filing number"],
                  ["documentId", "Document ID"],
                  ["page", "Page number"],
                ] as Array<[keyof ManualFilters, string]>).map(([field, placeholder]) => (
                  <input
                    className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-950 dark:placeholder:text-slate-500"
                    key={field}
                    onChange={(event) => setManual((current) => ({ ...current, [field]: event.target.value }))}
                    placeholder={placeholder}
                    type={field === "page" ? "number" : "text"}
                    value={manual[field]}
                  />
                ))}
              </div>

              <button
                className="mt-3 w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-blue-500 dark:text-slate-950"
                disabled={loading}
                onClick={() => void executeSearch()}
                type="button"
              >
                Apply scopes
              </button>

              <div className="mt-6 space-y-5">
                {FACET_DEFS.map((definition) => {
                  const buckets = facets[definition.field] ?? [];
                  if (!buckets.length) return null;
                  return (
                    <div key={definition.field}>
                      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">{definition.label}</div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {buckets.map((bucket) => {
                          const active = facetFilters[definition.filter].includes(bucket.value);
                          return (
                            <button
                              className={`rounded-full border px-2.5 py-1 text-xs transition ${
                                active ? "border-blue-700 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950/60 dark:text-blue-200" : "border-slate-200 bg-white text-slate-600 hover:border-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-slate-500"
                              }`}
                              key={bucket.value}
                              onClick={() => toggleFacet(definition.filter, bucket.value)}
                              type="button"
                            >
                              {bucket.value} <span className="text-slate-400 dark:text-slate-500">{bucket.count}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </section>

        <section className="min-w-0 bg-slate-100 dark:bg-slate-950">
          <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3 dark:border-slate-800 dark:bg-slate-900">
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Research viewer</div>
              <div className="mt-1 max-w-3xl truncate text-sm font-medium">
                {viewerMode === "document" ? selected?.title || (selected ? `Document ${selected.document_id}` : "No document selected") : viewerMode === "timeline" ? "Regulatory timeline" : "Regulatory graph"}
              </div>
            </div>
            <div className="flex min-w-fit gap-2">
              <div className="flex rounded-lg bg-slate-100 p-1 text-xs dark:bg-slate-800">
                {(["document", "timeline", "graph"] as ViewerMode[]).map((mode) => (
                  <button className={`rounded-md px-2.5 py-1.5 capitalize ${viewerMode === mode ? "bg-white font-medium shadow-sm dark:bg-slate-700" : "text-slate-500 dark:text-slate-400"}`} key={mode} onClick={() => setViewerMode(mode)} type="button">{mode}</button>
                ))}
              </div>
              {selected && viewerMode === "document" ? (
                <button className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800" onClick={() => scopeToDocument(selected)} type="button">
                  X-Ray document
                </button>
              ) : null}
              {selected?.source_url && viewerMode === "document" ? (
                <a className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800" href={selected.source_url} rel="noreferrer" target="_blank">REGDOCS source ↗</a>
              ) : null}
            </div>
          </div>

          <div className="flex min-h-[calc(100vh-182px)] items-start justify-center overflow-auto p-6">
            {viewerMode === "timeline" ? <AtlasTimeline scope={intelligenceScope} onOpenEvidence={(chunkId) => void openEvidenceById(chunkId)} /> : null}
            {viewerMode === "graph" ? <AtlasGraph scope={intelligenceScope} onOpenEvidence={(chunkId) => void openEvidenceById(chunkId)} /> : null}
            {viewerMode === "document" && selected ? (
              <article className="w-full max-w-[900px] rounded-sm border border-slate-300 bg-white px-10 py-10 shadow-sm dark:border-slate-700 dark:bg-slate-900 md:px-14 md:py-12">
                <div className="mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-5 dark:border-slate-800">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold uppercase tracking-widest text-slate-400 dark:text-slate-500">Document {selected.document_id}</div>
                    <h1 className="mt-2 text-xl font-semibold leading-7">{titleFor(selected)}</h1>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                      {selected.company ? <span>{selected.company}</span> : null}
                      {selected.project ? <span>· {selected.project}</span> : null}
                      {selected.filing_date ? <span>· {selected.filing_date}</span> : null}
                    </div>
                  </div>
                  <div className="min-w-fit rounded-md bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800 dark:bg-blue-950/60 dark:text-blue-200">{pageLabel(selected)}</div>
                </div>

                {selected.section_path?.length ? (
                  <div className="mb-4 text-xs text-slate-500 dark:text-slate-400">{selected.section_path.join(" › ")}</div>
                ) : null}

                <div className="whitespace-pre-wrap text-[15px] leading-7 text-slate-800 dark:text-slate-200">{selected.content || "No normalized text is available for this chunk."}</div>

                <div className="mt-8 flex flex-wrap gap-2 border-t border-slate-200 pt-5 dark:border-slate-800">
                  <button className="rounded-md bg-slate-900 px-3 py-2 text-xs font-medium text-white dark:bg-blue-500 dark:text-slate-950" onClick={() => addEvidence(selected)} type="button">+ Add to evidence</button>
                  <button className="rounded-md border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300" onClick={() => { setQuery(selected.content ? `\"${excerpt(selected.content, 90).replace(/\"/g, "")}\"` : titleFor(selected)); setLensId("search"); }} type="button">Find this passage</button>
                </div>

                <div className="mt-6 rounded-md bg-slate-50 p-4 text-xs leading-5 text-slate-500 dark:bg-slate-800/70 dark:text-slate-400">
                  This Phase 1 viewer renders normalized Stage 4 text with document/page identity. The original-page renderer and polygon overlays remain the next source-viewer milestone.
                </div>
              </article>
            ) : viewerMode === "document" ? (
              <div className="mt-24 max-w-md text-center">
                <div className="text-base font-semibold text-slate-700 dark:text-slate-300">Evidence viewer</div>
                <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">Run a search and select a result to inspect page-scoped regulatory evidence.</p>
              </div>
            ) : null}
          </div>
        </section>

        <aside className="border-l border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
            <div className="flex rounded-lg bg-slate-100 p-1 text-xs dark:bg-slate-800">
              {(["ask", "evidence", "analyze"] as RightMode[]).map((mode) => (
                <button
                  className={`flex-1 rounded-md px-2 py-1.5 capitalize ${rightMode === mode ? "bg-white font-medium shadow-sm dark:bg-slate-700" : "text-slate-500 dark:text-slate-400"}`}
                  key={mode}
                  onClick={() => setRightMode(mode)}
                  type="button"
                >
                  {mode}{mode === "evidence" && evidence.length ? ` (${evidence.length})` : ""}
                </button>
              ))}
            </div>
          </div>

          <div className="flex h-[calc(100vh-182px)] flex-col overflow-y-auto p-4">
            {rightMode === "ask" ? (
              <AtlasAsk evidence={evidence} onOpenEvidence={openEvidence} scope={intelligenceScope} selected={selected} />
            ) : null}

            {rightMode === "evidence" ? (
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Evidence board</div>
                <p className="mt-1 text-sm leading-5 text-slate-500 dark:text-slate-400">Pin passages while you investigate. This board is local to the current browser session.</p>
                <div className="mt-4 space-y-3">
                  {!evidence.length ? <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">Use “Add to evidence” on any retrieved passage.</div> : null}
                  {evidence.map((item) => (
                    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700" key={item.chunk_id}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="text-sm font-medium">{titleFor(item)}</div>
                        <button className="text-xs text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-200" onClick={() => removeEvidence(item.chunk_id)} type="button">Remove</button>
                      </div>
                      <div className="mt-1 text-xs text-blue-700 dark:text-blue-300">{pageLabel(item)} · Document {item.document_id}</div>
                      <div className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{excerpt(item.content, 260)}</div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {rightMode === "analyze" ? (
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">Investigation starters</div>
                <p className="mt-1 text-sm leading-5 text-slate-500 dark:text-slate-400">These run curated retrieval patterns against the same Azure Search index.</p>
                <div className="mt-4 grid gap-2">
                  {LENSES.filter((lens) => ["what-happened", "red-flags", "obligations", "tables", "figures"].includes(lens.id)).map((lens) => (
                    <button className="rounded-lg border border-slate-200 p-3 text-left hover:border-slate-400 hover:bg-slate-50 dark:border-slate-700 dark:hover:border-slate-500 dark:hover:bg-slate-800" key={lens.id} onClick={() => applyLens(lens)} type="button">
                      <div className="text-sm font-medium">{lens.label}</div>
                      <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{lens.description}</div>
                    </button>
                  ))}
                </div>
                <div className="mt-5 rounded-lg bg-blue-50 p-3 text-xs leading-5 text-blue-900 dark:bg-blue-950/60 dark:text-blue-200">
                  Chronology extraction, contradiction analysis, claim ledgers, and “Who Knew What When” become grounded LLM tools after Foundry is connected. Their retrieval foundation is now in this workbench.
                </div>
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </main>
  );
}
