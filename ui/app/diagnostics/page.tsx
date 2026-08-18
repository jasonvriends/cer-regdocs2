"use client";

import { useEffect, useState } from "react";
import { Activity, CheckCircle2, LoaderCircle, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Check = {
  ok: boolean;
  ms?: number;
  skipped?: boolean;
  detail?: string;
  count?: number | null;
  error?: string;
};

type Diagnostics = {
  status: "ok" | "degraded" | "configuration_required";
  deep: boolean;
  cached?: boolean;
  generatedAt: string;
  configuration: {
    search: boolean;
    hybrid: boolean;
    semantic: boolean;
    foundry: boolean;
    foundryModel: string | null;
    operatorDiagnostics: boolean;
  };
  checks?: Record<string, Check>;
};

function StatusIcon({ check }: { check: Check }) {
  if (check.skipped) return <Activity className="size-4 text-muted-foreground" />;
  return check.ok
    ? <CheckCircle2 className="size-4 text-emerald-600" />
    : <XCircle className="size-4 text-destructive" />;
}

function title(key: string) {
  return key.replace(/([A-Z])/g, " $1").replace(/^./, (value) => value.toUpperCase());
}

export default function DiagnosticsPage() {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(deep = false) {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/diagnostics${deep ? "?deep=1" : ""}`, {
        cache: "no-store",
        ...(deep ? { headers: { Authorization: `Bearer ${token}` } } : {}),
      });
      const payload = await response.json() as Diagnostics & { error?: string };
      if (!response.ok && !payload.checks) throw new Error(payload.error || `Diagnostics returned ${response.status}`);
      setData(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Diagnostics failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(false); }, []);

  return (
    <main className="min-h-dvh bg-muted/25 px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">REGDOCS Atlas</div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">System diagnostics</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Shallow checks confirm configuration. Operator-authorized live checks make real requests to Azure AI Search, Microsoft Foundry, the document reader, and the intelligence indexes.
            </p>
          </div>
        </div>

        <Card className="mt-6 gap-3 py-5">
          <CardHeader className="px-5 py-0">
            <CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4" />Operator live checks</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 px-5 py-0 sm:flex-row">
            <Input
              aria-label="Diagnostics operator token"
              className="sm:max-w-md"
              onChange={(event) => setToken(event.target.value)}
              placeholder="Operator token"
              type="password"
              value={token}
            />
            <Button disabled={loading || !token || data?.configuration.operatorDiagnostics === false} onClick={() => void load(true)}>
              {loading ? <LoaderCircle className="size-4 animate-spin" /> : <Activity className="size-4" />}
              Run live checks
            </Button>
          </CardContent>
        </Card>

        {error ? <div className="mt-6 rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">{error}</div> : null}

        {data ? (
          <>
            <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["Azure AI Search", data.configuration.search],
                ["Hybrid vectors", data.configuration.hybrid],
                ["Semantic ranking", data.configuration.semantic],
                ["Microsoft Foundry", data.configuration.foundry],
                ["Operator diagnostics", data.configuration.operatorDiagnostics],
              ].map(([label, configured]) => (
                <Card className="gap-2 py-4" key={String(label)}>
                  <CardHeader className="px-4 py-0"><CardTitle className="text-sm">{label}</CardTitle></CardHeader>
                  <CardContent className="flex items-center gap-2 px-4 py-0 text-sm text-muted-foreground">
                    {configured ? <CheckCircle2 className="size-4 text-emerald-600" /> : <XCircle className="size-4 text-destructive" />}
                    {configured ? "Configured" : "Not configured"}
                  </CardContent>
                </Card>
              ))}
            </div>

            <Card className="mt-5 gap-3 py-5">
              <CardHeader className="px-5 py-0">
                <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
                  <span>Overall status: {data.status}</span>
                  <span className="text-xs font-normal text-muted-foreground">{data.deep ? `Live · ${data.cached ? "cached" : "fresh"}` : "Configuration only"}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-5 py-0 text-sm text-muted-foreground">
                Foundry deployment: <span className="font-mono text-foreground">{data.configuration.foundryModel || "not configured"}</span>
              </CardContent>
            </Card>

            {data.checks ? (
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                {Object.entries(data.checks).map(([key, check]) => (
                  <Card className="gap-2 py-4" key={key}>
                    <CardHeader className="px-4 py-0">
                      <CardTitle className="flex items-center gap-2 text-sm"><StatusIcon check={check} />{title(key)}</CardTitle>
                    </CardHeader>
                    <CardContent className="px-4 py-0 text-xs leading-5 text-muted-foreground">
                      <div>{check.skipped ? "Skipped" : check.ok ? "Working" : "Failed"}{typeof check.ms === "number" ? ` · ${check.ms} ms` : ""}{typeof check.count === "number" ? ` · ${check.count.toLocaleString()} records/results` : ""}</div>
                      {check.detail ? <div className="mt-1 break-all font-mono text-[11px]">{check.detail}</div> : null}
                      {check.error ? <div className="mt-1 text-destructive">{check.error}</div> : null}
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : null}

            <div className="mt-5 text-xs text-muted-foreground">Generated {new Date(data.generatedAt).toLocaleString()}</div>
          </>
        ) : null}
      </div>
    </main>
  );
}
