import { DefaultAzureCredential } from "@azure/identity";
import { SearchClient } from "@azure/search-documents";

export type RegdocsSearchDocument = {
  chunk_id: string;
  document_id: string;
  title?: string | null;
  heading?: string | null;
  content?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  project?: string | null;
  company?: string | null;
  filing_date?: string | null;
  chunk_type?: string | null;
  source_url?: string | null;
  resolved_url?: string | null;
  element_paths?: string[] | null;
};

export type AtlasSearchResult = RegdocsSearchDocument & {
  score: number | null;
};

let cachedClient: SearchClient<RegdocsSearchDocument> | undefined;

function searchConfig() {
  const endpoint = process.env.AZURE_SEARCH_ENDPOINT?.trim();
  const indexName = process.env.AZURE_SEARCH_INDEX?.trim() || "regdocs-chunks";

  if (!endpoint) {
    throw new Error("AZURE_SEARCH_ENDPOINT is not configured");
  }

  return { endpoint, indexName };
}

function getSearchClient() {
  if (!cachedClient) {
    const { endpoint, indexName } = searchConfig();
    cachedClient = new SearchClient<RegdocsSearchDocument>(
      endpoint,
      indexName,
      new DefaultAzureCredential(),
    );
  }

  return cachedClient;
}

export async function searchRegdocs(query: string, top = 20): Promise<AtlasSearchResult[]> {
  const client = getSearchClient();
  const response = await client.search(query, {
    top,
    select: [
      "chunk_id",
      "document_id",
      "title",
      "heading",
      "content",
      "page_start",
      "page_end",
      "project",
      "company",
      "filing_date",
      "chunk_type",
      "source_url",
      "resolved_url",
      "element_paths",
    ],
  });

  const results: AtlasSearchResult[] = [];
  for await (const result of response.results) {
    results.push({
      ...result.document,
      score: result.score ?? null,
    });
  }

  return results;
}
