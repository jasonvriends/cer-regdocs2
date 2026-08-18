"use client";

import { useMemo, useState } from "react";
import { Search, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type ErrorRecord = {
  timeGenerated: string;
  operation: string | null;
  errorId: string;
  message: string | null;
  errorName: string | null;
  containerApp: string | null;
  revision: string | null;
  container: string | null;
};

type LookupPayload = {
  errorId?: string;
  count?: number;
  errors?: ErrorRecord[];
  error?: string;
};

export default function ErrorLookupPage() {
  const initialId = useMemo(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("errorId")?.toUpperCase() || "";
  }, []);
  const [errorId, setErrorId] = useState(initialId);
  const [token, setToken] = useState("");
  const [payload, setPayload] = useState<LookupPayload | null>(null);
  const [loading, setLoading] = useState(false);

  async function lookup() {
    setLoading(true);
    setPayload(null);
    try {
      const response = await fetch(`/api/diagnostics/errors?errorId=${encodeURIComponent(errorId.trim())}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      const result = await response.json() as LookupPayload;
      setPayload(result);
    } catch (error) {
      setPayload({ error: error instanceof Error ? error.message : "Error lookup failed." });
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-dvh bg-muted/25 px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-4xl">
        <div className="flex items-start gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-primary/10 text-primary"><ShieldCheck className="size-5" /></div>
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">REGDOCS Atlas</div>
            <h1 className="mt-1 text-3xl font-semibold tracking-tight">Error lookup</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Enter the reference shown to the user and the operator token. Atlas queries the Container Apps Log Analytics workspace for the matching server error.
            </p>
          </div>
        </div>

        <Card className="mt-7">
          <CardHeader><CardTitle className="text-base">Find an error</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
            <Input aria-label="Error reference" onChange={(event) => setErrorId(event.target.value.toUpperCase())} placeholder="ATLAS-0123ABCD4567EF89" value={errorId} />
            <Input aria-label="Operator token" onChange={(event) => setToken(event.target.value)} placeholder="Operator token" type="password" value={token} />
            <Button disabled={loading || !errorId.trim() || !token} onClick={() => void lookup()}><Search className="size-4" />{loading ? "Looking up…" : "Lookup"}</Button>
          </CardContent>
        </Card>

        {payload?.error ? <div className="mt-5 rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">{payload.error}</div> : null}

        {payload?.errors ? (
          <div className="mt-5 space-y-3">
            <div className="text-sm text-muted-foreground">{payload.errors.length ? `${payload.errors.length} matching log record(s)` : "No matching Log Analytics record yet. Ingestion can take a few minutes."}</div>
            {payload.errors.map((item, index) => (
              <Card key={`${item.timeGenerated}-${index}`}>
                <CardHeader className="pb-2"><CardTitle className="text-sm font-mono">{item.errorId}</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div><span className="font-medium">Operation:</span> {item.operation || "unknown"}</div>
                  <div><span className="font-medium">Error:</span> {item.errorName || "Error"}: {item.message || "No message captured"}</div>
                  <div className="text-xs text-muted-foreground">{item.timeGenerated} · {item.containerApp || "app"} · {item.revision || "revision unknown"} · {item.container || "container unknown"}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : null}
      </div>
    </main>
  );
}
