"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CalendarClock, CheckCircle2, FileCheck2, LoaderCircle, Quote } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AtlasClaim, AtlasObligation, IntelligenceScope } from "@/lib/intelligence";

type Mode = "claims" | "obligations";
type ClaimFilter = "all" | "regulator" | "party";
type ObligationFilter = "all" | "conditions" | "commitments" | "deadlines" | "outstanding";

type Props = {
  mode: Mode;
  scope: IntelligenceScope;
  onOpenEvidence: (chunkId: string) => void;
};

function query(scope: IntelligenceScope) {
  const params = new URLSearchParams();
  if (scope.documentId) params.set("documentId", scope.documentId);
  if (scope.filingId) params.set("filingId", scope.filingId);
  if (scope.filingNumber) params.set("filingNumber", scope.filingNumber);
  if (scope.company) params.set("company", scope.company);
  if (scope.project) params.set("project", scope.project);
  params.set("top", "250");
  return params;
}

function explicitRegulatorClaim(claim: AtlasClaim) {
  const text = `${claim.claim_type} ${claim.claimant ?? ""}`.toLowerCase();
  return /\b(commission|board|cer|regulator|regulatory|finding|decision|determination|conclusion)\b/.test(text);
}

function explicitOutstanding(obligation: AtlasObligation) {
  const status = (obligation.status ?? "").toLowerCase();
  return /\b(outstanding|open|pending|incomplete|not complete|not completed|due|overdue)\b/.test(status);
}

function obligationKind(obligation: AtlasObligation, word: "condition" | "commitment") {
  return obligation.obligation_type.toLowerCase().includes(word);
}

function EvidenceButton({ ids, onOpen }: { ids?: string[]; onOpen: (id: string) => void }) {
  const first = ids?.[0];
  if (!first) return null;
  return <Button onClick={() => onOpen(first)} size="sm" variant="outline">Open evidence</Button>;
}

function Meta({ origin, review, confidence }: { origin: string; review: string; confidence: number }) {
  return (
    <div className="flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
      <Badge variant="outline">{origin === "foundry_model" ? "Foundry extracted" : origin}</Badge>
      <Badge variant="outline">{review}</Badge>
      <Badge variant="outline">{Math.round(confidence * 100)}% confidence</Badge>
    </div>
  );
}

export function AtlasRegulatoryRecords({ mode, scope, onOpenEvidence }: Props) {
  const [claims, setClaims] = useState<AtlasClaim[]>([]);
  const [obligations, setObligations] = useState<AtlasObligation[]>([]);
  const [claimFilter, setClaimFilter] = useState<ClaimFilter>("all");
  const [obligationFilter, setObligationFilter] = useState<ObligationFilter>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scopeKey = JSON.stringify(scope);
  const hasScope = Object.values(scope).some(Boolean);

  useEffect(() => {
    if (!hasScope) {
      setClaims([]);
      setObligations([]);
      return;
    }
    const controller = new AbortController();
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`/api/${mode}?${query(scope)}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as {
          claims?: AtlasClaim[];
          obligations?: AtlasObligation[];
          error?: string;
        };
        if (!response.ok) throw new Error(payload.error || `${mode} retrieval failed`);
        if (mode === "claims") setClaims(payload.claims ?? []);
        else setObligations(payload.obligations ?? []);
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : `${mode} retrieval failed`);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [hasScope, mode, scopeKey]);

  const visibleClaims = useMemo(() => claims.filter((claim) => {
    if (claimFilter === "regulator") return explicitRegulatorClaim(claim);
    if (claimFilter === "party") return !explicitRegulatorClaim(claim);
    return true;
  }), [claimFilter, claims]);

  const visibleObligations = useMemo(() => obligations.filter((obligation) => {
    if (obligationFilter === "conditions") return obligationKind(obligation, "condition");
    if (obligationFilter === "commitments") return obligationKind(obligation, "commitment");
    if (obligationFilter === "deadlines") return Boolean(obligation.deadline);
    if (obligationFilter === "outstanding") return explicitOutstanding(obligation);
    return true;
  }), [obligationFilter, obligations]);

  if (!hasScope) {
    return <div className="rounded-xl border bg-muted/30 p-5 text-sm text-muted-foreground">Set a document, filing, company, or project scope to inspect regulatory intelligence.</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {mode === "claims" ? (
          <>
            {(["all", "regulator", "party"] as ClaimFilter[]).map((filter) => (
              <Button key={filter} onClick={() => setClaimFilter(filter)} size="sm" variant={claimFilter === filter ? "secondary" : "outline"}>
                {filter === "all" ? "All findings & claims" : filter === "regulator" ? "Regulator findings" : "Party claims"}
              </Button>
            ))}
          </>
        ) : (
          <>
            {(["all", "conditions", "commitments", "deadlines", "outstanding"] as ObligationFilter[]).map((filter) => (
              <Button key={filter} onClick={() => setObligationFilter(filter)} size="sm" variant={obligationFilter === filter ? "secondary" : "outline"}>
                {filter === "all" ? "All obligations" : filter[0].toUpperCase() + filter.slice(1)}
              </Button>
            ))}
          </>
        )}
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{mode === "claims" ? visibleClaims.length : visibleObligations.length} records</span>
        <span>Model-extracted records remain marked unreviewed until reviewed.</span>
      </div>

      {loading ? <div className="flex items-center gap-2 rounded-xl border p-4 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Loading regulatory intelligence…</div> : null}
      {error ? <div className="rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive"><AlertTriangle className="mr-2 inline size-4" />{error}</div> : null}

      {!loading && !error && mode === "claims" && !visibleClaims.length ? <div className="rounded-xl border bg-muted/25 p-5 text-sm text-muted-foreground">No extracted claims or findings match this scope/filter.</div> : null}
      {!loading && !error && mode === "obligations" && !visibleObligations.length ? <div className="rounded-xl border bg-muted/25 p-5 text-sm text-muted-foreground">No extracted obligations match this scope/filter.</div> : null}

      {mode === "claims" ? visibleClaims.map((claim) => (
        <article className="rounded-xl border bg-card p-4 shadow-xs" key={claim.id}>
          <div className="flex items-start gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/8 text-primary"><Quote className="size-4" /></div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{claim.claim_type}</Badge>
                {claim.claimant ? <span className="text-xs font-medium">{claim.claimant}</span> : null}
              </div>
              <p className="mt-2 text-sm leading-6">{claim.statement}</p>
              {claim.subject ? <p className="mt-1 text-xs text-muted-foreground">Subject: {claim.subject}</p> : null}
              <div className="mt-3"><Meta confidence={claim.confidence} origin={claim.origin} review={claim.review_status} /></div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {claim.document_id ? <span>Document {claim.document_id}</span> : null}
                {claim.filing_number ? <span>· Filing {claim.filing_number}</span> : null}
                {claim.evidence_page_start ? <span>· Page {claim.evidence_page_start}</span> : null}
                <EvidenceButton ids={claim.evidence_chunk_ids} onOpen={onOpenEvidence} />
              </div>
            </div>
          </div>
        </article>
      )) : visibleObligations.map((obligation) => (
        <article className="rounded-xl border bg-card p-4 shadow-xs" key={obligation.id}>
          <div className="flex items-start gap-3">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/8 text-primary">
              {explicitOutstanding(obligation) ? <CalendarClock className="size-4" /> : obligation.status?.toLowerCase().includes("complete") ? <CheckCircle2 className="size-4" /> : <FileCheck2 className="size-4" />}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{obligation.obligation_type}</Badge>
                {obligation.status ? <Badge variant="outline">{obligation.status}</Badge> : null}
              </div>
              <p className="mt-2 text-sm leading-6">{obligation.action}</p>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                {obligation.obligated_party ? <span>Party: {obligation.obligated_party}</span> : null}
                {obligation.deadline ? <span>Deadline: {obligation.deadline}</span> : null}
              </div>
              <div className="mt-3"><Meta confidence={obligation.confidence} origin={obligation.origin} review={obligation.review_status} /></div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {obligation.document_id ? <span>Document {obligation.document_id}</span> : null}
                {obligation.filing_number ? <span>· Filing {obligation.filing_number}</span> : null}
                {obligation.evidence_page_start ? <span>· Page {obligation.evidence_page_start}</span> : null}
                <EvidenceButton ids={obligation.evidence_chunk_ids} onOpen={onOpenEvidence} />
              </div>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
