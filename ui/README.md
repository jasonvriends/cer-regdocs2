# REGDOCS Atlas UI

REGDOCS Atlas is the Next.js research workbench for CER REGDOCS evidence published through Azure AI Search and Microsoft Foundry.

It is designed around one rule: **generated answers and derived regulatory intelligence must stay connected to source evidence**.

## Production architecture

```text
Browser
  |
  v
Azure Container App: app-regdocs-<suffix>
  |
  +--> Azure AI Search
  |      - regdocs-chunks-hybrid       document chunks / keyword / vector / semantic
  |      - regdocs-entities            Stage 6 entities
  |      - regdocs-relations           Stage 6 relationships
  |      - regdocs-events              Stage 6 chronology
  |      - regdocs-claims              Stage 6 findings/claims
  |      - regdocs-obligations         Stage 6 conditions/commitments/obligations
  |
  +--> Microsoft Foundry
  |      - grounded Ask synthesis
  |      - Stage 6 structured regulatory extraction
  |
  +--> Log Analytics
         - UI/API logs
         - Stage 5 job logs
         - Stage 6 job logs
         - structured atlas.error events
         - Ask telemetry
```

Production Azure calls use managed identities. Search, Foundry, Log Analytics credentials, and the diagnostics operator token are never sent to normal browser code.

## User-facing features

Atlas currently provides:

- natural-language grounded Ask;
- keyword, hybrid vector, and optional semantic retrieval;
- hybrid-to-keyword fallback;
- separate **retrieved evidence** and **actually cited evidence**;
- citation validation before generated answer text is accepted;
- an expandable per-answer line proving which Microsoft Foundry deployment and retrieval path were used;
- normalized HTML source reading with page/chunk navigation and evidence highlighting;
- Research Shelf evidence collection/export;
- live corpus coverage from the configured Search index;
- regulatory timeline;
- relationship graph;
- **Findings & claims** view;
- **Commitments & obligations** view with explicit condition/commitment/deadline/status filters;
- protected operator diagnostics;
- traceable `ATLAS-...` server-error references.

Model-extracted Stage 6 records remain visibly marked `unreviewed` until a review workflow changes that state. The UI does not infer that an obligation is outstanding unless the extracted status explicitly indicates an open/pending/outstanding state.

## Ask provenance

Successful answers show a compact expandable footer such as:

```text
Grounded by Microsoft Foundry · Hybrid + semantic · 6 cited · 1.8s
```

The details include:

```text
Foundry deployment/model
retrieval mode
hybrid fallback if any
semantic ranking status
retrieved evidence count
cited evidence count
retry count
Search time
Foundry time
total time
current Search index and filing-date coverage
```

The Ask stream distinguishes:

```text
evidence    passages retrieved from Azure AI Search
citations   passages the validated Foundry answer actually cited
```

If Foundry generation fails after Search succeeds, the retrieved evidence remains available without being mislabeled as cited.

## Regulatory intelligence

Stage 6 has two layers:

```text
normalized documents
  |
  +--> deterministic metadata derivation
  |
  +--> Microsoft Foundry structured extraction
          - events
          - claims
          - obligations
          - relationships
          - evidence chunk IDs
          - confidence
          - review state
```

Foundry output is rejected if it does not cite valid chunk IDs from the exact input batch. Model-derived records carry evidence page/chunk information, extractor/model version, confidence, and `review_status=unreviewed`.

The cloud Stage 6 job publishes five Search indexes:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

## Operator commands

```bash
# Local preflight; no Azure calls.
./ui/deploy/deploy.sh --validate

# Terraform preview against the durable remote state.
./ui/deploy/deploy.sh --plan

# UI/API update only; no Stage 5/Stage 6 run.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only.
./ui/deploy/deploy.sh --infra-only

# Full infrastructure/workload deployment.
./ui/deploy/deploy.sh

# Explicit Stage 5 hybrid-index publication.
./ui/deploy/deploy.sh --restart-index

# Explicit Stage 6 Foundry extraction/publication.
./ui/deploy/deploy.sh --restart-intelligence

# Refresh both publication layers.
./ui/deploy/deploy.sh --restart-index --restart-intelligence

# UI URL/images, Stage 5/6 executions, recent Log Analytics logs.
./ui/deploy/deploy.sh --status

# Trace a user-visible server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

See [`deploy/README.md`](deploy/README.md) for the full Cloud Shell deployment/runbook and [`OPERATIONS.md`](OPERATIONS.md) for the short operator workflow.

## No GitHub Actions

This repository intentionally does not use a GitHub Actions verification/deployment workflow. Before deployment run:

```bash
./ui/deploy/deploy.sh --validate
```

It checks Bash syntax, Terraform formatting/validation, Python compilation, TypeScript, and the production Next.js build without calling Azure.

For infrastructure changes follow it with:

```bash
./ui/deploy/deploy.sh --plan
```

## Terraform state and naming

Cloud Shell does not need to preserve local Terraform state. State lives in the configured Blob container, normally at:

```text
terraform/regdocs-atlas.tfstate
```

`NAME_SUFFIX` is a stable installation identifier, not a release number. Reuse it with the same remote state for every normal update.

Terraform `prevent_destroy` protects the globally named ACR, Azure AI Search, and Foundry resources from accidental deletion/replacement.

If the state Blob exists, do not import resources just because Cloud Shell was restarted.

## Indexing safety setting

Keep:

```text
EMBEDDING_BATCH_SIZE="32"
```

The config example, Terraform default, cloud-indexer fallback, and deployment cap all use 32 after larger batches caused indexing failures.

## Stage 5 and Stage 6 jobs

The shared publisher image is used by two independent Container Apps jobs:

```text
job-regdocs-<suffix>                Stage 5 hybrid chunks/embeddings
job-regdocs-intelligence-<suffix>   Stage 6 Foundry regulatory intelligence
```

Stage 6 is not started implicitly by an ordinary deployment because corpus-wide model extraction can incur meaningful Foundry cost. Start it deliberately with:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

Its SQLite extraction cache is checkpointed to Blob every 15 minutes:

```text
workspace/6_enrich/model/extraction.sqlite
```

A restarted job reuses completed requests for unchanged normalized input/model/prompt versions.

For a first pilot, set in `config.env`:

```bash
INTELLIGENCE_DOCUMENT_LIMIT="10"
export TF_VAR_intelligence_document_limit="$INTELLIGENCE_DOCUMENT_LIMIT"
```

Apply configuration and run Stage 6, inspect the results, then clear the limit for full-corpus extraction.

## Live diagnostics

Open:

```text
https://<atlas-host>/diagnostics
```

Shallow configuration is inexpensive. **Run live checks** requires the operator token because it makes real service calls and returns operational diagnostics.

Retrieve the token after Terraform is attached to production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Live checks verify:

```text
corpus metadata
keyword Search
hybrid/vector Search
semantic ranking when configured
HTML document retrieval
real Microsoft Foundry grounded inference
entities index
relations index
events index
claims index
obligations index
```

## User-visible errors

Real server faults from Ask, Search, source reading, evidence lookup, timeline, graph, claims, and obligations receive references such as:

```text
ATLAS-0123ABCD4567EF89
```

Fastest operator lookup:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Or open:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

The web lookup requires the operator token. Ask question text is intentionally not written to structured error/Ask telemetry events.

## Runtime configuration

Terraform supplies the normal production settings, including:

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

The intelligence readers default to:

```text
AZURE_SEARCH_ENTITIES_INDEX=regdocs-entities
AZURE_SEARCH_RELATIONS_INDEX=regdocs-relations
AZURE_SEARCH_EVENTS_INDEX=regdocs-events
AZURE_SEARCH_CLAIMS_INDEX=regdocs-claims
AZURE_SEARCH_OBLIGATIONS_INDEX=regdocs-obligations
```

For local development, Azure Search may also use `AZURE_SEARCH_API_KEY`. Never put credentials or operator secrets in `NEXT_PUBLIC_*` variables.

## Local development

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

Production verification:

```bash
cd ui
npm ci
npm run typecheck
npm run build
```

## Public URL

The Azure-managed Container Apps hostname looks like:

```text
https://app-regdocs-<suffix>.<environment-id>.<region>.azurecontainerapps.io
```

Print the real current URL with:

```bash
./ui/deploy/deploy.sh --status
```

For a short stable address such as `regdocsatlas.example.com`, bind a custom domain you own. `azurewebsites.net` is the App Service hostname family, not the generated hostname for Azure Container Apps.
