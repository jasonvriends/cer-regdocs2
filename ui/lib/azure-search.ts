import { DefaultAzureCredential } from "@azure/identity";
import { AzureKeyCredential, SearchClient } from "@azure/search-documents";

export type RegdocsSearchDocument = {
  chunk_id: string;
  document_id: string;
  chunk_index?: number | null;
  chunk_type?: string | null;
  title?: string | null;
  heading?: string | null;
  content?: string | null;
  section_path?: string[] | null;
  page_start?: number | null;
  page_end?: number | null;
  content_index?: number | null;
  word_count?: number | null;
  filing_date?: string | null;
  submitter?: string | null;
  company?: string | null;
  project?: string | null;
  filing_number?: string | null;
  filing_id?: string | null;
  application_types?: string[] | null;
  commodities?: string[] | null;
  document_types?: string[] | null;
  file_types?: string[] | null;
  roles?: string[] | null;
  source_url?: string | null;
  resolved_url?: string | null;
  file_path?: string | null;
  file_sha256?: string | null;
  analyzer_id?: string | null;
  api_version?: string | null;
  normalizer_version?: string | null;
  table_id?: string | null;
  table_part?: number | null;
  table_row_start?: number | null;
  table_row_end?: number | null;
  figure_id?: string | null;
  azure_figure_id?: string | null;
  element_paths?: string[] | null;
  local_element_paths?: string[] | null;
};

export type AtlasSearchResult = RegdocsSearchDocument & {
  score: number | null;
};

export type AtlasFacetBucket = {
  value: string;
  count: number;
};

export type AtlasFacets = Record<string, AtlasFacetBucket[]>;

export type AtlasSearchSort = "relevance" | "newest" | "oldest" | "chunk";

export type AtlasSearchRequest = {
  query: string;
  top?: number;
  documentIds?: string[];
  filingIds?: string[];
  filingNumbers?: string[];
  companies?: string[];
  projects?: string[];
  chunkTypes?: string[];
  applicationTypes?: string[];
  commodities?: string[];
  documentTypes?: string[];
  fileTypes?: string[];
  roles?: string[];
  page?: number;
  sort?: AtlasSearchSort;
};

export type AtlasSearchResponse = {
  query: string;
  count: number;
  totalCount: number | null;
  facets: AtlasFacets;
  results: AtlasSearchResult[];
};

let cachedClient: SearchClient<RegdocsSearchDocument> | undefined;
let cachedSignature = "";

function searchConfig() {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  const indexName =
    process.env.AZURE_SEARCH_INDEX?.trim() ||
    process.env.AZURE_SEARCH_INDEX_NAME?.trim() ||
    "regdocs-chunks";
  const apiKey = process.env.AZURE_SEARCH_API_KEY?.trim();

  if (!endpoint) {
    throw new Error("AZURE_SEARCH_ENDPOINT is not configured");
  }

  return { endpoint, indexName, apiKey };
}

function getSearchClient() {
  const config = searchConfig();
  const authentication = config.apiKey ? "api-key" : "managed-identity";
  const signature = `${config.endpoint}|${config.indexName}|${authentication}|${config.apiKey ?? ""}`;

  if (!cachedClient || signature !== cachedSignature) {
    const credential = config.apiKey
      ? new AzureKeyCredential(config.apiKey)
      : new DefaultAzureCredential();
    cachedClient = new SearchClient<RegdocsSearchDocument>(
      config.endpoint,
      config.indexName,
      credential,
    );
    cachedSignature = signature;
  }

  return cachedClient;
}

function cleanValues(values: string[] | undefined) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))];
}

function escapeODataString(value: string) {
  return value.replace(/'/g, "''");
}

function scalarClause(field: string, values: string[] | undefined) {
  const cleaned = cleanValues(values);
  if (!cleaned.length) return null;
  return `(${cleaned.map((value) => `${field} eq '${escapeODataString(value)}'`).join(" or ")})`;
}

function collectionClause(field: string, values: string[] | undefined) {
  const cleaned = cleanValues(values);
  if (!cleaned.length) return null;
  return `(${cleaned
    .map((value) => `${field}/any(value: value eq '${escapeODataString(value)}')`)
    .join(" or ")})`;
}

function buildFilter(request: AtlasSearchRequest) {
  const clauses = [
    scalarClause("document_id", request.documentIds),
    scalarClause("filing_id", request.filingIds),
    scalarClause("filing_number", request.filingNumbers),
    scalarClause("company", request.companies),
    scalarClause("project", request.projects),
    scalarClause("chunk_type", request.chunkTypes),
    collectionClause("application_types", request.applicationTypes),
    collectionClause("commodities", request.commodities),
    collectionClause("document_types", request.documentTypes),
    collectionClause("file_types", request.fileTypes),
    collectionClause("roles", request.roles),
  ].filter((value): value is string => Boolean(value));

  if (request.page && Number.isInteger(request.page) && request.page > 0) {
    clauses.push(`(page_start le ${request.page} and page_end ge ${request.page})`);
  }

  return clauses.length ? clauses.join(" and ") : undefined;
}

function orderBy(sort: AtlasSearchSort | undefined) {
  switch (sort) {
    case "newest":
      return ["filing_date desc"];
    case "oldest":
      return ["filing_date asc"];
    case "chunk":
      return ["chunk_index asc"];
    default:
      return undefined;
  }
}

function normalizeFacets(input: unknown): AtlasFacets {
  if (!input || typeof input !== "object") return {};
  const output: AtlasFacets = {};

  for (const [field, rawBuckets] of Object.entries(input as Record<string, unknown>)) {
    if (!Array.isArray(rawBuckets)) continue;
    output[field] = rawBuckets
      .map((bucket) => {
        if (!bucket || typeof bucket !== "object") return null;
        const value = (bucket as { value?: unknown }).value;
        const count = (bucket as { count?: unknown }).count;
        if (value === undefined || value === null) return null;
        return {
          value: String(value),
          count: typeof count === "number" ? count : Number(count ?? 0),
        };
      })
      .filter((bucket): bucket is AtlasFacetBucket => Boolean(bucket));
  }

  return output;
}

const SELECT_FIELDS = [
  "chunk_id",
  "document_id",
  "chunk_index",
  "chunk_type",
  "title",
  "heading",
  "content",
  "section_path",
  "page_start",
  "page_end",
  "content_index",
  "word_count",
  "filing_date",
  "submitter",
  "company",
  "project",
  "filing_number",
  "filing_id",
  "application_types",
  "commodities",
  "document_types",
  "file_types",
  "roles",
  "source_url",
  "resolved_url",
  "file_path",
  "file_sha256",
  "analyzer_id",
  "api_version",
  "normalizer_version",
  "table_id",
  "table_part",
  "table_row_start",
  "table_row_end",
  "figure_id",
  "azure_figure_id",
  "element_paths",
  "local_element_paths",
] as const;

const FACETS = [
  "company,count:12,sort:count",
  "project,count:12,sort:count",
  "chunk_type,count:10,sort:count",
  "application_types,count:12,sort:count",
  "commodities,count:12,sort:count",
  "document_types,count:12,sort:count",
  "file_types,count:12,sort:count",
  "roles,count:12,sort:count",
];

export async function searchRegdocs(request: AtlasSearchRequest): Promise<AtlasSearchResponse> {
  const client = getSearchClient();
  const query = request.query.trim() || "*";
  const top = Math.min(Math.max(request.top ?? 20, 1), 50);
  const response = await client.search(query, {
    top,
    filter: buildFilter(request),
    facets: FACETS,
    includeTotalCount: true,
    orderBy: orderBy(request.sort),
    queryType: "simple",
    searchMode: "any",
    select: SELECT_FIELDS,
  });

  const results: AtlasSearchResult[] = [];
  for await (const result of response.results) {
    results.push({
      ...result.document,
      score: result.score ?? null,
    });
  }

  return {
    query,
    count: results.length,
    totalCount: response.count ?? null,
    facets: normalizeFacets(response.facets),
    results,
  };
}
