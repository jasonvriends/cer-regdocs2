"use client";

import { createContext, useContext } from "react";
import type { AtlasSearchResult } from "@/lib/azure-search";
import type { AtlasFilters } from "@/lib/atlas-ui";

export type AtlasScope = "corpus" | "evidence";

export type AtlasContextValue = {
  basket: AtlasSearchResult[];
  filters: AtlasFilters;
  scope: AtlasScope;
  addEvidence: (item: AtlasSearchResult) => void;
  removeEvidence: (chunkId: string) => void;
  openFilters: () => void;
  openSource: (item: AtlasSearchResult) => void;
  setScope: (scope: AtlasScope) => void;
};

export const AtlasContext = createContext<AtlasContextValue | null>(null);

export function useAtlas() {
  const value = useContext(AtlasContext);
  if (!value) throw new Error("useAtlas must be used within AtlasContext.Provider");
  return value;
}
