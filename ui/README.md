# REGDOCS Atlas UI

REGDOCS Atlas is the Next.js research workbench for CER REGDOCS evidence published through Azure AI Search and Microsoft Foundry.

The v1 rule is simple: **answers and derived regulatory intelligence stay connected to source evidence**.

For the exact data behind each feature, see [`DATA-CONTRACT.md`](DATA-CONTRACT.md). For the finite project acceptance gate, see [`../COMPLETION.md`](../COMPLETION.md).

## Production architecture

```text
Browser
  |
  v
Azure Container App: app-regdocs-<suffix>
  |
  +--> Azure AI Search
  |      - regdocs-chunks-hybrid       Search / Ask / HTML documents / Shelf / coverage
  |      - regdocs-entities            graph nodes
  |      - regdocs-relations           graph edges
  |      - regdocs-events              regulatory timeline
  |      - regdocs-claims              findings / claims
  |      - regdocs-obligations         commitments / obligations
  |
  +--> Microsoft Foundry
  |      - grounded Ask synthesis
  |      - Stage 6 structured regulatory extraction
  |
  +--> Log Analytics
         - UI/API logs
         - Stage 5 logs
         - Stage 6 logs
         - atlas.error events
         - Ask telemetry
```

Production Azure calls use managed identities. Search, Foundry, Log Analytics credentials, and the diagnostics operator token are not sent to normal browser code.

## V1 user-facing features

The visible v1 tools are implemented end to end:

- grounded natural-language Ask;
- keyword, hybrid vector, and optional semantic retrieval;
- company/project/filing/document/content-type filters;
- hybrid-to-keyword fallback;
- separate retrieved evidence and final cited evidence;
- citation validation;
- expandable Foundry/retrieval/timing/coverage details on answers;
- normalized HTML document viewing;
- page jumps, evidence highlighting, HTML table rendering, extracted figure text, and original REGDOCS links;
- Shelf evidence collection;
- Shelf-only Ask;
- Shelf CSV export;
- live corpus coverage;
- regulatory timeline;
- relationship graph;
- Findings & claims;
- Commitments & obligations;
- protected operator diagnostics;
- traceable `ATLAS-...` server-error references.

The v1 UI intentionally does not advertise unfinished arbitrary dataset generation, server-side shared workspaces, or an embedded PDF viewer.

## Document viewer

The source reader is an accessible HTML reconstruction of the indexed normalized document.

It uses the Stage 5 fields:

```text
document_id
chunk_id
chunk_index
chunk_type
heading
content
page_start
page_end
source_url
```

For a source preview Atlas fetches all indexed chunks for the document in `chunk_index` order. It groups them by page, highlights the selected evidence, supports page jumps, renders tables, shows extracted figure text, and exposes the original REGDOCS URL when available.

A duplicate PDF upload is not required. The UI explicitly warns that HTML layout may differ from the authoritative source.

## Ask provenance

Successful answers show a compact expandable footer such as:

```text
Grounded by Microsoft Foundry · Hybrid + semantic · 6 cited · 1.8s
```

Details include:

```text
Foundry deployment
retrieval mode / fallback
semantic ranking
retrieved evidence count
cited evidence count
retry count
Search / Foundry / total time
current Search index and filing-date coverage
```

The stream treats these separately:

```text
evidence    passages retrieved from Azure AI Search
citations   passages actually cited by the validated Foundry answer
```

If synthesis fails after Search succeeds, retrieved evidence can remain visible without being mislabeled as cited.

## Stage 6 regulatory intelligence

Stage 6 combines deterministic derivation with Microsoft Foundry structured extraction and publishes:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

Foundry-derived records are rejected unless their evidence chunk IDs belong to the exact extraction input. They retain confidence/origin/extractor information and remain `unreviewed` until a review workflow changes that state.

Stage 6 is the final data-processing stage.

## Start deployment here

```bash
./ui/deploy/deploy.sh
```

No arguments means **read-only guide**. It does not deploy anything. It checks what it can see and prints the next command.

The main commands are:

```bash
# Read-only guide
./ui/deploy/deploy.sh

# Verify the five-file Stage 4 cloud package
./ui/deploy/deploy.sh --check-data

# Bash/Terraform/Python/TypeScript/Next.js validation; no Azure changes
./ui/deploy/deploy.sh --validate

# Terraform preview against remote state
./ui/deploy/deploy.sh --plan

# Terraform/RBAC/config only
./ui/deploy/deploy.sh --infra-only

# UI/API update only; never starts Stage 5/6
./ui/deploy/deploy.sh --ui-only

# Explicit full infrastructure/workload deployment
./ui/deploy/deploy.sh --full

# Explicit Stage 5 publication
./ui/deploy/deploy.sh --restart-index

# Explicit Stage 6 publication
./ui/deploy/deploy.sh --restart-intelligence

# Current URL/images, Stage 5/6 executions, and recent logs
./ui/deploy/deploy.sh --status

# Trace one user-visible error
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

See [`deploy/README-FIRST-DEPLOYMENT.md`](deploy/README-FIRST-DEPLOYMENT.md) for a first deployment and [`OPERATIONS.md`](OPERATIONS.md) for the short runbook.

## No GitHub Actions

The project intentionally does not require a GitHub Actions workflow for deployment or verification.

Run:

```bash
./ui/deploy/deploy.sh --validate
```

before deployment. For infrastructure changes also review:

```bash
./ui/deploy/deploy.sh --plan
```

## Durable Terraform state and stable names

Terraform state lives in the configured Blob container, normally:

```text
terraform/regdocs-atlas.tfstate
```

`NAME_SUFFIX` is a stable installation ID, not a release number. Reuse it with the same remote state.

Terraform `prevent_destroy` protects the globally named ACR, Azure AI Search, and Foundry resources from accidental deletion/replacement.

If the state Blob exists, do not import resources merely because Cloud Shell restarted.

## Indexing safety

Keep:

```text
EMBEDDING_BATCH_SIZE="32"
```

The config example, Terraform default, cloud-indexer fallback, and deployment cap all enforce the production-safe value after larger request batches caused indexing failures.

## Cloud jobs

The publisher image is used by two independent Container Apps jobs:

```text
job-regdocs-<suffix>                Stage 5 Search/embeddings
job-regdocs-intelligence-<suffix>   Stage 6 Foundry regulatory intelligence
```

Stage 6 is started explicitly because corpus-wide model extraction can incur meaningful Foundry cost:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

Its extraction cache is checkpointed to Blob and completed requests can be reused after restarts.

For the first production-quality check you may temporarily set:

```text
INTELLIGENCE_DOCUMENT_LIMIT="10"
```

Inspect the pilot, then clear the limit for final corpus publication.

## Live diagnostics

Open:

```text
https://<atlas-host>/diagnostics
```

Shallow configuration is inexpensive. **Run live checks** requires the diagnostics operator token.

Retrieve it after Terraform is attached to the production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Live diagnostics exercise:

```text
corpus metadata
keyword Search
hybrid/vector Search
semantic ranking when configured
HTML document retrieval
Microsoft Foundry grounded inference
entities
relations
events
claims
obligations
```

## User-visible errors

Server faults from the v1 APIs receive references such as:

```text
ATLAS-0123ABCD4567EF89
```

Fast operator lookup:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Or use:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Ask question text is intentionally not included in structured error/Ask telemetry events.

## Runtime configuration

Terraform supplies the production settings, including:

```text
AZURE_CLIENT_ID
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_SEARCH_VECTOR_FIELD
AZURE_SEARCH_SEMANTIC_CONFIGURATION
FOUNDRY_PROJECT_ENDPOINT
FOUNDRY_MODEL_DEPLOYMENT
LOG_ANALYTICS_WORKSPACE_ID
REGDOCS_DIAGNOSTICS_TOKEN
```

The five Stage 6 index names default to the `regdocs-*` names in [`DATA-CONTRACT.md`](DATA-CONTRACT.md).

For local development Search may also use `AZURE_SEARCH_API_KEY`. Never put credentials or operator secrets in `NEXT_PUBLIC_*` variables.

## Local development

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

Release validation uses:

```bash
./ui/deploy/deploy.sh --validate
```

## Public URL

The generated Container Apps hostname has the form:

```text
https://app-regdocs-<suffix>.<environment-id>.<region>.azurecontainerapps.io
```

Print the real deployed URL with:

```bash
./ui/deploy/deploy.sh --status
```

A custom domain can provide a shorter address. `azurewebsites.net` is the App Service hostname family, not the Azure-managed Container Apps hostname.
