"use client";

import { X } from "lucide-react";
import { DocumentReader } from "@/components/document-reader";
import { Button } from "@/components/ui/button";
import type { AtlasSearchResult } from "@/lib/azure-search";

export function AtlasSourceViewer({
  item,
  onAddEvidence,
  onClose,
}: {
  item: AtlasSearchResult;
  onAddEvidence: (item: AtlasSearchResult) => void;
  onClose: () => void;
}) {
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <Button
        aria-label="Close source"
        className="absolute right-3 top-2 z-20 rounded-full bg-background/90 shadow-sm backdrop-blur"
        onClick={onClose}
        size="icon-sm"
        variant="outline"
      >
        <X className="size-4" />
      </Button>
      <DocumentReader
        onAddToWorkspace={onAddEvidence}
        query={item.content ?? ""}
        selected={item}
      />
    </div>
  );
}
