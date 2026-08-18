import type { AtlasSearchResult } from "@/lib/azure-search";

export type AtlasCitation = {
  id: string;
  chunkId: string;
  title: string;
  documentId: string;
  filingNumber: string | null;
  pageStart: number | null;
  pageEnd: number | null;
  sourceUrl: string | null;
  resolvedUrl: string | null;
  fileType: string | null;
  excerpt: string;
};

export type AtlasRunInfo = {
  foundryUsed: boolean;
  deployment: string | null;
  retrievalMode: string;
  retrievalFallbackFrom: string | null;
  evidenceCount: number;
  citationCount: number;
  semanticApplied: boolean;
  retryCount: number;
  timings: {
    retrievalMs: number;
    foundryMs: number;
    totalMs: number;
  };
  coverage?: {
    indexName: string;
    chunkCount: number;
    earliestFilingDate: string | null;
    latestFilingDate: string | null;
  } | null;
};

export type AtlasFilters = {
  company: string;
  project: string;
  documentId: string;
  filingNumber: string;
  chunkTypes: string[];
};

export const EMPTY_FILTERS: AtlasFilters = {
  company: "",
  project: "",
  documentId: "",
  filingNumber: "",
  chunkTypes: [],
};

export function citationToResult(citation: AtlasCitation): AtlasSearchResult {
  return {
    chunk_id: citation.chunkId,
    document_id: citation.documentId,
    chunk_type: "text",
    title: citation.title,
    heading: citation.title,
    content: citation.excerpt,
    filing_number: citation.filingNumber,
    page_start: citation.pageStart,
    page_end: citation.pageEnd,
    source_url: citation.sourceUrl,
    resolved_url: citation.resolvedUrl,
    file_types: citation.fileType ? [citation.fileType] : null,
    score: null,
  };
}

export function resultTitle(item: AtlasSearchResult) {
  return item.heading || item.title || `Document ${item.document_id}`;
}

function csvCell(value: unknown) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

export function exportEvidence(items: AtlasSearchResult[]) {
  const rows = [
    ["title", "document_id", "filing_number", "page_start", "page_end", "content_type", "excerpt", "source_url"],
    ...items.map((item) => [resultTitle(item), item.document_id, item.filing_number, item.page_start, item.page_end, item.chunk_type, item.content, item.source_url]),
  ];
  const blob = new Blob([rows.map((row) => row.map(csvCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "regdocs-atlas-shelf.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}
