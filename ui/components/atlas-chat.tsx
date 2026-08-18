"use client";

import { useCallback, useMemo, useState, type CSSProperties } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";
import { createLocalStorageAdapter, createSimpleTitleAdapter } from "@assistant-ui/core/react";
import {
  Clock3,
  Database,
  Download,
  FileText,
  GitFork,
  LibraryBig,
  Trash2,
} from "lucide-react";
import { AtlasGraph } from "@/components/atlas-graph";
import { AtlasRegulatoryRecords } from "@/components/atlas-regulatory-records";
import { AtlasTimeline } from "@/components/atlas-timeline";
import { AtlasContext, type AtlasScope } from "@/components/atlas-context";
import { AtlasLogo, AtlasSidebar, ResearchSidebarTrigger } from "@/components/atlas-sidebar";
import { AtlasSourceViewer } from "@/components/atlas-source-viewer";
import { AtlasThread } from "@/components/assistant-ui/atlas-thread";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { ThemeSelector } from "@/components/theme-selector";
import type { AtlasSearchResult } from "@/lib/azure-search";
import {
  EMPTY_FILTERS,
  exportEvidence,
  resultTitle,
  type AtlasCitation,
  type AtlasFilters,
  type AtlasRunInfo,
} from "@/lib/atlas-ui";
import type { IntelligenceScope } from "@/lib/intelligence";

type AnswerPayload = {
  answer?: string;
  citations?: AtlasCitation[];
  error?: string;
};

type AskStreamEvent =
  | { type: "delta"; delta: string }
  | { type: "evidence"; evidence: AtlasCitation[] }
  | { type: "citations"; citations: AtlasCitation[] }
  | {
      type: "done";
      foundry?: { used?: boolean; deployment?: string | null };
      retrievalMode?: string;
      retrievalFallbackFrom?: string | null;
      evidenceCount?: number;
      citationCount?: number;
      semanticApplied?: boolean;
      retryCount?: number;
      timings?: { retrievalMs?: number; foundryMs?: number; totalMs?: number };
      coverage?: AtlasRunInfo["coverage"];
    }
  | { type: "error"; error: string };

type CorpusStatusPayload = {
  indexName?: string;
  chunkCount?: number;
  earliestFilingDate?: string | null;
  latestFilingDate?: string | null;
  generatedAt?: string;
  error?: string;
};

type IntelligenceView = "timeline" | "graph" | "claims" | "obligations";

const atlasBrowserStorage = {
  async getItem(key: string) { return window.localStorage.getItem(key); },
  async setItem(key: string, value: string) { window.localStorage.setItem(key, value); },
  async removeItem(key: string) { window.localStorage.removeItem(key); },
};

const atlasThreadListAdapter = createLocalStorageAdapter({
  storage: atlasBrowserStorage,
  prefix: "regdocs-atlas:",
  titleGenerator: createSimpleTitleAdapter(),
});

function messageText(messages: Parameters<ChatModelAdapter["run"]>[0]["messages"]) {
  const message = [...messages].reverse().find((item) => item.role === "user");
  return message?.content.filter((part) => part.type === "text").map((part) => part.text).join("\n").trim() ?? "";
}

function intelligenceTitle(view: IntelligenceView | null) {
  if (view === "graph") return "Relationship graph";
  if (view === "claims") return "Findings & claims";
  if (view === "obligations") return "Commitments & obligations";
  return "Regulatory timeline";
}

export function AtlasChat({ defaultSidebarOpen = true }: { defaultSidebarOpen?: boolean }) {
  const [basket, setBasket] = useState<AtlasSearchResult[]>([]);
  const [filters, setFilters] = useState<AtlasFilters>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<AtlasFilters>(EMPTY_FILTERS);
  const [scope, setScope] = useState<AtlasScope>("corpus");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [coverageLoading, setCoverageLoading] = useState(false);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [corpusStatus, setCorpusStatus] = useState<CorpusStatusPayload | null>(null);
  const [dataOpen, setDataOpen] = useState(false);
  const [preview, setPreview] = useState<AtlasSearchResult | null>(null);
  const [intelligenceView, setIntelligenceView] = useState<IntelligenceView | null>(null);
  const [intelligenceError, setIntelligenceError] = useState<string | null>(null);

  const addEvidence = useCallback((item: AtlasSearchResult) => {
    setBasket((current) => current.some((entry) => entry.chunk_id === item.chunk_id) ? current : [...current, item]);
  }, []);

  const removeEvidence = useCallback((chunkId: string) => {
    setBasket((current) => current.filter((item) => item.chunk_id !== chunkId));
  }, []);

  const removeFilter = useCallback((key: string) => {
    if (key.startsWith("type:")) {
      const value = key.slice(5);
      setFilters((current) => ({ ...current, chunkTypes: current.chunkTypes.filter((item) => item !== value) }));
      return;
    }
    setFilters((current) => ({ ...current, [key]: "" }));
  }, []);

  const modelAdapter = useMemo<ChatModelAdapter>(() => ({
    async *run({ messages, abortSignal }) {
      const question = messageText(messages);
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Accept": "application/x-ndjson", "Content-Type": "application/json" },
        signal: abortSignal,
        body: JSON.stringify({
          question,
          workspaceChunkIds: scope === "evidence" ? basket.map((item) => item.chunk_id) : [],
          filters: scope === "corpus" ? {
            companies: filters.company ? [filters.company] : [],
            projects: filters.project ? [filters.project] : [],
            documentIds: filters.documentId ? [filters.documentId] : [],
            filingNumbers: filters.filingNumber ? [filters.filingNumber] : [],
            chunkTypes: filters.chunkTypes,
          } : {},
        }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json() as AnswerPayload;
        throw new Error(payload.error || "Atlas could not answer this question.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answer = "";
      let evidence: AtlasCitation[] = [];
      let citations: AtlasCitation[] = [];
      let runInfo: AtlasRunInfo | null = null;
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line) as AskStreamEvent;
          if (event.type === "error") throw new Error(event.error);
          if (event.type === "delta") answer += event.delta;
          if (event.type === "evidence") evidence = event.evidence;
          if (event.type === "citations") citations = event.citations;
          if (event.type === "done") {
            runInfo = {
              foundryUsed: event.foundry?.used === true,
              deployment: event.foundry?.deployment ?? null,
              retrievalMode: event.retrievalMode || "unknown",
              retrievalFallbackFrom: event.retrievalFallbackFrom ?? null,
              evidenceCount: event.evidenceCount ?? evidence.length,
              citationCount: event.citationCount ?? citations.length,
              semanticApplied: event.semanticApplied === true,
              retryCount: event.retryCount ?? 0,
              timings: {
                retrievalMs: event.timings?.retrievalMs ?? 0,
                foundryMs: event.timings?.foundryMs ?? 0,
                totalMs: event.timings?.totalMs ?? 0,
              },
              coverage: event.coverage ?? null,
            };
          }
          if (["delta", "evidence", "citations", "done"].includes(event.type)) {
            const sourcePart = citations.length
              ? [{ type: "data" as const, name: "regdocs-citations", data: citations }]
              : evidence.length
                ? [{ type: "data" as const, name: "regdocs-evidence", data: evidence }]
                : [];
            yield {
              content: [
                { type: "text", text: answer },
                ...sourcePart,
                ...(runInfo ? [{ type: "data" as const, name: "regdocs-run-info", data: runInfo }] : []),
              ],
            };
          }
        }
        if (done) break;
      }
      if (!answer.trim()) throw new Error("The evidence did not produce an answer.");
    },
  }), [basket, filters, scope]);

  const runtime = useRemoteThreadListRuntime({
    runtimeHook: () => useLocalRuntime(modelAdapter),
    adapter: atlasThreadListAdapter,
  });

  const activeFilters = useMemo(() => {
    const values: Array<{ key: string; label: string }> = [];
    if (filters.company) values.push({ key: "company", label: filters.company });
    if (filters.project) values.push({ key: "project", label: filters.project });
    if (filters.filingNumber) values.push({ key: "filingNumber", label: `Filing ${filters.filingNumber}` });
    if (filters.documentId) values.push({ key: "documentId", label: `Document ${filters.documentId}` });
    filters.chunkTypes.forEach((value) => values.push({ key: `type:${value}`, label: value }));
    return values;
  }, [filters]);

  const intelligenceScope = useMemo<IntelligenceScope>(() => {
    const filteredScope: IntelligenceScope = {
      documentId: filters.documentId || undefined,
      filingNumber: filters.filingNumber || undefined,
      company: filters.company || undefined,
      project: filters.project || undefined,
    };
    if (Object.values(filteredScope).some(Boolean)) return filteredScope;

    const source = preview ?? basket[0];
    if (!source) return {};
    return {
      documentId: source.document_id || undefined,
      filingId: source.filing_id || undefined,
      filingNumber: source.filing_number || undefined,
      company: source.company || undefined,
      project: source.project || undefined,
    };
  }, [basket, filters, preview]);

  const intelligenceScopeLabel = useMemo(() => {
    const labels = [
      intelligenceScope.documentId ? `Document ${intelligenceScope.documentId}` : null,
      intelligenceScope.filingNumber ? `Filing ${intelligenceScope.filingNumber}` : null,
      intelligenceScope.company,
      intelligenceScope.project,
    ].filter((value): value is string => Boolean(value));
    return labels.join(" · ");
  }, [intelligenceScope]);

  async function openIntelligenceEvidence(chunkId: string) {
    setIntelligenceError(null);
    try {
      const response = await fetch(`/api/evidence/${encodeURIComponent(chunkId)}`, { cache: "no-store" });
      const payload = await response.json() as AtlasSearchResult & { error?: string };
      if (!response.ok) throw new Error(payload.error || "Evidence lookup failed");
      setPreview(payload);
      setIntelligenceView(null);
    } catch (caught) {
      setIntelligenceError(caught instanceof Error ? caught.message : "Evidence lookup failed");
    }
  }

  async function loadCoverage() {
    setCoverageLoading(true);
    setCoverageError(null);
    try {
      const response = await fetch("/api/corpus-status", { cache: "no-store" });
      const payload = await response.json() as CorpusStatusPayload;
      if (!response.ok) throw new Error(payload.error || "Corpus status could not be loaded.");
      setCorpusStatus(payload);
    } catch (caught) {
      setCoverageError(caught instanceof Error ? caught.message : "Corpus status could not be loaded.");
    } finally {
      setCoverageLoading(false);
    }
  }

  const context = useMemo(() => ({
    basket,
    filters,
    scope,
    addEvidence,
    removeEvidence,
    openFilters: () => { setDraftFilters(filters); setFiltersOpen(true); },
    openSource: setPreview,
    setScope,
  }), [addEvidence, basket, filters, removeEvidence, scope]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AtlasContext.Provider value={context}>
        <SidebarProvider
          className="h-dvh min-h-0 overflow-hidden bg-muted/25 text-foreground"
          defaultOpen={defaultSidebarOpen}
          style={{ "--sidebar-width": "18rem", "--sidebar-width-icon": "3.5rem" } as CSSProperties}
        >
          <a className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-md bg-foreground px-4 py-2 text-sm font-semibold text-background shadow-lg transition-transform focus:translate-y-0" href="#atlas-main-content">Skip to main content</a>
          <AtlasSidebar
            onClaims={() => { setIntelligenceError(null); setIntelligenceView("claims"); }}
            onCoverage={() => { setCoverageOpen(true); void loadCoverage(); }}
            onDataset={() => setDataOpen(true)}
            onGraph={() => { setIntelligenceError(null); setIntelligenceView("graph"); }}
            onObligations={() => { setIntelligenceError(null); setIntelligenceView("obligations"); }}
            onShelf={() => setEvidenceOpen(true)}
            onTimeline={() => { setIntelligenceError(null); setIntelligenceView("timeline"); }}
            shelfCount={basket.length}
          />

          <SidebarInset className="min-h-0 min-w-0 overflow-hidden" id="atlas-main-content" tabIndex={-1}>
            <header className="z-20 flex h-14 shrink-0 items-center border-b bg-background/90 px-3 backdrop-blur-xl sm:px-4">
              <ResearchSidebarTrigger className="mr-2" />
              <div className="md:hidden"><AtlasLogo compact /></div>
              <div className="hidden md:block"><div className="text-sm font-semibold">Research workspace</div><div className="text-[10px] text-muted-foreground">Ask, verify, collect</div></div>
              <div className="ml-auto flex items-center gap-2">
                <Badge className="hidden rounded-full font-normal sm:inline-flex" variant="outline">{scope === "evidence" ? `Shelf only · ${basket.length}` : "All CER records"}</Badge>
                <Button aria-label="Open regulatory timeline" className="hidden sm:inline-flex" onClick={() => setIntelligenceView("timeline")} size="icon-sm" title="Regulatory timeline" variant="ghost"><Clock3 className="size-4" /></Button>
                <Button aria-label="Open relationship graph" className="hidden sm:inline-flex" onClick={() => setIntelligenceView("graph")} size="icon-sm" title="Relationship graph" variant="ghost"><GitFork className="size-4" /></Button>
                <ThemeSelector />
                <Button className="gap-1.5 md:hidden" onClick={() => setEvidenceOpen(true)} size="sm" variant="outline"><LibraryBig className="size-4" />Shelf<Badge className="ml-0.5 min-w-4 rounded-full px-1 text-[9px]" variant="secondary">{basket.length}</Badge></Button>
              </div>
            </header>
            {activeFilters.length ? <div aria-label="Active search filters" className="flex shrink-0 flex-wrap items-center gap-1.5 border-b bg-muted/35 px-4 py-2 sm:px-6" role="region"><span className="mr-1 text-[11px] font-medium text-muted-foreground">Searching within</span>{activeFilters.map((item) => <Button aria-label={`Remove ${item.label} filter`} className="h-7 rounded-full px-2.5 text-xs font-normal" key={item.key} onClick={() => removeFilter(item.key)} size="sm" variant="outline">{item.label}<span aria-hidden="true">×</span></Button>)}</div> : null}
            <div className="relative min-h-0 flex-1"><AtlasThread /></div>
          </SidebarInset>

          {preview ? <aside className="fixed inset-0 z-50 flex min-w-0 bg-background lg:static lg:z-auto lg:w-[54vw] lg:max-w-[920px] lg:shrink-0 lg:border-l"><AtlasSourceViewer item={preview} onAddEvidence={addEvidence} onClose={() => setPreview(null)} /></aside> : null}

          <Dialog onOpenChange={setFiltersOpen} open={filtersOpen}>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader><DialogTitle>Narrow the evidence</DialogTitle><DialogDescription>Use only what you know. Leave everything else blank.</DialogDescription></DialogHeader>
              <div className="grid gap-4 py-2 sm:grid-cols-2">
                {[{ key: "company", label: "Company", placeholder: "e.g. Enbridge" }, { key: "project", label: "Project", placeholder: "Project name" }, { key: "filingNumber", label: "Filing number", placeholder: "e.g. C38090" }, { key: "documentId", label: "Document ID", placeholder: "Exact ID" }].map((field) => <label className="grid gap-1.5 text-xs font-medium" key={field.key}>{field.label}<Input onChange={(event) => setDraftFilters((current) => ({ ...current, [field.key]: event.target.value }))} placeholder={field.placeholder} value={draftFilters[field.key as keyof Omit<AtlasFilters, "chunkTypes">]} /></label>)}
              </div>
              <div><div className="text-xs font-medium">Content to include</div><div className="mt-2 flex flex-wrap gap-2">{[["text", "Documents"], ["table", "Tables"], ["figure", "Figures & maps"]].map(([value, label]) => <Button key={value} onClick={() => setDraftFilters((current) => ({ ...current, chunkTypes: current.chunkTypes.includes(value) ? current.chunkTypes.filter((item) => item !== value) : [...current.chunkTypes, value] }))} size="sm" variant={draftFilters.chunkTypes.includes(value) ? "secondary" : "outline"}>{label}</Button>)}</div></div>
              <DialogFooter className="mt-2"><Button onClick={() => setDraftFilters(EMPTY_FILTERS)} variant="ghost">Clear all</Button><Button onClick={() => { setFilters(draftFilters); setFiltersOpen(false); }}>Apply filters</Button></DialogFooter>
            </DialogContent>
          </Dialog>

          <Sheet onOpenChange={setEvidenceOpen} open={evidenceOpen}>
            <SheetContent className="w-full gap-0 sm:max-w-md">
              <SheetHeader className="border-b p-5"><SheetTitle className="flex items-center gap-2 text-lg"><LibraryBig className="size-5" />Shelf</SheetTitle><SheetDescription>Save the exact evidence you want to question, compare, or export.</SheetDescription></SheetHeader>
              <div className="min-h-0 flex-1 overflow-y-auto p-4">
                {basket.length ? <div className="space-y-2">{basket.map((item, index) => <div className="rounded-xl border bg-card p-3" key={item.chunk_id}><div className="flex items-start gap-3"><Badge className="mt-0.5 shrink-0">{index + 1}</Badge><button className="min-w-0 flex-1 text-left" onClick={() => setPreview(item)} type="button"><div className="line-clamp-2 text-sm font-semibold">{resultTitle(item)}</div><div className="mt-1 text-xs text-muted-foreground">Document {item.document_id}{item.page_start ? ` · Page ${item.page_start}` : ""}</div></button><Button aria-label="Remove from shelf" onClick={() => removeEvidence(item.chunk_id)} size="icon-sm" variant="ghost"><Trash2 className="size-4" /></Button></div></div>)}</div> : <div className="grid h-full place-items-center px-8 text-center"><div><div className="mx-auto grid size-12 place-items-center rounded-2xl bg-muted text-muted-foreground"><LibraryBig className="size-5" /></div><div className="mt-4 font-semibold">Your shelf is empty</div><p className="mt-2 text-sm leading-6 text-muted-foreground">Ask a question, then add the source passages worth keeping.</p></div></div>}
              </div>
              <SheetFooter className="border-t bg-muted/35"><Button disabled={!basket.length} onClick={() => { setScope("evidence"); runtime.thread.composer.setText("What does the evidence on my shelf establish, where does it conflict, and what gaps remain?"); setEvidenceOpen(false); }}>Ask this shelf</Button><Button disabled={!basket.length} onClick={() => exportEvidence(basket)} variant="outline"><Download className="size-4" />Export shelf</Button></SheetFooter>
            </SheetContent>
          </Sheet>

          <Dialog onOpenChange={setCoverageOpen} open={coverageOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle className="text-xl">Corpus coverage</DialogTitle>
                <DialogDescription>Live metadata from the currently configured Azure AI Search index.</DialogDescription>
              </DialogHeader>
              {coverageLoading ? <p className="text-sm text-muted-foreground">Loading current index coverage…</p> : null}
              {coverageError ? <div className="rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">{coverageError}</div> : null}
              {corpusStatus ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-xl bg-muted/60 p-4"><div className="text-xl font-semibold">{(corpusStatus.chunkCount ?? 0).toLocaleString()}</div><div className="text-xs text-muted-foreground">indexed chunks</div></div>
                  <div className="rounded-xl bg-muted/60 p-4"><div className="truncate text-sm font-semibold">{corpusStatus.indexName || "unknown"}</div><div className="text-xs text-muted-foreground">Search index</div></div>
                  <div className="rounded-xl bg-muted/60 p-4 sm:col-span-2"><div className="text-sm font-semibold">{corpusStatus.earliestFilingDate || "unknown"} → {corpusStatus.latestFilingDate || "unknown"}</div><div className="text-xs text-muted-foreground">filing dates represented in the index</div></div>
                </div>
              ) : null}
              <p className="text-xs leading-5 text-muted-foreground">Atlas can only answer from indexed evidence. Verify material decisions against the original REGDOCS source.</p>
            </DialogContent>
          </Dialog>

          <Dialog onOpenChange={setDataOpen} open={dataOpen}>
            <DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle className="text-xl">Make a data product</DialogTitle><DialogDescription>Start with a useful shape; Atlas will find the underlying evidence first.</DialogDescription></DialogHeader><div className="space-y-2">{[["Schedule A table inventory", "Find candidate tables and reusable columns."], ["Source inventory", "Document, filing, page, company, project and link."], ["Shelf CSV", "Export the source passages currently on your shelf."]].map(([title, description], index) => <Button className="h-auto w-full justify-start gap-3 rounded-xl border px-4 py-3 text-left" disabled={index === 2 && !basket.length} key={title} onClick={() => { if (index === 0) { setFilters((current) => ({ ...current, chunkTypes: ["table"] })); runtime.thread.composer.setText("Find Schedule A tables and identify the columns needed for a reusable CSV dataset."); } else if (index === 1) { runtime.thread.composer.setText("Create a source inventory with document, filing, page, company, project, and source link."); } else { exportEvidence(basket); } setDataOpen(false); }} variant="ghost"><span className="grid size-9 place-items-center rounded-lg bg-primary/8 text-primary">{index === 2 ? <Download className="size-4" /> : index === 1 ? <FileText className="size-4" /> : <Database className="size-4" />}</span><span><strong className="block text-sm">{title}</strong><small className="text-xs text-muted-foreground">{description}</small></span></Button>)}</div></DialogContent>
          </Dialog>

          <Dialog onOpenChange={(open) => { if (!open) setIntelligenceView(null); }} open={intelligenceView !== null}>
            <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-[min(1100px,calc(100vw-2rem))]">
              <DialogHeader className="pr-10">
                <DialogTitle className="text-xl">{intelligenceTitle(intelligenceView)}</DialogTitle>
                <DialogDescription>
                  {intelligenceScopeLabel ? `Active scope: ${intelligenceScopeLabel}` : "Use an active filter, an open source, or a saved shelf source to set the research scope."}
                </DialogDescription>
              </DialogHeader>
              {!intelligenceScopeLabel ? <div><Button onClick={() => { setIntelligenceView(null); setDraftFilters(filters); setFiltersOpen(true); }} size="sm" variant="outline">Set a research filter</Button></div> : null}
              {intelligenceError ? <div className="rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">{intelligenceError}</div> : null}
              {intelligenceView === "graph" ? <AtlasGraph onOpenEvidence={(chunkId) => void openIntelligenceEvidence(chunkId)} scope={intelligenceScope} /> : null}
              {intelligenceView === "timeline" ? <AtlasTimeline onOpenEvidence={(chunkId) => void openIntelligenceEvidence(chunkId)} scope={intelligenceScope} /> : null}
              {intelligenceView === "claims" ? <AtlasRegulatoryRecords mode="claims" onOpenEvidence={(chunkId) => void openIntelligenceEvidence(chunkId)} scope={intelligenceScope} /> : null}
              {intelligenceView === "obligations" ? <AtlasRegulatoryRecords mode="obligations" onOpenEvidence={(chunkId) => void openIntelligenceEvidence(chunkId)} scope={intelligenceScope} /> : null}
            </DialogContent>
          </Dialog>

        </SidebarProvider>
      </AtlasContext.Provider>
    </AssistantRuntimeProvider>
  );
}
