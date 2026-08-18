import "server-only";

import { createReadStream, existsSync } from "node:fs";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { DefaultAzureCredential } from "@azure/identity";
import { AzureKeyCredential, SearchClient } from "@azure/search-documents";

export type AtlasEntity = {
  id: string;
  entity_type: "organization" | "project" | "filing" | "document" | string;
  name: string;
  aliases?: string[];
  source_url?: string | null;
  origin: string;
  schema_version: string;
};

export type AtlasRelation = {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type: string;
  document_id?: string | null;
  filing_id?: string | null;
  filing_number?: string | null;
  company?: string | null;
  project?: string | null;
  evidence_chunk_ids?: string[];
  evidence_page_start?: number | null;
  evidence_page_end?: number | null;
  source_url?: string | null;
  confidence: number;
  origin: string;
  review_status: string;
};

export type AtlasEvent = {
  id: string;
  event_type: string;
  title: string;
  summary: string;
  date_start: string;
  date_end?: string | null;
  date_precision: string;
  date_basis: string;
  entity_ids?: string[];
  document_id?: string | null;
  filing_id?: string | null;
  filing_number?: string | null;
  company?: string | null;
  project?: string | null;
  chunk_id?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  source_url?: string | null;
  confidence: number;
  origin: string;
  review_status: string;
};

export type AtlasClaim = {
  id: string;
  claim_type: string;
  statement: string;
  claimant?: string | null;
  subject?: string | null;
  evidence_chunk_ids?: string[];
  evidence_page_start?: number | null;
  evidence_page_end?: number | null;
  document_id?: string | null;
  filing_id?: string | null;
  filing_number?: string | null;
  company?: string | null;
  project?: string | null;
  source_url?: string | null;
  confidence: number;
  origin: string;
  review_status: string;
  extractor_version?: string | null;
};

export type AtlasObligation = {
  id: string;
  obligation_type: string;
  obligated_party?: string | null;
  action: string;
  deadline?: string | null;
  status?: string | null;
  evidence_chunk_ids?: string[];
  evidence_page_start?: number | null;
  evidence_page_end?: number | null;
  document_id?: string | null;
  filing_id?: string | null;
  filing_number?: string | null;
  company?: string | null;
  project?: string | null;
  source_url?: string | null;
  confidence: number;
  origin: string;
  review_status: string;
  extractor_version?: string | null;
};

export type IntelligenceScope = {
  documentId?: string;
  filingId?: string;
  filingNumber?: string;
  company?: string;
  project?: string;
};

export type AtlasGraph = {
  nodes: AtlasEntity[];
  edges: AtlasRelation[];
  truncated: boolean;
};

const clientCache = new Map<string, SearchClient<Record<string, unknown>>>();
const localCache = new Map<string, Promise<Array<Record<string, unknown>>>>();

function config() {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  const apiKey = process.env.AZURE_SEARCH_API_KEY?.trim();
  return { endpoint, apiKey };
}

function getClient(indexName: string) {
  const { endpoint, apiKey } = config();
  if (!endpoint) return null;
  const cacheKey = `${endpoint}|${indexName}|${apiKey ?? "managed-identity"}`;
  let client = clientCache.get(cacheKey);
  if (!client) {
    client = new SearchClient<Record<string, unknown>>(
      endpoint,
      indexName,
      apiKey ? new AzureKeyCredential(apiKey) : new DefaultAzureCredential(),
    );
    clientCache.set(cacheKey, client);
  }
  return client;
}

function localDirectory() {
  const configured = process.env.REGDOCS_INTELLIGENCE_DIR?.trim();
  return configured ? resolve(configured) : resolve(process.cwd(), "..", "workspace", "6_enrich");
}

async function readLocal(name: string) {
  const path = resolve(localDirectory(), `${name}.jsonl`);
  if (!existsSync(path)) return [];
  let pending = localCache.get(path);
  if (!pending) {
    pending = (async () => {
      const rows: Array<Record<string, unknown>> = [];
      const lines = createInterface({ input: createReadStream(path, "utf8"), crlfDelay: Infinity });
      for await (const line of lines) {
        if (line.trim()) rows.push(JSON.parse(line) as Record<string, unknown>);
      }
      return rows;
    })();
    localCache.set(path, pending);
  }
  return pending;
}

function escapeOData(value: string) {
  return value.replace(/'/g, "''");
}

function scopeFilter(scope: IntelligenceScope) {
  const clauses: string[] = [];
  const fields: Array<[keyof IntelligenceScope, string]> = [
    ["documentId", "document_id"],
    ["filingId", "filing_id"],
    ["filingNumber", "filing_number"],
    ["company", "company"],
    ["project", "project"],
  ];
  for (const [property, field] of fields) {
    const value = scope[property]?.trim();
    if (value) clauses.push(`${field} eq '${escapeOData(value)}'`);
  }
  return clauses.join(" and ");
}

function matchesScope(row: Record<string, unknown>, scope: IntelligenceScope) {
  return (
    (!scope.documentId || String(row.document_id ?? "") === scope.documentId) &&
    (!scope.filingId || String(row.filing_id ?? "") === scope.filingId) &&
    (!scope.filingNumber || String(row.filing_number ?? "") === scope.filingNumber) &&
    (!scope.company || String(row.company ?? "") === scope.company) &&
    (!scope.project || String(row.project ?? "") === scope.project)
  );
}

type IntelligenceKind = "entities" | "relations" | "events" | "claims" | "obligations";

async function searchIndex<T extends Record<string, unknown>>(
  kind: IntelligenceKind,
  options: { filter?: string; top: number; orderBy?: string[] },
) {
  const envName = `AZURE_SEARCH_${kind.toUpperCase()}_INDEX`;
  const defaults: Record<IntelligenceKind, string> = {
    entities: "regdocs-entities",
    relations: "regdocs-relations",
    events: "regdocs-events",
    claims: "regdocs-claims",
    obligations: "regdocs-obligations",
  };
  const indexName = process.env[envName]?.trim() || defaults[kind];
  const client = getClient(indexName);
  if (!client) return null;
  const response = await client.search("*", {
    filter: options.filter,
    top: options.top,
    orderBy: options.orderBy,
  });
  const records: T[] = [];
  for await (const result of response.results) records.push(result.document as T);
  return records;
}

export function hasIntelligenceScope(scope: IntelligenceScope) {
  return Object.values(scope).some((value) => Boolean(value?.trim()));
}

export async function getTimeline(scope: IntelligenceScope, top = 200): Promise<AtlasEvent[]> {
  const limit = Math.min(Math.max(top, 1), 500);
  const filter = scopeFilter(scope);
  const remote = await searchIndex<AtlasEvent & Record<string, unknown>>("events", {
    filter: filter || undefined,
    top: limit,
    orderBy: ["date_start asc"],
  });
  if (remote) return remote;
  const local = await readLocal("events");
  return local
    .filter((row) => matchesScope(row, scope))
    .sort((left, right) => String(left.date_start).localeCompare(String(right.date_start)))
    .slice(0, limit) as Array<AtlasEvent & Record<string, unknown>>;
}

export async function getClaims(scope: IntelligenceScope, top = 200): Promise<AtlasClaim[]> {
  const limit = Math.min(Math.max(top, 1), 500);
  const filter = scopeFilter(scope);
  const remote = await searchIndex<AtlasClaim & Record<string, unknown>>("claims", {
    filter: filter || undefined,
    top: limit,
  });
  if (remote) return remote;
  const local = await readLocal("claims");
  return local.filter((row) => matchesScope(row, scope)).slice(0, limit) as Array<AtlasClaim & Record<string, unknown>>;
}

export async function getObligations(scope: IntelligenceScope, top = 200): Promise<AtlasObligation[]> {
  const limit = Math.min(Math.max(top, 1), 500);
  const filter = scopeFilter(scope);
  const remote = await searchIndex<AtlasObligation & Record<string, unknown>>("obligations", {
    filter: filter || undefined,
    top: limit,
    orderBy: ["deadline asc"],
  });
  if (remote) return remote;
  const local = await readLocal("obligations");
  return local
    .filter((row) => matchesScope(row, scope))
    .sort((left, right) => String(left.deadline ?? "9999").localeCompare(String(right.deadline ?? "9999")))
    .slice(0, limit) as Array<AtlasObligation & Record<string, unknown>>;
}

export async function getGraph(scope: IntelligenceScope, top = 400): Promise<AtlasGraph> {
  const limit = Math.min(Math.max(top, 1), 500);
  const filter = scopeFilter(scope);
  if (!filter) return { nodes: [], edges: [], truncated: false };

  let edges = await searchIndex<AtlasRelation & Record<string, unknown>>("relations", {
    filter,
    top: limit + 1,
  });
  if (!edges) {
    const local = await readLocal("relations");
    edges = local.filter((row) => matchesScope(row, scope)).slice(0, limit + 1) as Array<
      AtlasRelation & Record<string, unknown>
    >;
  }
  const truncated = edges.length > limit;
  edges = edges.slice(0, limit);
  const entityIds = [...new Set(edges.flatMap((edge) => [edge.source_id, edge.target_id]))];

  const entityIndex = process.env.AZURE_SEARCH_ENTITIES_INDEX?.trim() || "regdocs-entities";
  const entityClient = getClient(entityIndex);
  let nodes: AtlasEntity[] = [];
  if (entityClient) {
    for (let start = 0; start < entityIds.length; start += 75) {
      const batch = entityIds.slice(start, start + 75);
      const entityFilter = batch.map((id) => `id eq '${escapeOData(id)}'`).join(" or ");
      const response = await entityClient.search("*", { filter: entityFilter, top: batch.length });
      for await (const result of response.results) nodes.push(result.document as unknown as AtlasEntity);
    }
  } else {
    const wanted = new Set(entityIds);
    nodes = (await readLocal("entities")).filter((row) => wanted.has(String(row.id))) as Array<
      AtlasEntity & Record<string, unknown>
    >;
  }
  return { nodes, edges, truncated };
}
