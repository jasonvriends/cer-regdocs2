"use client";

import { FormEvent, useMemo, useState } from "react";
import type {
  AtlasFacets,
  AtlasSearchMode,
  AtlasSearchResponse,
  AtlasSearchResult,
  AtlasSearchSort,
} from "@/lib/azure-search";
import { DocumentReader } from "@/components/document-reader";

type SearchApiResponse = AtlasSearchResponse & { error?: string };
type GroundedCitation = {
  id: string;
  title: string;
  documentId: string;
  filingNumber: string | null;
  pageStart: number | null;
  pageEnd: number | null;
  sourceUrl: string | null;
  excerpt: string;
};
type GroundedAnswerResponse = {
  answer?: string;
  citations?: GroundedCitation[];
  model?: string;
  evidenceCount?: number;
  retrievalMode?: string;
  scope?: string;
  error?: string;
};
type ViewMode = "discover" | "search" | "data" | "coverage";
type LeftMode = "results" | "refine";
type RightMode = "workspace" | "ask" | "tools";
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

type DiscoveryId =
  | "recent"
  | "decisions"
  | "project"
  | "company"
  | "events"
  | "tables"
  | "figures"
  | "schedule-a"
  | "obligations"
  | "red-flags";

type Discovery = {
  id: DiscoveryId;
  label: string;
  description: string;
  eyebrow: string;
  icon: string;
  presetQuery?: string;
  chunkType?: "text" | "table" | "figure";
  sort?: AtlasSearchSort;
  opensRefine?: boolean;
};

const DISCOVERIES: Discovery[] = [
  { id: "recent", label: "Recent filings", description: "See the newest material in this corpus.", eyebrow: "Keep current", icon: "↗", presetQuery: "*", sort: "newest" },
  { id: "decisions", label: "Decisions & orders", description: "Find determinations, orders, certificates, and reasons.", eyebrow: "Regulatory outcomes", icon: "§", presetQuery: "decision | order | certificate | reasons" },
  { id: "project", label: "Explore a project", description: "Build a complete, chronological project dossier.", eyebrow: "Follow the record", icon: "◇", presetQuery: "*", sort: "oldest", opensRefine: true },
  { id: "company", label: "Explore a company", description: "Find a company’s filings across projects and time.", eyebrow: "Know the filer", icon: "◎", presetQuery: "*", sort: "newest", opensRefine: true },
  { id: "events", label: "What happened?", description: "Retrieve incidents, responses, remediation, and chronology.", eyebrow: "Investigate", icon: "⌁", presetQuery: "incident | failure | notice | investigation | response | remediation | order" },
  { id: "tables", label: "Search tables", description: "Search extracted rows and tables—not only document titles.", eyebrow: "Find buried data", icon: "▦", presetQuery: "*", chunkType: "table" },
  { id: "figures", label: "Maps & figures", description: "Discover captions, diagrams, alignment sheets, and maps.", eyebrow: "See the evidence", icon: "▧", presetQuery: "*", chunkType: "figure" },
  { id: "schedule-a", label: "Schedule A", description: "Find Schedule A material across every filing in scope.", eyebrow: "Reusable dataset", icon: "A", presetQuery: '"Schedule A"' },
  { id: "obligations", label: "Commitments & duties", description: "Find candidate requirements and promised actions.", eyebrow: "Track compliance", icon: "✓", presetQuery: '"shall provide" | "must submit" | "undertakes to provide" | "will file"' },
  { id: "red-flags", label: "Red flags", description: "Find investigation, violation, penalty, and non-compliance language.", eyebrow: "Review risk", icon: "!", presetQuery: '"material weakness" | investigation | noncompliance | violation | penalty' },
];

const FACET_DEFS: Array<{ field: string; filter: FacetFilterKey; label: string }> = [
  { field: "company", filter: "company", label: "Companies" },
  { field: "project", filter: "project", label: "Projects" },
  { field: "document_types", filter: "documentType", label: "Document types" },
  { field: "roles", filter: "role", label: "Filed by" },
  { field: "commodities", filter: "commodity", label: "Commodities" },
  { field: "application_types", filter: "applicationType", label: "Application types" },
  { field: "chunk_type", filter: "chunkType", label: "Content types" },
  { field: "file_types", filter: "fileType", label: "File types" },
];

const CORPUS = {
  primaryStart: "January 1, 2026",
  primaryEnd: "July 31, 2026",
  records: 11108,
  documents: 6180,
  pages: 70519,
  tables: 42180,
  companies: 180,
  projects: 41,
  allDocuments: 8213,
  allPages: 90484,
  allTables: 52488,
  latestLinkedRecord: "August 7, 2026",
};

const MONTHS = [
  { month: "Jan", documents: 748, pages: 14300 },
  { month: "Feb", documents: 638, pages: 5742 },
  { month: "Mar", documents: 891, pages: 10890 },
  { month: "Apr", documents: 921, pages: 6018 },
  { month: "May", documents: 959, pages: 11891 },
  { month: "Jun", documents: 1232, pages: 14126 },
  { month: "Jul", documents: 791, pages: 7552 },
];

const EMPTY_FACET_FILTERS: Record<FacetFilterKey, string[]> = {
  company: [], project: [], chunkType: [], applicationType: [], commodity: [], documentType: [], fileType: [], role: [],
};

const EMPTY_MANUAL: ManualFilters = {
  company: "", project: "", filingId: "", filingNumber: "", documentId: "", page: "",
};

function pageLabel(result: AtlasSearchResult) {
  if (!result.page_start) return "Page unknown";
  if (!result.page_end || result.page_end === result.page_start) return `Page ${result.page_start}`;
  return `Pages ${result.page_start}–${result.page_end}`;
}

function excerpt(text?: string | null, length = 360) {
  if (!text) return "No text preview is available for this passage.";
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > length ? `${normalized.slice(0, length)}…` : normalized;
}

function titleFor(result: AtlasSearchResult) {
  return result.heading || result.title || `Document ${result.document_id}`;
}

function csvCell(value: unknown) {
  const normalized = value === null || value === undefined ? "" : String(value);
  return `"${normalized.replace(/"/g, '""')}"`;
}

function downloadCsv(items: AtlasSearchResult[], filename: string) {
  const headers = ["title", "document_id", "filing_number", "filing_date", "company", "project", "pages", "content_type", "excerpt", "source_url"];
  const rows = items.map((item) => [
    titleFor(item), item.document_id, item.filing_number, item.filing_date, item.company, item.project,
    pageLabel(item), item.chunk_type, item.content, item.source_url,
  ]);
  const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function AtlasWorkbench() {
  const [view, setView] = useState<ViewMode>("discover");
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<AtlasSearchMode>("keyword");
  const [sort, setSort] = useState<AtlasSearchSort>("relevance");
  const [manual, setManual] = useState<ManualFilters>(EMPTY_MANUAL);
  const [facetFilters, setFacetFilters] = useState<Record<FacetFilterKey, string[]>>(EMPTY_FACET_FILTERS);
  const [facets, setFacets] = useState<AtlasFacets>({});
  const [results, setResults] = useState<AtlasSearchResult[]>([]);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<AtlasSearchResult[]>([]);
  const [workspaceQuery, setWorkspaceQuery] = useState("");
  const [leftMode, setLeftMode] = useState<LeftMode>("results");
  const [rightMode, setRightMode] = useState<RightMode>("workspace");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [askQuestion, setAskQuestion] = useState("");
  const [askScope, setAskScope] = useState<"corpus" | "workspace">("corpus");
  const [askLoading, setAskLoading] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [groundedAnswer, setGroundedAnswer] = useState<GroundedAnswerResponse | null>(null);

  const selected = useMemo(
    () => results.find((result) => result.chunk_id === selectedChunkId) ?? results[0] ?? null,
    [results, selectedChunkId],
  );

  const filteredWorkspace = useMemo(() => {
    const needle = workspaceQuery.trim().toLowerCase();
    if (!needle) return workspace;
    return workspace.filter((item) =>
      [titleFor(item), item.content, item.company, item.project, item.filing_number, item.document_id]
        .some((value) => String(value ?? "").toLowerCase().includes(needle)),
    );
  }, [workspace, workspaceQuery]);

  const workspaceDocumentCount = new Set(workspace.map((item) => item.document_id)).size;
  const activeFilterCount =
    Object.values(facetFilters).reduce((count, values) => count + values.length, 0) +
    Object.values(manual).filter((value) => value.trim()).length;

  function buildParams() {
    const params = new URLSearchParams();
    params.set("q", query.trim() || "*");
    params.set("top", "30");
    params.set("sort", sort);
    params.set("mode", searchMode);

    const manualPairs: Array<[keyof ManualFilters, string]> = [
      ["company", "company"], ["project", "project"], ["filingId", "filingId"],
      ["filingNumber", "filingNumber"], ["documentId", "documentId"], ["page", "page"],
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

  async function runSearch(params: URLSearchParams) {
    setView("search");
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/search?${params.toString()}`, { cache: "no-store" });
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

  async function executeSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    await runSearch(buildParams());
  }

  function applyDiscovery(item: Discovery) {
    setView("search");
    setQuery(item.presetQuery ?? "*");
    setSort(item.sort ?? "relevance");
    setManual(EMPTY_MANUAL);
    setFacetFilters({ ...EMPTY_FACET_FILTERS, chunkType: item.chunkType ? [item.chunkType] : [] });
    setLeftMode(item.opensRefine ? "refine" : "results");
    if (!item.opensRefine) {
      const params = new URLSearchParams({
        q: item.presetQuery ?? "*",
        top: "30",
        sort: item.sort ?? "relevance",
        mode: "keyword",
      });
      if (item.chunkType) params.append("chunkType", item.chunkType);
      void runSearch(params);
    }
  }

  function toggleFacet(filter: FacetFilterKey, value: string) {
    setFacetFilters((current) => {
      const next = current[filter].includes(value)
        ? current[filter].filter((item) => item !== value)
        : [...current[filter], value];
      return { ...current, [filter]: next };
    });
  }

  function clearFilters() {
    setFacetFilters(EMPTY_FACET_FILTERS);
    setManual(EMPTY_MANUAL);
    setSort("relevance");
  }

  function addToWorkspace(result: AtlasSearchResult) {
    setWorkspace((current) => current.some((item) => item.chunk_id === result.chunk_id) ? current : [...current, result]);
    setRightMode("workspace");
  }

  function removeFromWorkspace(chunkId: string) {
    setWorkspace((current) => current.filter((item) => item.chunk_id !== chunkId));
  }

  function askFilters() {
    return {
      companies: [...facetFilters.company, ...(manual.company.trim() ? [manual.company.trim()] : [])],
      projects: [...facetFilters.project, ...(manual.project.trim() ? [manual.project.trim()] : [])],
      filingIds: manual.filingId.trim() ? [manual.filingId.trim()] : [],
      filingNumbers: manual.filingNumber.trim() ? [manual.filingNumber.trim()] : [],
      documentIds: manual.documentId.trim() ? [manual.documentId.trim()] : [],
      chunkTypes: facetFilters.chunkType,
      applicationTypes: facetFilters.applicationType,
      commodities: facetFilters.commodity,
      documentTypes: facetFilters.documentType,
      fileTypes: facetFilters.fileType,
      roles: facetFilters.role,
      page: manual.page.trim() ? Number.parseInt(manual.page, 10) : undefined,
    };
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = askQuestion.trim();
    if (question.length < 3) return;
    const useWorkspace = askScope === "workspace" && workspace.length > 0;
    setAskLoading(true);
    setAskError(null);
    setGroundedAnswer(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          searchMode,
          workspaceChunkIds: useWorkspace ? workspace.map((item) => item.chunk_id) : [],
          filters: useWorkspace ? {} : askFilters(),
        }),
      });
      const payload = (await response.json()) as GroundedAnswerResponse;
      if (!response.ok) throw new Error(payload.error || "The grounded answer failed");
      setGroundedAnswer(payload);
    } catch (caught) {
      setAskError(caught instanceof Error ? caught.message : "The grounded answer failed");
    } finally {
      setAskLoading(false);
    }
  }

  function openGroundedCitation(citation: GroundedCitation) {
    const passage = results.find((result) => result.document_id === citation.documentId && result.page_start === citation.pageStart)
      ?? workspace.find((result) => result.document_id === citation.documentId && result.page_start === citation.pageStart)
      ?? {
        chunk_id: `citation-${citation.id}-${citation.documentId}-${citation.pageStart ?? "unknown"}`,
        document_id: citation.documentId,
        chunk_type: "text",
        title: citation.title,
        heading: citation.title,
        content: citation.excerpt,
        filing_number: citation.filingNumber,
        page_start: citation.pageStart,
        page_end: citation.pageEnd,
        source_url: citation.sourceUrl,
        score: null,
      };
    if (!results.some((result) => result.chunk_id === passage.chunk_id)) {
      setResults((current) => [passage, ...current]);
    }
    setSelectedChunkId(passage.chunk_id);
    setQuery(askQuestion.trim());
    setView("search");
  }

  const navigation: Array<{ id: ViewMode; label: string }> = [
    { id: "discover", label: "Discover" }, { id: "search", label: "Search" },
    { id: "data", label: "Data products" }, { id: "coverage", label: "Coverage" },
  ];

  return (
    <main className="min-h-screen bg-[var(--atlas-canvas)] text-[var(--atlas-ink)]">
      <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1880px] items-center gap-6 px-5 py-3">
          <button className="min-w-fit text-left" onClick={() => setView("discover")} type="button">
            <div className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-teal-800 text-sm font-bold text-white">A</span>
              <div>
                <div className="text-[15px] font-bold tracking-tight">REGDOCS Atlas</div>
                <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400">CER research, made usable</div>
              </div>
            </div>
          </button>
          <nav aria-label="Primary" className="hidden items-center gap-1 lg:flex">
            {navigation.map((item) => (
              <button className={`rounded-md px-3 py-2 text-sm font-medium ${view === item.id ? "bg-teal-50 text-teal-900" : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"}`} key={item.id} onClick={() => setView(item.id)} type="button">{item.label}</button>
            ))}
          </nav>
          <form className="relative ml-auto flex max-w-2xl flex-1" onSubmit={executeSearch}>
            <span className="pointer-events-none absolute left-3.5 top-2.5 text-slate-400">⌕</span>
            <input aria-label="Search CER records" className="w-full rounded-l-lg border border-r-0 border-slate-300 bg-slate-50 py-2.5 pl-10 pr-3 text-sm outline-none focus:border-teal-700 focus:bg-white" onChange={(event) => setQuery(event.target.value)} placeholder="Search in plain language, by phrase, company, filing or document…" value={query} />
            <button className="rounded-r-lg bg-teal-800 px-5 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-60" disabled={loading} type="submit">{loading ? "Searching…" : "Search"}</button>
          </form>
          <button className="relative min-w-fit rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50" onClick={() => { setView("search"); setRightMode("workspace"); }} type="button">
            Workspace <span className="ml-1 rounded-full bg-teal-800 px-1.5 py-0.5 text-[10px] text-white">{workspace.length}</span>
          </button>
        </div>
      </header>

      {view === "discover" ? (
        <div className="mx-auto max-w-[1500px] px-6 py-9">
          <section className="overflow-hidden rounded-2xl bg-[var(--atlas-hero)] text-white shadow-sm">
            <div className="grid gap-8 px-7 py-10 lg:grid-cols-[1.45fr_0.75fr] lg:px-12 lg:py-12">
              <div>
                <div className="text-xs font-bold uppercase tracking-[0.22em] text-teal-200">Public regulatory evidence</div>
                <h1 className="mt-3 max-w-3xl text-3xl font-semibold leading-tight tracking-tight md:text-5xl">Find the fact, table, or filing that is hard to find.</h1>
                <p className="mt-4 max-w-2xl text-base leading-7 text-slate-200">Search inside CER documents—not just their titles. Explore by question, inspect the exact page, collect evidence, and export what you find.</p>
                <form className="mt-7 flex max-w-3xl rounded-xl bg-white p-1.5 shadow-xl shadow-slate-950/20" onSubmit={executeSearch}>
                  <input aria-label="Search the CER corpus" className="min-w-0 flex-1 rounded-lg px-4 py-3 text-sm text-slate-900 outline-none" onChange={(event) => setQuery(event.target.value)} placeholder="Try “watercourse crossings near wetlands” or C38090…" value={query} />
                  <button className="rounded-lg bg-amber-400 px-6 text-sm font-bold text-slate-950 hover:bg-amber-300" type="submit">Search records</button>
                </form>
                <div className="mt-3 text-xs text-slate-300">Tip: normal questions work. Use quotation marks when the exact wording matters.</div>
              </div>
              <button className="group self-stretch rounded-xl border border-white/15 bg-white/10 p-6 text-left backdrop-blur hover:bg-white/15" onClick={() => setView("coverage")} type="button">
                <div className="flex items-center justify-between"><span className="text-xs font-bold uppercase tracking-[0.18em] text-teal-200">Dataset coverage</span><span className="text-xl transition group-hover:translate-x-1">→</span></div>
                <div className="mt-4 text-2xl font-semibold">{CORPUS.primaryStart}</div>
                <div className="my-1 text-sm text-slate-300">through</div>
                <div className="text-2xl font-semibold">{CORPUS.primaryEnd}</div>
                <div className="mt-5 border-t border-white/15 pt-4 text-sm leading-6 text-slate-200">Complete primary collection. Linked records extend earlier, and an August update is also indexed.</div>
                <div className="mt-3 text-sm font-semibold text-amber-300">See what is included →</div>
              </button>
            </div>
          </section>

          <section aria-label="Corpus statistics" className="-mt-1 grid grid-cols-2 overflow-hidden rounded-b-xl border border-t-0 border-slate-200 bg-white shadow-sm md:grid-cols-4">
            {[
              [CORPUS.documents, "searchable documents", "downloaded and analyzed"], [CORPUS.pages, "pages", "full text and OCR"],
              [CORPUS.tables, "tables", "individually searchable"], [CORPUS.records, "registry records", "filings, folders, and files"],
            ].map(([value, label, note], index) => (
              <button className={`p-5 text-left hover:bg-teal-50/50 ${index ? "border-l border-slate-200" : ""}`} key={String(label)} onClick={() => setView("coverage")} type="button">
                <div className="text-2xl font-bold tracking-tight text-teal-900">{Number(value).toLocaleString()}</div>
                <div className="mt-0.5 text-sm font-semibold text-slate-800">{label}</div>
                <div className="mt-1 text-xs text-slate-400">{note}</div>
              </button>
            ))}
          </section>

          <div className="mt-10 flex items-end justify-between gap-4">
            <div><div className="text-xs font-bold uppercase tracking-[0.18em] text-teal-700">Start with a purpose</div><h2 className="mt-1 text-2xl font-semibold tracking-tight">What do you need to know?</h2></div>
            <button className="text-sm font-semibold text-teal-800 hover:underline" onClick={() => setView("search")} type="button">Open advanced search →</button>
          </div>
          <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {DISCOVERIES.map((item) => (
              <button className="group min-h-44 rounded-xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-teal-600 hover:shadow-md" key={item.id} onClick={() => applyDiscovery(item)} type="button">
                <div className="flex items-start justify-between"><span className="grid h-9 w-9 place-items-center rounded-lg bg-teal-50 text-lg font-bold text-teal-800">{item.icon}</span><span className="text-slate-300 transition group-hover:translate-x-1 group-hover:text-teal-700">→</span></div>
                <div className="mt-4 text-[10px] font-bold uppercase tracking-[0.15em] text-amber-700">{item.eyebrow}</div>
                <div className="mt-1 text-[15px] font-bold text-slate-900">{item.label}</div>
                <div className="mt-2 text-xs leading-5 text-slate-500">{item.description}</div>
              </button>
            ))}
          </section>

          <section className="mt-10 grid gap-5 lg:grid-cols-2">
            <button className="rounded-xl border border-slate-200 bg-white p-6 text-left shadow-sm hover:border-teal-500" onClick={() => { setView("search"); setRightMode("workspace"); }} type="button">
              <div className="text-xs font-bold uppercase tracking-[0.16em] text-teal-700">Research Workspace</div>
              <h3 className="mt-2 text-xl font-semibold">A shelf with a job to do</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">Collect page-level passages, search within only those items, export a cited evidence CSV, and ground a Microsoft Foundry answer in that exact set.</p>
              <div className="mt-4 text-sm font-bold text-teal-800">Open workspace ({workspace.length}) →</div>
            </button>
            <button className="rounded-xl border border-slate-200 bg-white p-6 text-left shadow-sm hover:border-teal-500" onClick={() => setView("data")} type="button">
              <div className="text-xs font-bold uppercase tracking-[0.16em] text-teal-700">Data products</div>
              <h3 className="mt-2 text-xl font-semibold">From documents to reusable datasets</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">Start with searchable tables today. Next, standardize recurring forms such as Schedule A into reviewed rows and columns that can be filtered and downloaded.</p>
              <div className="mt-4 text-sm font-bold text-teal-800">Explore data products →</div>
            </button>
          </section>
        </div>
      ) : null}

      {view === "coverage" ? (
        <div className="mx-auto max-w-[1300px] px-6 py-10">
          <div className="max-w-3xl"><div className="text-xs font-bold uppercase tracking-[0.18em] text-teal-700">Transparent by design</div><h1 className="mt-2 text-3xl font-semibold tracking-tight">What can I search?</h1><p className="mt-3 text-base leading-7 text-slate-600">Coverage is part of every answer. These counts describe the corpus in this Atlas build; they are not counts for all historical CER records.</p></div>
          <section className="mt-7 grid gap-4 md:grid-cols-3">
            <div className="rounded-xl bg-teal-900 p-6 text-white md:col-span-2"><div className="text-xs font-bold uppercase tracking-[0.16em] text-teal-200">Complete primary window</div><div className="mt-3 text-3xl font-semibold">Jan 1 – Jul 31, 2026</div><p className="mt-3 max-w-xl text-sm leading-6 text-teal-50">The primary scout collected this period completely and expanded compound filings and container relationships. Records dated August 1–7 and linked historical records are also present.</p></div>
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-6"><div className="text-xs font-bold uppercase tracking-[0.16em] text-amber-800">Important boundary</div><div className="mt-3 text-lg font-semibold text-slate-900">Not yet the full historical registry</div><p className="mt-2 text-sm leading-6 text-slate-600">Always show this date boundary beside exported results and Foundry-generated summaries.</p></div>
          </section>
          <section className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[[CORPUS.records,"registry records"],[CORPUS.documents,"analyzed documents"],[CORPUS.pages,"searchable pages"],[CORPUS.tables,"searchable tables"],[CORPUS.companies,"companies"],[CORPUS.projects,"projects"],[2515,"filing numbers"],[364,"submitters"]].map(([value,label]) => <div className="rounded-xl border border-slate-200 bg-white p-5" key={String(label)}><div className="text-2xl font-bold text-teal-900">{Number(value).toLocaleString()}</div><div className="mt-1 text-sm text-slate-500">{label}</div></div>)}
          </section>
          <section className="mt-5 rounded-xl border border-slate-200 bg-white p-6">
            <div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-lg font-semibold">Documents analyzed by month</h2><p className="mt-1 text-sm text-slate-500">Primary January–July collection</p></div><div className="text-xs text-slate-400">Bars show document count · labels include pages</div></div>
            <div className="mt-7 grid grid-cols-7 gap-3">
              {MONTHS.map((item) => <div className="flex min-w-0 flex-col items-center" key={item.month}><div className="text-xs font-semibold text-slate-600">{item.documents.toLocaleString()}</div><div className="mt-2 flex h-40 w-full items-end rounded-md bg-slate-50 px-2"><div className="w-full rounded-t bg-teal-700" style={{ height: `${Math.max(18, item.documents / 12.32)}%` }} /></div><div className="mt-2 text-sm font-bold">{item.month}</div><div className="hidden text-[10px] text-slate-400 sm:block">{item.pages.toLocaleString()} pp.</div></div>)}
            </div>
          </section>
          <section className="mt-5 rounded-xl border border-slate-200 bg-white p-6"><h2 className="text-lg font-semibold">How these numbers differ</h2><div className="mt-4 grid gap-4 md:grid-cols-3"><div><div className="text-sm font-bold">Registry record</div><p className="mt-1 text-sm leading-6 text-slate-500">A CER result can be a filing, folder, compound document, or downloadable file.</p></div><div><div className="text-sm font-bold">Searchable document</div><p className="mt-1 text-sm leading-6 text-slate-500">A downloaded file analyzed by Azure and published to the search index.</p></div><div><div className="text-sm font-bold">Passage</div><p className="mt-1 text-sm leading-6 text-slate-500">A page-scoped text, table, or figure chunk returned as a search result.</p></div></div></section>
        </div>
      ) : null}

      {view === "data" ? (
        <div className="mx-auto max-w-[1300px] px-6 py-10">
          <div className="max-w-3xl"><div className="text-xs font-bold uppercase tracking-[0.18em] text-teal-700">Reusable regulatory data</div><h1 className="mt-2 text-3xl font-semibold tracking-tight">Data products</h1><p className="mt-3 text-base leading-7 text-slate-600">The source record stays authoritative. Data products make recurring content comparable across documents, with every row linked back to its page.</p></div>
          <section className="mt-7 grid gap-4 lg:grid-cols-3">
            <article className="rounded-xl border-2 border-teal-700 bg-white p-6 shadow-sm lg:col-span-2"><div className="flex items-start justify-between gap-4"><div><div className="text-xs font-bold uppercase tracking-[0.16em] text-teal-700">Priority pilot</div><h2 className="mt-2 text-2xl font-semibold">Schedule A registry</h2></div><span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-bold text-amber-900">Discovery live</span></div><p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">Find every candidate “Schedule A,” then extract a schema appropriate to each Schedule A family. Human review and page-level provenance come before a dataset is labelled authoritative.</p><div className="mt-5 flex flex-wrap gap-2"><button className="rounded-lg bg-teal-800 px-4 py-2.5 text-sm font-bold text-white" onClick={() => applyDiscovery(DISCOVERIES.find((item) => item.id === "schedule-a")!)} type="button">Find Schedule A candidates</button><button className="rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700" disabled type="button">Download reviewed CSV · coming next</button></div></article>
            <aside className="rounded-xl bg-slate-900 p-6 text-white"><div className="text-xs font-bold uppercase tracking-[0.16em] text-teal-200">Quality contract</div><ul className="mt-4 space-y-3 text-sm leading-6 text-slate-200"><li>✓ Source document and exact page</li><li>✓ Extraction version and review status</li><li>✓ Original value beside normalized value</li><li>✓ CSV schema and data dictionary</li><li>✓ Coverage and exception counts</li></ul></aside>
          </section>
          <section className="mt-6"><h2 className="text-xl font-semibold">A scalable catalogue</h2><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[
            ["Commitments", "Owner, action, due date, status", "Candidate search live"], ["Conditions", "Condition, milestone, evidence", "Planned"], ["Information requests", "Question, response, undertaking", "Planned"], ["Consultation logs", "Party, issue, date, response", "Planned"], ["Watercourse tables", "Location, crossing, method, impact", "Tables searchable"], ["Financial schedules", "Period, category, amount, units", "Planned"], ["Incident timelines", "Event, date, actor, source", "Candidate search live"], ["Orders & decisions", "Instrument, date, company, outcome", "Candidate search live"],
          ].map(([title, fields, status]) => <article className="rounded-xl border border-slate-200 bg-white p-5" key={title}><div className="text-sm font-bold">{title}</div><div className="mt-2 text-xs leading-5 text-slate-500">{fields}</div><div className="mt-4 text-[10px] font-bold uppercase tracking-wider text-teal-700">{status}</div></article>)}</div></section>
        </div>
      ) : null}

      {view === "search" ? (
        <div className="mx-auto grid min-h-[calc(100vh-65px)] max-w-[1880px] grid-cols-1 bg-white lg:grid-cols-[380px_minmax(440px,1fr)_400px]">
          <section className="border-r border-slate-200 bg-white">
            <div className="border-b border-slate-200 p-4">
              <div className="flex items-center justify-between gap-3"><div><div className="text-xs font-bold uppercase tracking-wider text-teal-700">Search results</div><div className="mt-1 text-sm text-slate-700">{totalCount !== null ? `${totalCount.toLocaleString()} matching passages` : "Search within document text, tables, and figures"}</div></div><div className="flex rounded-lg bg-slate-100 p-1 text-xs"><button className={`rounded-md px-2.5 py-1.5 ${leftMode === "results" ? "bg-white font-semibold shadow-sm" : "text-slate-500"}`} onClick={() => setLeftMode("results")} type="button">Results</button><button className={`rounded-md px-2.5 py-1.5 ${leftMode === "refine" ? "bg-white font-semibold shadow-sm" : "text-slate-500"}`} onClick={() => setLeftMode("refine")} type="button">Filters{activeFilterCount ? ` (${activeFilterCount})` : ""}</button></div></div>
              <div className="mt-3 flex items-center gap-2"><select aria-label="Search method" className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-2 text-xs" onChange={(event) => setSearchMode(event.target.value as AtlasSearchMode)} value={searchMode}><option value="keyword">Keyword search</option><option value="hybrid">Hybrid meaning + keyword</option></select><select aria-label="Result sort" className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-2 text-xs" onChange={(event) => setSort(event.target.value as AtlasSearchSort)} value={sort}><option value="relevance">Most relevant</option><option value="newest">Newest filing</option><option value="oldest">Oldest filing</option><option value="chunk">Document order</option></select></div>
              {results.length ? <button className="mt-2 text-xs font-bold text-teal-700 hover:underline" onClick={() => downloadCsv(results, "regdocs-atlas-search-results.csv")} type="button">Export these {results.length} results as CSV</button> : null}
            </div>
            {error ? <div className="m-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{error}</div> : null}
            {leftMode === "results" ? <div className="max-h-[calc(100vh-180px)] overflow-y-auto">{!results.length && !error ? <div className="p-5"><div className="rounded-xl bg-teal-50 p-5"><div className="font-semibold text-teal-950">Start broad, then narrow</div><p className="mt-2 text-sm leading-6 text-teal-900">Enter a question above or choose a discovery path. Results are exact, page-scoped passages—not a list of entire PDFs.</p><button className="mt-3 text-sm font-bold text-teal-800" onClick={() => setView("discover")} type="button">Browse discovery paths →</button></div></div> : null}{results.map((result) => { const active = selected?.chunk_id === result.chunk_id; return <button className={`block w-full border-b border-slate-100 p-4 text-left transition ${active ? "border-l-4 border-l-teal-700 bg-teal-50/70" : "border-l-4 border-l-transparent hover:bg-slate-50"}`} key={result.chunk_id} onClick={() => setSelectedChunkId(result.chunk_id)} type="button"><div className="flex items-center justify-between gap-2 text-xs"><span className="font-bold text-teal-700">{pageLabel(result)}</span><span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase text-slate-500">{result.chunk_type || "text"}</span></div><div className="mt-1 line-clamp-2 text-sm font-bold text-slate-900">{titleFor(result)}</div><div className="mt-2 line-clamp-3 text-xs leading-5 text-slate-600">{excerpt(result.content, 230)}</div><div className="mt-2 truncate text-[11px] text-slate-400">{result.project || result.company || `Document ${result.document_id}`}</div></button>;})}</div> : <div className="max-h-[calc(100vh-180px)] overflow-y-auto p-4"><div className="flex items-center justify-between"><div className="text-xs font-bold uppercase tracking-wider text-slate-500">Narrow this search</div><button className="text-xs font-bold text-teal-700 hover:underline" onClick={clearFilters} type="button">Clear all</button></div><p className="mt-2 text-xs leading-5 text-slate-500">Filters combine with the words in the search box.</p><div className="mt-3 grid gap-2">{([ ["company","Company name"],["project","Project name"],["filingId","Filing ID"],["filingNumber","Filing number"],["documentId","Document ID"],["page","Page number"] ] as Array<[keyof ManualFilters,string]>).map(([field, placeholder]) => <input className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-teal-600" key={field} onChange={(event) => setManual((current) => ({ ...current, [field]: event.target.value }))} placeholder={placeholder} type={field === "page" ? "number" : "text"} value={manual[field]} />)}</div><button className="mt-3 w-full rounded-md bg-teal-800 px-3 py-2.5 text-sm font-bold text-white disabled:opacity-50" disabled={loading} onClick={() => void executeSearch()} type="button">Apply filters</button><div className="mt-6 space-y-5">{FACET_DEFS.map((definition) => { const buckets = facets[definition.field] ?? []; if (!buckets.length) return null; return <div key={definition.field}><div className="text-xs font-bold uppercase tracking-wider text-slate-500">{definition.label}</div><div className="mt-2 flex flex-wrap gap-1.5">{buckets.map((bucket) => { const active = facetFilters[definition.filter].includes(bucket.value); return <button className={`rounded-full border px-2.5 py-1 text-xs ${active ? "border-teal-700 bg-teal-50 text-teal-900" : "border-slate-200 text-slate-600 hover:border-slate-400"}`} key={bucket.value} onClick={() => toggleFacet(definition.filter, bucket.value)} type="button">{bucket.value} <span className="text-slate-400">{bucket.count}</span></button>;})}</div></div>;})}</div></div>}
          </section>

          <section className="flex h-[calc(100vh-65px)] min-w-0 flex-col bg-slate-100">
            {selected ? (
              <DocumentReader key={selected.document_id} onAddToWorkspace={addToWorkspace} query={query} selected={selected} />
            ) : (
              <div className="mt-24 max-w-md self-center px-6 text-center">
                <div className="text-base font-semibold text-slate-700">Choose a result to open the document</div>
                <p className="mt-2 text-sm leading-6 text-slate-500">Atlas reconstructs the document as readable HTML, jumps to the matching page, and highlights your search terms.</p>
              </div>
            )}
          </section>

          <aside className="border-l border-slate-200 bg-white">
            <div className="border-b border-slate-200 px-4 py-3"><div className="flex rounded-lg bg-slate-100 p-1 text-xs">{([ ["workspace",`Workspace${workspace.length ? ` (${workspace.length})` : ""}`],["ask","Ask"],["tools","Analyze"] ] as Array<[RightMode,string]>).map(([mode,label]) => <button className={`flex-1 rounded-md px-2 py-1.5 ${rightMode === mode ? "bg-white font-semibold shadow-sm" : "text-slate-500"}`} key={mode} onClick={() => setRightMode(mode)} type="button">{label}</button>)}</div></div>
            <div className="flex h-[calc(100vh-118px)] flex-col overflow-y-auto p-4">
              {rightMode === "workspace" ? <div><div className="flex items-start justify-between gap-3"><div><div className="text-xs font-bold uppercase tracking-wider text-teal-700">Research Workspace</div><p className="mt-1 text-sm leading-5 text-slate-500">Your focused set for review, export, and cited answers.</p></div>{workspace.length ? <button className="text-xs font-bold text-teal-700" onClick={() => downloadCsv(workspace, "regdocs-atlas-evidence.csv")} type="button">Export CSV</button> : null}</div>{workspace.length ? <div className="mt-4 grid grid-cols-2 gap-2"><div className="rounded-lg bg-teal-50 p-3"><div className="text-xl font-bold text-teal-900">{workspace.length}</div><div className="text-xs text-teal-800">passages</div></div><div className="rounded-lg bg-teal-50 p-3"><div className="text-xl font-bold text-teal-900">{workspaceDocumentCount}</div><div className="text-xs text-teal-800">documents</div></div></div> : null}<div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600"><strong>What it does:</strong> collect only the evidence relevant to your question, search inside that smaller set, download it with citations, or ask Foundry to answer from this set only.</div>{workspace.length ? <input aria-label="Search within workspace" className="mt-4 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-teal-600" onChange={(event) => setWorkspaceQuery(event.target.value)} placeholder="Search within workspace…" value={workspaceQuery} /> : null}<div className="mt-4 space-y-3">{!workspace.length ? <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm leading-6 text-slate-500">Use “Add to workspace” on a source passage. Your collection stays in this browser tab.</div> : null}{filteredWorkspace.map((item) => <div className="rounded-lg border border-slate-200 p-3" key={item.chunk_id}><div className="flex items-start justify-between gap-2"><button className="text-left text-sm font-semibold hover:text-teal-800" onClick={() => { setSelectedChunkId(item.chunk_id); if (!results.some((result) => result.chunk_id === item.chunk_id)) setResults((current) => [item, ...current]); }} type="button">{titleFor(item)}</button><button className="text-xs text-slate-400 hover:text-red-700" onClick={() => removeFromWorkspace(item.chunk_id)} type="button">Remove</button></div><div className="mt-1 text-xs font-semibold text-teal-700">{pageLabel(item)} · Document {item.document_id}</div><div className="mt-2 text-xs leading-5 text-slate-600">{excerpt(item.content, 220)}</div></div>)}</div></div> : null}
              {rightMode === "ask" ? <div className="flex min-h-full flex-col"><div className="flex items-center justify-between gap-2"><div className="text-xs font-bold uppercase tracking-wider text-teal-700">Ask with citations</div><span className="rounded-full bg-teal-50 px-2 py-1 text-[9px] font-bold uppercase text-teal-800">Foundry + AI Search</span></div><p className="mt-2 text-sm leading-6 text-slate-600">Atlas retrieves CER evidence first, then Microsoft Foundry answers only from those passages.</p><div className="mt-4 rounded-lg border border-slate-200 p-3"><label className="text-[10px] font-bold uppercase tracking-wider text-slate-400" htmlFor="answer-scope">Answer from</label><select className="mt-2 w-full rounded-md border border-slate-300 px-2 py-2 text-sm" id="answer-scope" onChange={(event) => setAskScope(event.target.value as "corpus" | "workspace")} value={askScope}><option value="corpus">Search corpus with current filters</option><option disabled={!workspace.length} value="workspace">Research Workspace ({workspace.length} passages)</option></select><div className="mt-2 text-xs leading-5 text-slate-500">{askScope === "workspace" && workspace.length ? "Only exact Workspace passages will be supplied." : `${searchMode === "hybrid" ? "Hybrid" : "Keyword"} retrieval will find up to 12 passages.`}</div></div>{askError ? <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-5 text-amber-950">{askError}</div> : null}{groundedAnswer?.answer ? <div className="mt-4"><div className="rounded-lg border border-teal-200 bg-teal-50/50 p-4"><div className="text-[10px] font-bold uppercase tracking-wider text-teal-700">Grounded answer</div><div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">{groundedAnswer.answer}</div><div className="mt-3 border-t border-teal-200 pt-2 text-[10px] text-slate-500">{groundedAnswer.evidenceCount} passages · {groundedAnswer.retrievalMode} · {groundedAnswer.model}</div></div><div className="mt-4 space-y-2"><div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Cited sources · open in reader</div>{groundedAnswer.citations?.map((citation) => <button className="block w-full rounded-lg border border-slate-200 p-3 text-left hover:border-teal-500 hover:bg-teal-50/30" key={citation.id} onClick={() => openGroundedCitation(citation)} type="button"><div className="flex items-center justify-between gap-2"><span className="text-xs font-bold text-teal-800">[{citation.id}]</span><span className="text-[10px] text-slate-400">{citation.pageStart ? `Page ${citation.pageStart}` : "Page unknown"}</span></div><div className="mt-1 text-xs font-semibold text-slate-800">{citation.title}</div><div className="mt-1 line-clamp-2 text-[11px] leading-4 text-slate-500">{citation.excerpt}</div></button>)}</div></div> : null}<form className="mt-auto pt-5" onSubmit={submitQuestion}><textarea aria-label="Ask Atlas" className="min-h-24 w-full resize-none rounded-lg border border-slate-300 bg-white p-3 text-sm outline-none focus:border-teal-600" maxLength={2000} onChange={(event) => setAskQuestion(event.target.value)} placeholder="What does the evidence say about…?" value={askQuestion} /><button className="mt-2 w-full rounded-lg bg-teal-800 px-4 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50" disabled={askLoading || askQuestion.trim().length < 3} type="submit">{askLoading ? "Retrieving and answering…" : "Ask with citations"}</button><div className="mt-2 text-center text-[10px] leading-4 text-slate-400">Primary complete coverage: Jan 1–Jul 31, 2026. Verify important conclusions against REGDOCS.</div></form></div> : null}
              {rightMode === "tools" ? <div><div className="text-xs font-bold uppercase tracking-wider text-teal-700">Investigation tools</div><p className="mt-1 text-sm leading-5 text-slate-500">Each tool has a defined output. Today they run transparent retrieval patterns.</p><div className="mt-4 grid gap-2">{DISCOVERIES.filter((item) => ["events","red-flags","obligations","tables","figures","schedule-a"].includes(item.id)).map((item) => <button className="rounded-lg border border-slate-200 p-3 text-left hover:border-teal-500 hover:bg-teal-50/40" key={item.id} onClick={() => applyDiscovery(item)} type="button"><div className="flex items-center justify-between"><div className="text-sm font-semibold">{item.label}</div><span className="rounded-full bg-teal-50 px-2 py-0.5 text-[9px] font-bold uppercase text-teal-700">Search</span></div><div className="mt-1 text-xs leading-5 text-slate-500">{item.description}</div></button>)}</div><div className="mt-5 rounded-lg bg-slate-900 p-4 text-xs leading-5 text-slate-200"><strong className="text-white">Foundry layer:</strong> chronology, contradiction checks, claim ledgers, and structured extraction belong here only after their output schema, citations, and evaluation threshold are defined.</div></div> : null}
            </div>
          </aside>
        </div>
      ) : null}
    </main>
  );
}
