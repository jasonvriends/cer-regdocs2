import "server-only";

import { randomBytes, timingSafeEqual } from "node:crypto";
import { DefaultAzureCredential } from "@azure/identity";

const ERROR_PREFIX = "ATLAS";
const ERROR_ID_RE = /^ATLAS-[A-F0-9]{16}$/;

export type AtlasErrorLog = {
  timeGenerated: string;
  operation: string | null;
  errorId: string;
  message: string | null;
  errorName: string | null;
  containerApp: string | null;
  revision: string | null;
  container: string | null;
};

function serializeError(error: unknown) {
  if (error instanceof Error) {
    return {
      errorName: error.name,
      message: error.message,
      stack: error.stack,
    };
  }
  return {
    errorName: "UnknownError",
    message: String(error),
    stack: undefined,
  };
}

export function createErrorId() {
  return `${ERROR_PREFIX}-${randomBytes(8).toString("hex").toUpperCase()}`;
}

export function reportServerError(
  operation: string,
  error: unknown,
  context: Record<string, unknown> = {},
) {
  const errorId = createErrorId();
  const serialized = serializeError(error);
  console.error("REGDOCS error", JSON.stringify({
    event: "atlas.error",
    errorId,
    operation,
    ...serialized,
    context,
  }));
  return errorId;
}

export function publicErrorResponse(
  operation: string,
  error: unknown,
  userMessage: string,
  status = 502,
  context: Record<string, unknown> = {},
) {
  const errorId = reportServerError(operation, error, context);
  return Response.json(
    {
      error: `${userMessage} Reference: ${errorId}`,
      errorId,
    },
    { status },
  );
}

export function withErrorReference(message: string, errorId: string) {
  return `${message} Reference: ${errorId}`;
}

export function validErrorId(value: string) {
  return ERROR_ID_RE.test(value);
}

export function diagnosticsTokenConfigured() {
  return Boolean(process.env.REGDOCS_DIAGNOSTICS_TOKEN?.trim());
}

export function authorizedDiagnosticsToken(candidate: string | null) {
  const expected = process.env.REGDOCS_DIAGNOSTICS_TOKEN?.trim();
  if (!expected || !candidate) return false;
  const left = Buffer.from(candidate);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function parseStructuredError(log: string) {
  const marker = "REGDOCS error ";
  const offset = log.indexOf(marker);
  if (offset < 0) return null;
  try {
    const parsed = JSON.parse(log.slice(offset + marker.length)) as Record<string, unknown>;
    return parsed.event === "atlas.error" ? parsed : null;
  } catch {
    return null;
  }
}

export async function queryErrorFromLogAnalytics(errorId: string): Promise<AtlasErrorLog[]> {
  if (!validErrorId(errorId)) throw new Error("Invalid Atlas error reference");
  const workspaceId = process.env.LOG_ANALYTICS_WORKSPACE_ID?.trim();
  if (!workspaceId) throw new Error("LOG_ANALYTICS_WORKSPACE_ID is not configured");

  const credential = new DefaultAzureCredential();
  const token = await credential.getToken("https://api.loganalytics.io/.default");
  const escaped = errorId.replace(/'/g, "''");
  const query = `ContainerAppConsoleLogs_CL\n| where TimeGenerated > ago(30d)\n| where Log_s contains '${escaped}'\n| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s\n| order by TimeGenerated desc\n| take 20`;
  const response = await fetch(`https://api.loganalytics.azure.com/v1/workspaces/${encodeURIComponent(workspaceId)}/query`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query }),
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 800);
    throw new Error(`Log Analytics query failed (${response.status}): ${detail}`);
  }

  const payload = await response.json() as {
    tables?: Array<{ columns?: Array<{ name?: string }>; rows?: unknown[][] }>;
  };
  const table = payload.tables?.[0];
  const columns = table?.columns?.map((column) => column.name || "") ?? [];
  const index = (name: string) => columns.indexOf(name);
  return (table?.rows ?? []).map((row) => {
    const log = String(row[index("Log_s")] ?? "");
    const structured = parseStructuredError(log);
    return {
      timeGenerated: String(row[index("TimeGenerated")] ?? ""),
      operation: typeof structured?.operation === "string" ? structured.operation : null,
      errorId,
      message: typeof structured?.message === "string" ? structured.message : null,
      errorName: typeof structured?.errorName === "string" ? structured.errorName : null,
      containerApp: String(row[index("ContainerAppName_s")] ?? "") || null,
      revision: String(row[index("RevisionName_s")] ?? "") || null,
      container: String(row[index("ContainerName_s")] ?? "") || null,
    };
  });
}
