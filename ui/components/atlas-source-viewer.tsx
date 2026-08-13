"use client";

import dynamic from "next/dynamic";
import { ExternalLink, FileText, X } from "lucide-react";
import { DocumentReader } from "@/components/document-reader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AtlasSearchResult } from "@/lib/azure-search";

const PDFViewer = dynamic(
  () => import("@/components/extend/pdf-viewer").then((module) => module.PDFViewer),
  { ssr: false, loading: () => <div className="grid h-full place-items-center text-sm text-muted-foreground">Opening document surface…</div> },
);

function pdfUrl(item: AtlasSearchResult) {
  const candidates = [item.resolved_url, item.source_url].filter((value): value is string => Boolean(value));
  return candidates.find((value) => /\.pdf(?:$|[?#])/i.test(value)) ?? null;
}

export function AtlasSourceViewer({
  item,
  onAddEvidence,
  onClose,
}: {
  item: AtlasSearchResult;
  onAddEvidence: (item: AtlasSearchResult) => void;
  onClose: () => void;
}) {
  const source = pdfUrl(item);
  const page = item.page_start ?? null;

  if (!source) return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <Button aria-label="Close source" className="absolute right-3 top-2 z-20 rounded-full bg-background/90 shadow-sm backdrop-blur" onClick={onClose} size="icon-sm" variant="outline"><X className="size-4" /></Button>
      <DocumentReader onAddToWorkspace={onAddEvidence} query={item.content ?? ""} selected={item} />
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-muted/35">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-background px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="size-4 shrink-0 text-primary" />
          <span className="truncate text-sm font-medium">{item.title || `Document ${item.document_id}`}</span>
          {page ? <Badge className="shrink-0" variant="secondary">Cited page {page}</Badge> : null}
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => onAddEvidence(item)} size="sm" variant="outline">Add to shelf</Button>
          {item.source_url ? <Button render={<a href={item.source_url} rel="noreferrer" target="_blank" />} size="sm" variant="ghost"><ExternalLink className="size-4" />Original</Button> : null}
          <Button aria-label="Close source" onClick={onClose} size="icon-sm" variant="ghost"><X className="size-4" /></Button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <PDFViewer
          className="h-full"
          fileName={item.title ?? `Document ${item.document_id}`}
          pageClassName={(pageNumber) => pageNumber === page ? "ring-4 ring-primary/35 ring-offset-4" : undefined}
          showUpload={false}
          src={source}
        />
      </div>
    </div>
  );
}
