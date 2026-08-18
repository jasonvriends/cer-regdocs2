import { AzureKeyCredential, SearchClient } from "@azure/search-documents";
import { DefaultAzureCredential } from "@azure/identity";

export type CorpusStatus = {
  indexName: string;
  chunkCount: number;
  earliestFilingDate: string | null;
  latestFilingDate: string | null;
  generatedAt: string;
};

type CorpusStatusDocument = {
  filing_date?: string | null;
};

const CACHE_MS = 5 * 60_000;
let cached: { at: number; value: CorpusStatus } | null = null;

function config() {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  const indexName =
    process.env.AZURE_SEARCH_INDEX?.trim() ||
    process.env.AZURE_SEARCH_INDEX_NAME?.trim() ||
    "regdocs-chunks";
  const apiKey = process.env.AZURE_SEARCH_API_KEY?.trim();
  if (!endpoint) throw new Error("AZURE_SEARCH_ENDPOINT is not configured");
  return { endpoint, indexName, apiKey };
}

function credential(apiKey: string | undefined) {
  return apiKey ? new AzureKeyCredential(apiKey) : new DefaultAzureCredential();
}

async function boundaryDate(
  client: SearchClient<CorpusStatusDocument>,
  direction: "asc" | "desc",
) {
  const response = await client.search("*", {
    top: 1,
    filter: "filing_date ne null",
    orderBy: [`filing_date ${direction}`],
    queryType: "simple",
    select: ["filing_date"],
  });
  for await (const result of response.results) {
    return result.document.filing_date?.trim() || null;
  }
  return null;
}

export async function getCorpusStatus(options: { refresh?: boolean } = {}): Promise<CorpusStatus> {
  const now = Date.now();
  if (!options.refresh && cached && now - cached.at < CACHE_MS) return cached.value;

  const settings = config();
  const client = new SearchClient<CorpusStatusDocument>(
    settings.endpoint,
    settings.indexName,
    credential(settings.apiKey),
  );

  const countResponse = await client.search("*", {
    top: 1,
    includeTotalCount: true,
    queryType: "simple",
    select: ["filing_date"],
  });
  for await (const _ of countResponse.results) break;

  const [earliestFilingDate, latestFilingDate] = await Promise.all([
    boundaryDate(client, "asc"),
    boundaryDate(client, "desc"),
  ]);

  const value: CorpusStatus = {
    indexName: settings.indexName,
    chunkCount: countResponse.count ?? 0,
    earliestFilingDate,
    latestFilingDate,
    generatedAt: new Date().toISOString(),
  };
  cached = { at: now, value };
  return value;
}
