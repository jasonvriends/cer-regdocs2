"use client";

import type { DataMessagePartProps } from "@assistant-ui/react";
import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAssistantDataUI,
  useAui,
} from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  FileSearch,
  Filter,
  LibraryBig,
  LoaderCircle,
  Plus,
  Square,
} from "lucide-react";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { useAtlas } from "@/components/atlas-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { AtlasCitation, AtlasRunInfo } from "@/lib/atlas-ui";
import { citationToResult } from "@/lib/atlas-ui";

const STARTERS = [
  ["Summarize a filing", "Summarize the key findings and regulatory outcome in this filing."],
  ["Build a timeline", "What happened, in chronological order, and what did the regulator require next?"],
  ["Find Schedule A tables", "Find Schedule A tables and explain what fields they contain."],
  ["Track commitments", "What commitments or conditions must be completed, and by when?"],
] as const;

function Welcome() {
  const aui = useAui();

  function sendPrompt(prompt: string) {
    const composer = aui.composer();
    composer.setText(prompt);
    composer.send();
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center pb-8 text-center">
      <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-primary/15 bg-primary/5 text-primary">
        <FileSearch className="size-5" />
      </div>
      <h1 className="mt-5 text-3xl font-semibold tracking-[-0.035em] text-balance sm:text-5xl">What do you need to know?</h1>
      <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-muted-foreground sm:text-base">
        Ask naturally. Atlas reads the CER record, answers with page-level citations, and keeps the evidence attached.
      </p>
      <div className="mt-9 grid gap-2 text-left sm:grid-cols-2">
        {STARTERS.map(([label, prompt]) => (
          <Button className="h-auto justify-between rounded-2xl border-border/75 bg-card px-4 py-3.5 font-medium shadow-xs hover:border-primary/35 hover:bg-primary/5" key={label} onClick={() => sendPrompt(prompt)} variant="outline">
            <span>{label}</span><ArrowUp className="size-4 rotate-45 text-muted-foreground" />
          </Button>
        ))}
      </div>
    </div>
  );
}

function SourceCards({ citations, label, countLabel }: { citations: AtlasCitation[]; label: string; countLabel: string }) {
  const { addEvidence, basket, openSource } = useAtlas();

  return (
    <div className="mt-5">
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
        <span className="text-xs text-muted-foreground">{citations.length} {countLabel}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {citations.map((citation) => {
          const result = citationToResult(citation);
          const saved = basket.some((item) => item.chunk_id === citation.chunkId);
          return (
            <Card className="gap-0 rounded-2xl border-border/80 py-0 shadow-xs" key={`${citation.id}-${citation.chunkId}`}>
              <CardContent className="p-3.5">
                <button className="block w-full text-left" onClick={() => openSource(result)} type="button">
                  <div className="flex items-center gap-2">
                    <Badge className="rounded-md bg-primary/10 px-1.5 text-[10px] text-primary" variant="secondary">{citation.id}</Badge>
                    <span className="text-[11px] font-medium text-muted-foreground">{citation.pageStart ? `Page ${citation.pageStart}` : "Page unknown"}</span>
                  </div>
                  <div className="mt-2 line-clamp-2 text-sm font-semibold leading-5">{citation.title}</div>
                  <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{citation.excerpt}</p>
                </button>
                <div className="mt-3 flex items-center gap-1 border-t pt-2">
                  <Button className="h-7 px-2 text-xs text-primary" onClick={() => openSource(result)} size="sm" variant="ghost">Preview</Button>
                  <Button className="h-7 gap-1 px-2 text-xs" disabled={saved} onClick={() => addEvidence(result)} size="sm" variant="ghost">
                    {saved ? <Check className="size-3.5" /> : <Plus className="size-3.5" />}{saved ? "On shelf" : "Add to shelf"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function CitationList({ data }: DataMessagePartProps<AtlasCitation[]>) {
  return <SourceCards citations={data as AtlasCitation[]} countLabel="cited" label="Cited sources" />;
}

function EvidenceList({ data }: DataMessagePartProps<AtlasCitation[]>) {
  return <SourceCards citations={data as AtlasCitation[]} countLabel="retrieved" label="Retrieved evidence" />;
}

function RunInfo({ data }: DataMessagePartProps<AtlasRunInfo>) {
  const info = data as AtlasRunInfo;
  const retrieval = info.retrievalMode === "hybrid" ? "Hybrid" : info.retrievalMode === "keyword" ? "Keyword" : info.retrievalMode;
  const retrievalLabel = `${retrieval}${info.semanticApplied ? " + semantic" : ""}`;
  return (
    <details className="mt-3 text-[11px] text-muted-foreground">
      <summary className="cursor-pointer list-none select-none">
        <span className="font-medium text-foreground">{info.foundryUsed ? "Grounded by Microsoft Foundry" : "Foundry not used"}</span>
        <span> · {retrievalLabel} · {info.citationCount} cited · {(info.timings.totalMs / 1000).toFixed(1)}s</span>
      </summary>
      <div className="mt-2 rounded-lg border bg-muted/35 px-3 py-2 leading-5">
        <div>Deployment: <span className="font-mono text-foreground">{info.deployment || "unknown"}</span></div>
        <div>Evidence: {info.evidenceCount} retrieved · {info.citationCount} cited · citation validation passed</div>
        <div>Retrieval: {info.retrievalMode}{info.retrievalFallbackFrom ? ` (fallback from ${info.retrievalFallbackFrom})` : ""} · retries {info.retryCount}</div>
        <div>Timing: Search {info.timings.retrievalMs} ms · Foundry {info.timings.foundryMs} ms · total {info.timings.totalMs} ms</div>
        {info.coverage ? <div>Corpus: {info.coverage.indexName} · {info.coverage.chunkCount.toLocaleString()} chunks · {info.coverage.earliestFilingDate || "unknown"} → {info.coverage.latestFilingDate || "unknown"}</div> : null}
      </div>
    </details>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="group relative grid grid-cols-[2rem_minmax(0,1fr)] gap-3">
      <div className="grid size-8 place-items-center rounded-xl bg-primary text-xs font-bold text-primary-foreground">A</div>
      <div className="min-w-0 pt-0.5">
        <div className="text-[15px] leading-7 text-foreground">
          <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
          <MessagePrimitive.Error>
            <ErrorPrimitive.Root className="rounded-xl border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
              <ErrorPrimitive.Message />
            </ErrorPrimitive.Root>
          </MessagePrimitive.Error>
        </div>
        <ActionBarPrimitive.Root className="mt-2 opacity-0 transition group-hover:opacity-100" hideWhenRunning>
          <ActionBarPrimitive.Copy render={<TooltipIconButton tooltip="Copy answer" variant="ghost" />}>
            <AuiIf condition={(state) => state.message.isCopied}><Check className="size-4" /></AuiIf>
            <AuiIf condition={(state) => !state.message.isCopied}><Copy className="size-4" /></AuiIf>
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="ml-auto max-w-[85%]">
      <div className="rounded-2xl rounded-br-md bg-foreground px-4 py-3 text-sm leading-6 text-background">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function Composer() {
  const { basket, filters, openFilters, scope, setScope } = useAtlas();
  const filterCount = [filters.company, filters.project, filters.documentId, filters.filingNumber].filter(Boolean).length + filters.chunkTypes.length;

  return (
    <ComposerPrimitive.Root className="rounded-[22px] border border-border bg-card p-2 shadow-[0_18px_55px_rgba(20,40,36,0.14)] transition focus-within:border-primary/45 focus-within:ring-4 focus-within:ring-primary/8">
      <ComposerPrimitive.Input aria-label="Ask REGDOCS Atlas" className="max-h-40 min-h-14 w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 outline-none placeholder:text-muted-foreground" placeholder={scope === "evidence" ? "Ask only about sources on your shelf…" : "Ask anything about CER records…"} rows={2} />
      <div className="flex items-center gap-1 px-1 pb-0.5">
        <Button className="gap-1.5 text-xs" onClick={openFilters} size="sm" type="button" variant={filterCount ? "secondary" : "ghost"}>
          <Filter className="size-4" />Filters{filterCount ? <Badge className="h-4 min-w-4 rounded-full px-1 text-[9px]">{filterCount}</Badge> : null}
        </Button>
        <Button className="gap-1.5 text-xs" disabled={!basket.length} onClick={() => setScope(scope === "corpus" ? "evidence" : "corpus")} size="sm" type="button" variant={scope === "evidence" ? "secondary" : "ghost"}>
          <LibraryBig className="size-4" />{scope === "evidence" ? `Shelf only (${basket.length})` : "All records"}
        </Button>
        <div className="ml-auto">
          <AuiIf condition={(state) => !state.thread.isRunning}>
            <ComposerPrimitive.Send render={<Button aria-label="Send question" className="rounded-xl" size="icon-sm" />}><ArrowUp className="size-4" /></ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf condition={(state) => state.thread.isRunning}>
            <ComposerPrimitive.Cancel render={<Button aria-label="Stop generating" className="rounded-xl" size="icon-sm" />}><Square className="size-3 fill-current" /></ComposerPrimitive.Cancel>
          </AuiIf>
        </div>
      </div>
    </ComposerPrimitive.Root>
  );
}

export function AtlasThread() {
  useAssistantDataUI({ name: "regdocs-evidence", render: EvidenceList });
  useAssistantDataUI({ name: "regdocs-citations", render: CitationList });
  useAssistantDataUI({ name: "regdocs-run-info", render: RunInfo });

  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col bg-background">
      <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col overflow-y-auto scroll-smooth" turnAnchor="top">
        <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 pt-6 sm:px-6">
          <AuiIf condition={(state) => state.thread.isEmpty}><Welcome /></AuiIf>
          <div className="flex flex-col gap-9 pb-8">
            <ThreadPrimitive.Messages components={{ AssistantMessage, UserMessage }} />
          </div>
          <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto bg-gradient-to-t from-background via-background to-transparent pb-4 pt-10 sm:pb-6">
            <ThreadPrimitive.ScrollToBottom render={<Button aria-label="Scroll to latest answer" className="absolute -top-1 left-1/2 -translate-x-1/2 rounded-full disabled:invisible" size="icon-sm" variant="outline" />}><ArrowDown className="size-4" /></ThreadPrimitive.ScrollToBottom>
            <Composer />
            <div className="mt-2 text-center text-[10px] text-muted-foreground">Every answer stays attached to its source pages.</div>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
      <AuiIf condition={(state) => state.thread.isRunning}>
        <div aria-live="polite" className="pointer-events-none absolute left-1/2 top-20 flex -translate-x-1/2 items-center gap-2 rounded-full border bg-card/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur" role="status">
          <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin text-primary" />Reading the evidence…
        </div>
      </AuiIf>
    </ThreadPrimitive.Root>
  );
}
