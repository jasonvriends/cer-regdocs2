# REGDOCS Atlas UI

REGDOCS Atlas is the Next.js research workbench for the Stage 5 Azure AI Search index and Stage 6 regulatory-intelligence indexes.

The Python pipeline acquires, analyzes, normalizes, enriches, and publishes REGDOCS data. The `ui/` application is the web layer that lets people search that published corpus, open normalized documents, collect evidence, inspect timelines and relationships, and ask Microsoft Foundry for grounded answers.

## Production architecture

```text
Browser
  |
  v
Azure Container App: app-regdocs-<suffix>
  |
  +--> Azure AI Search
  |      - regdocs chunks / hybrid search
  |      - entities
  |      - relations
  |      - events
  |
  +--> Microsoft Foundry
  |      - grounded Ask
  |      - chat deployment
  |
  +--> Log Analytics
         - Container App console logs
         - structured atlas.error events
         - Ask telemetry
```

The browser never receives Azure Search credentials, Foundry credentials, Log Analytics credentials, or the diagnostics operator token. Server-side Azure calls use managed identity in production.

## What is implemented

The current workbench includes:

- server-side Azure AI Search access;
- keyword, hybrid vector, and optional semantic retrieval;
- Microsoft Foundry grounded answers;
- citation validation before generated answer text is shown;
- automatic hybrid-to-keyword fallback when needed;
- retrieved evidence preserved even when Foundry synthesis fails;
- HTML document reconstruction instead of depending on remote PDF embedding;
- page/chunk navigation and evidence highlighting;
- Research Shelf / evidence collection;
- regulatory timelines and relationship graphs from Stage 6 indexes;
- `/diagnostics` for configuration and explicit live service checks;
- structured Ask telemetry;
- user-visible `ATLAS-...` error references for server faults;
- protected Log Analytics error lookup at `/diagnostics/errors`.

See [`PRODUCT.md`](PRODUCT.md) for the product/capability contract and [`OPERATIONS.md`](OPERATIONS.md) for the focused operator runbook.

---

# Required runtime configuration

The production Terraform deployment configures these values automatically on the Container App:

```text
AZURE_CLIENT_ID
LOG_ANALYTICS_WORKSPACE_ID
REGDOCS_DIAGNOSTICS_TOKEN
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX
AZURE_SEARCH_VECTOR_FIELD
AZURE_SEARCH_SEMANTIC_CONFIGURATION
FOUNDRY_PROJECT_ENDPOINT
FOUNDRY_MODEL_DEPLOYMENT
FOUNDRY_SAFETY_SALT
```

The Stage 6 intelligence readers default to the deployed index names `regdocs-entities`, `regdocs-relations`, and `regdocs-events`. They can be overridden for nonstandard deployments with `AZURE_SEARCH_ENTITIES_INDEX`, `AZURE_SEARCH_RELATIONS_INDEX`, and `AZURE_SEARCH_EVENTS_INDEX`.

For local development, Azure Search may also use a read-only `AZURE_SEARCH_API_KEY`. Do not put secrets in variables beginning with `NEXT_PUBLIC_`; Next.js exposes those variables to browser code.

Foundry calls and production Search/Log Analytics calls use `DefaultAzureCredential`. The Terraform deployment assigns the Container App's user-assigned identity the required Azure roles.

---

# Local development

Prerequisites:

- Node.js 22 or newer;
- an Azure AI Search index produced by Stage 5;
- Azure credentials or a read-only Search query key.

From the repository root:

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

Before publishing UI code, run:

```bash
cd ui
npm ci
npm run typecheck
npm run build
```

GitHub Actions also runs typecheck/build and validates the Terraform configuration. It does not run a deployed smoke test.

---

# Production deployment from Azure Cloud Shell

The production deployment is under [`ui/deploy/`](deploy/). It uses:

- Terraform for Azure resources and RBAC;
- Azure Blob Storage for durable Terraform state;
- Azure Container Registry for immutable UI/indexer images;
- Azure Container Apps for the web UI and resumable indexing job;
- Microsoft Foundry for embeddings/chat;
- Azure AI Search for corpus and intelligence indexes;
- Log Analytics for application logs and error lookup.

## Important: Cloud Shell does not need to keep Terraform state locally

Terraform state is remote. By default it lives at:

```text
terraform/regdocs-atlas.tfstate
```

inside the existing Blob container configured by `STORAGE_ACCOUNT` and `BLOB_CONTAINER`.

`deploy.sh` runs `terraform init -reconfigure` against that Blob on every deployment. A new or reset Cloud Shell session therefore does **not** mean Terraform state has been lost.

### Verify the remote state before considering imports

```bash
cd ~/cer-regdocs2
source ui/deploy/config.env

read -rsp "Paste SAS: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"

az storage blob show \
  --account-name "$STORAGE_ACCOUNT" \
  --container-name "$BLOB_CONTAINER" \
  --name "${STATE_BLOB:-terraform/regdocs-atlas.tfstate}" \
  --sas-token "$AZURE_STORAGE_SAS_TOKEN" \
  --query '{name:name,size:properties.contentLength,lastModified:properties.lastModified}' \
  -o table
```

If that Blob exists, **do not import the Azure resources**. Reuse the remote state.

If the state Blob is missing but the Azure resources still exist, stop before running `terraform apply`. That is a state-recovery/import situation and the existing resource IDs should be imported deliberately rather than allowing Terraform to treat the deployment as new.

## One-time Cloud Shell setup

```bash
git clone <repository-url> cer-regdocs2
cd cer-regdocs2
cp ui/deploy/config.env.example ui/deploy/config.env
code ui/deploy/config.env
```

At minimum configure:

```text
SUBSCRIPTION_ID
NAME_SUFFIX
STORAGE_ACCOUNT
STORAGE_RESOURCE_GROUP
BLOB_CONTAINER
CONFIRM_BILLABLE_DEPLOYMENT=yes
```

Then provide a private container SAS with Read, Create, Write, and List permission. Keep the SAS out of Git and out of `config.env`.

```bash
read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
export AZURE_STORAGE_SAS_TOKEN
```

## Full infrastructure + workload deployment

Use this when:

- provisioning the environment for the first time;
- Terraform changed;
- RBAC changed;
- Container App environment variables changed;
- Foundry/Search infrastructure changed;
- normalized corpus/index inputs changed.

Update the checkout first:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master
```

Then run:

```bash
./ui/deploy/deploy.sh
```

The script is designed to be rerun after Cloud Shell disconnects. ACR builds continue in Azure and the indexing job runs independently of the Cloud Shell session.

If the script exits after queueing ACR builds, rerun the same command after the builds complete:

```bash
./ui/deploy/deploy.sh
```

When normalized `chunks.jsonl` or `provenance.jsonl` changed and you intentionally want a new index publication:

```bash
./ui/deploy/deploy.sh --restart-index
```

To provision without starting an otherwise-needed indexing job:

```bash
./ui/deploy/deploy.sh --no-start
```

**Note:** the deployment script tags both UI and indexer images with the current Git commit. A changed indexer image may cause the deployment logic to start a new indexing execution. For a normal UI-code-only release, use the UI-only procedure below instead.

---

# Deploy only the UI

Use this when Terraform, RBAC, Search, Foundry, and index data are already correct and the change is only Next.js/UI/API code.

This path:

- builds only `regdocs-ui`;
- updates only the Container App;
- does not run Terraform;
- does not touch Search or Foundry resources;
- does not start the indexing job;
- does not require the Blob SAS.

From Azure Cloud Shell:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master

source ui/deploy/config.env
az account set --subscription "$SUBSCRIPTION_ID"

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-regdocs-atlas}"
TAG="$(git rev-parse --short=12 HEAD)"
ACR_NAME="crregdocs${NAME_SUFFIX}"
APP_NAME="app-regdocs-${NAME_SUFFIX}"

az acr build \
  --registry "$ACR_NAME" \
  --image "regdocs-ui:$TAG" \
  --file ui/deploy/containers/ui.Dockerfile \
  .

ACR_LOGIN_SERVER="$(az acr show \
  --name "$ACR_NAME" \
  --query loginServer \
  -o tsv)"

az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$ACR_LOGIN_SERVER/regdocs-ui:$TAG"
```

Confirm the active revision:

```bash
az containerapp revision list \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[].{Revision:name,Active:properties.active,Traffic:properties.trafficWeight,Created:properties.createdTime}" \
  -o table
```

Get the public hostname:

```bash
UI_HOST="$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn \
  -o tsv)"

echo "https://$UI_HOST"
```

### Important for the error-observability release

The first deployment containing the Log Analytics error lookup must use the **full Terraform deployment**, because it adds RBAC, the workspace ID, and the diagnostics operator token to the Container App. After those infrastructure changes have been applied once, later UI-only releases can use the UI-only procedure above.

---

# Diagnostics

Open:

```text
https://<atlas-host>/diagnostics
```

The page first shows inexpensive configuration checks. Clicking **Run live checks** performs real server-side checks against the configured services, including Azure AI Search, hybrid/semantic retrieval, Microsoft Foundry, the document reader backend, and Stage 6 intelligence indexes.

These live checks are operator-initiated diagnostics. They are not an automated smoke-test suite.

---

# User-visible errors and Log Analytics

Real server faults from Ask, Search, document view, evidence lookup, timeline, and relationship graph are assigned a random reference such as:

```text
ATLAS-0123ABCD4567EF89
```

The user sees a safe message containing that reference, for example:

```text
The grounded answer could not be generated.
Reference: ATLAS-0123ABCD4567EF89
```

The server writes the same reference as a structured `atlas.error` event to the Container App console log. The Container Apps environment is already connected to the deployment's Log Analytics workspace.

Normal validation errors, no-result responses, 404s, and rate-limit responses are not treated as server faults and therefore do not receive an operator error record.

Ask question text is not added to the structured error event or Ask telemetry.

## Operator lookup URL

Open:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Enter the diagnostics operator token when prompted. The browser sends that token only to the Atlas server; the server uses managed identity to query Log Analytics.

Retrieve the token after Terraform has been initialized against the production remote state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

The Terraform output is marked sensitive. Do not put this token in client-side environment variables, screenshots, tickets, or Git.

## Search the Log Analytics workspace directly

In the Azure Portal, open the deployment's Log Analytics workspace and run:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

The Terraform deployment retains Log Analytics data for 30 days unless that configuration is changed.

## What an operator should capture from an error

When investigating a user report, ask for:

1. the exact `ATLAS-...` reference;
2. approximately when the error occurred;
3. what feature they were using (Ask, Search, document, timeline, graph);
4. optionally the page/filing/document they were viewing.

The error reference is normally enough to find the underlying server exception. Avoid asking users to send sensitive credentials or copy large browser console dumps unless the server trace is insufficient.

---

# Ask / Foundry observability

Successful and failed Ask requests emit structured `REGDOCS ask telemetry` without question text. The telemetry includes operational fields such as:

```text
retrieval mode
hybrid -> keyword fallback
number of evidence passages
whether Foundry was used
Foundry deployment/model
citation-validation status
retry count
Search time
Foundry time
total time
```

This makes it possible to distinguish failures in retrieval from failures in Foundry generation or citation validation.

If Foundry fails after Azure AI Search has already retrieved evidence, Atlas keeps those source passages visible rather than discarding the successful retrieval.

---

# Search API

The browser calls `GET /api/search`. The route builds Azure OData filters on the server instead of accepting arbitrary filter expressions.

Examples:

```text
/api/search?q=pipeline%20abandonment
/api/search?q=*&company=Trans%20Mountain%20Pipeline%20ULC
/api/search?q=*&filingId=<filing-id>
/api/search?q=*&documentId=<document-id>&sort=chunk
/api/search?q=groundwater&documentId=<document-id>&page=42
/api/search?q=cost&chunkType=table
/api/search?q=route%20map&chunkType=figure
/api/search?q=investigation&role=Applicant&commodity=Oil
/api/search?q=impacts%20to%20traditional%20land%20use&mode=hybrid
```

Supported parameters include `q`, `top`, `sort`, `page`, `documentId`, `filingId`, `filingNumber`, `company`, `project`, `chunkType`, `applicationType`, `commodity`, `documentType`, `fileType`, `role`, and `mode=keyword|hybrid`.

---

# HTML document viewer

The normal source preview uses the normalized HTML document reader rather than embedding the remote REGDOCS PDF. `GET /api/document-view` retrieves an ordered window of indexed chunks around a requested page. The UI groups those chunks into page-like sheets, highlights matching evidence, renders extracted tables/figure labels where possible, and provides a link back to the authoritative REGDOCS source.

The HTML reconstruction is optimized for search, reading, accessibility, and evidence collection. It does not claim pixel fidelity to the original document.

---

# Grounded Ask architecture

`POST /api/ask` is a controlled retrieval-augmented generation route.

For corpus questions, the server selects a retrieval strategy and retrieves up to 12 passages from Azure AI Search. Exact phrase/record-like questions can favor lexical search; normal conceptual questions favor hybrid retrieval, with keyword fallback when hybrid is unavailable or fails.

For Shelf/Workspace questions, Atlas refetches the exact chunk IDs from Search so browser-supplied passage text is not trusted as evidence.

Only retrieved passages are sent to Foundry. The answer must contain valid `[S#]` citations that map back to those fixed passages. Model text is held until citation validation succeeds. If generation or citation validation fails, the retrieved evidence remains available to the user and the failure receives an `ATLAS-...` reference when it is a server fault.

Important conclusions should still be verified against the authoritative REGDOCS source.

---

# Operational files

```text
ui/README.md                  UI architecture, deployment, diagnostics, errors
ui/OPERATIONS.md              focused error/operations runbook
ui/deploy/README.md           Cloud Shell/Terraform deployment details
ui/deploy/deploy.sh           resumable full deployment script
ui/deploy/config.env          ignored local deployment configuration
ui/deploy/terraform/          Azure infrastructure and RBAC
```

For the Python acquisition/analysis pipeline, return to the repository-level [`README.md`](../README.md) and [`SYNTAX.md`](../SYNTAX.md).
