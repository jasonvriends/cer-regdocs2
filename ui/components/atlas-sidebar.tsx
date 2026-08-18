"use client";

import { BarChart3, Clock3, Database, GitFork, LibraryBig, PanelLeft, Plus, ShieldCheck } from "lucide-react";
import { ThreadList, ThreadListNew } from "@/components/assistant-ui/thread-list";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function AtlasLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <div aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm">A</div>
      <div className={cn("min-w-0", compact && "sr-only")}>
        <div className="truncate text-sm font-bold tracking-tight">REGDOCS Atlas</div>
        <div className="text-[10px] font-medium text-muted-foreground">Public CER evidence</div>
      </div>
    </div>
  );
}

export function ResearchSidebarTrigger({ className }: { className?: string }) {
  const { isMobile, openMobile, state, toggleSidebar } = useSidebar();
  const expanded = isMobile ? openMobile : state === "expanded";
  const action = expanded ? "Collapse" : "Expand";

  return (
    <Button
      aria-expanded={expanded}
      aria-label={`${action} research navigation`}
      className={cn("size-9 shrink-0", className)}
      onClick={toggleSidebar}
      size="icon"
      title={`${action} research navigation (Ctrl+B)`}
      type="button"
      variant="ghost"
    >
      <PanelLeft aria-hidden="true" className="size-5" />
    </Button>
  );
}

export function AtlasSidebar({
  shelfCount,
  onCoverage,
  onDataset,
  onGraph,
  onShelf,
  onTimeline,
}: {
  shelfCount: number;
  onCoverage: () => void;
  onDataset: () => void;
  onGraph: () => void;
  onShelf: () => void;
  onTimeline: () => void;
}) {
  const { isMobile, setOpenMobile } = useSidebar();

  const runAndClose = (action: () => void) => {
    if (isMobile) setOpenMobile(false);
    action();
  };

  return (
    <Sidebar aria-label="Research history and tools" collapsible="icon" id="atlas-research-navigation">
      <nav aria-label="Research history and tools" className="flex h-full min-h-0 flex-col">
        <SidebarHeader className="h-16 justify-center border-b border-sidebar-border px-3 group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:px-1.5">
          <div className="group-data-[collapsible=icon]:hidden"><AtlasLogo /></div>
          <div className="hidden group-data-[collapsible=icon]:block"><AtlasLogo compact /></div>
        </SidebarHeader>

        <SidebarContent className="p-3 group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:px-2">
          <div
            className="group-data-[collapsible=icon]:hidden"
            onClick={(event) => {
              const target = event.target as HTMLElement;
              if (isMobile && target.closest('[data-slot="aui_thread-list-new"], [data-slot="aui_thread-list-item-trigger"]')) setOpenMobile(false);
            }}
          >
            <ThreadList />
          </div>
          <ThreadListNew
            aria-label="Start new research"
            className="hidden size-8 justify-center p-0 group-data-[collapsible=icon]:flex"
          >
            <Plus aria-hidden="true" className="size-4" />
          </ThreadListNew>
        </SidebarContent>

        <SidebarFooter className="border-t border-sidebar-border p-2">
          <div className="mb-2 px-2 pb-2 group-data-[collapsible=icon]:hidden">
            <div className="mb-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Public records sourced from</div>
            <a
              aria-label="Visit the Canada Energy Regulator website"
              className="block rounded-sm"
              href="https://www.cer-rec.gc.ca/en/"
              rel="noreferrer"
              target="_blank"
            >
              <img
                alt="Canada Energy Regulator / Régie de l’énergie du Canada"
                className="h-auto w-full max-w-[14rem]"
                height="21"
                src="https://www.cer-rec.gc.ca/global/images/logo_cer_en.png"
                width="222"
              />
            </a>
          </div>
          <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground group-data-[collapsible=icon]:sr-only">Research tools</div>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton aria-label={`Shelf, ${shelfCount} saved ${shelfCount === 1 ? "source" : "sources"}`} onClick={() => runAndClose(onShelf)} tooltip="Shelf">
                <LibraryBig aria-hidden="true" className="text-primary" />
                <span className="flex-1">Shelf</span>
                <span aria-hidden="true" className="rounded-full bg-sidebar-accent px-1.5 text-[10px] tabular-nums">{shelfCount}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={() => runAndClose(onDataset)} tooltip="Make a dataset">
                <Database aria-hidden="true" className="text-primary" />
                <span>Make a dataset</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={() => runAndClose(onTimeline)} tooltip="Regulatory timeline">
                <Clock3 aria-hidden="true" className="text-primary" />
                <span>Timeline</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={() => runAndClose(onGraph)} tooltip="Regulatory graph">
                <GitFork aria-hidden="true" className="text-primary" />
                <span>Relationship graph</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton className="h-auto min-h-9 items-start py-2 group-data-[collapsible=icon]:items-center" onClick={() => runAndClose(onCoverage)} tooltip="Coverage">
                <BarChart3 aria-hidden="true" className="mt-0.5 text-primary group-data-[collapsible=icon]:mt-0" />
                <span>
                  <span className="block">Coverage</span>
                  <span className="mt-0.5 flex items-center gap-1 text-[10px] font-normal text-muted-foreground">
                    <ShieldCheck aria-hidden="true" className="size-3 text-emerald-600" />Live index status
                  </span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </nav>
      <SidebarRail aria-label="Toggle research navigation" title="Toggle research navigation" />
    </Sidebar>
  );
}
