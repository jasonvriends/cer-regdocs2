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
  |      - chunks / keyword + hybrid search
  |      - entities
  |      - relations
  |      - events
  |
  +--> Microsoft Foundry
  |      - grounded Ask
  |      - chat deployment
  |
  +--> Log Analytics
         - UI/API console logs
         - indexer job console logs
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
- normalized HTML document reconstruction;
- page/chunk navigation and evidence highlighting;
- Research Shelf / evidence collection;
- regulatory timelines and relationship graphs from Stage 6 indexes;
- `/diagnostics` for configuration and operator-initiated live checks;
- structured Ask telemetry;
- user-visible `ATLAS-...` references for server faults;
- protected Log Analytics error lookup at `/diagnostics/errors`.

See [`PRODUCT.md`](PRODUCT.md) for the product/capability contract and [`OPERATIONS.md`](OPERATIONS.md) for the short operator runbook.

---

# Operator quick reference

These are the normal deployment and support commands:

```bash
# Normal UI/API update. Applies Terraform changes but never starts indexing.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. No image build and no indexing.
./ui/deploy/deploy.sh --infra-only

# Full UI + indexer deployment.
./ui/deploy/deploy.sh

# Full deployment and explicitly start a fresh index publication.
./ui/deploy/deploy.sh --restart-index

# Read-only deployment/indexing status plus latest indexer Log Analytics output.
./ui/deploy/deploy.sh --status

# Read-only lookup for a user-visible server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

`--full` is accepted as an explicit alias for the default full mode:

```bash
./ui/deploy/deploy.sh --full
```

`--no-start` has been removed. It was ambiguous because an indexer image change could still trigger an indexing run. Use `--ui-only` or `--infra-only`, which have precise no-indexing behavior.

## Which deployment command should I use?

| Change | Command |
|---|---|
| Next.js UI or API code | `./ui/deploy/deploy.sh --ui-only` |
| UI code plus Terraform/RBAC/env changes | `./ui/deploy/deploy.sh --ui-only` |
| Terraform/RBAC/env only | `./ui/deploy/deploy.sh --infra-only` |
| Search/Foundry infrastructure plus workloads | `./ui/deploy/deploy.sh` |
| Indexer code changed | `./ui/deploy/deploy.sh` |
| Normalized corpus changed and must be republished | `./ui/deploy/deploy.sh --restart-index` |
| First deployment | `./ui/deploy/deploy.sh` |
| Check current deployment/indexer status | `./ui/deploy/deploy.sh --status` |
| Investigate a user error | `./ui/deploy/deploy.sh --error ATLAS-...` |

The UI and indexer have separate Terraform image tags. A UI-only deployment advances only the UI tag, so a UI commit cannot accidentally look like an indexer change.

---

# Runtime configuration

The production Terraform deployment configures these values on the Container App:

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

The Stage 6 readers default to `regdocs-entities`, `regdocs-relations`, and `regdocs-events`. Nonstandard deployments can override those with `AZURE_SEARCH_ENTITIES_INDEX`, `AZURE_SEARCH_RELATIONS_INDEX`, and `AZURE_SEARCH_EVENTS_INDEX`.

For local development, Azure Search may also use a read-only `AZURE_SEARCH_API_KEY`. Never put secrets in `NEXT_PUBLIC_*` variables because Next.js exposes those to browser code.

Foundry, Search, and Log Analytics calls use `DefaultAzureCredential` in Azure. Terraform assigns the Container App's user-assigned identity the required roles.

---

# Local validation

Prerequisites:

- Node.js 22 or newer;
- an Azure AI Search index produced by Stage 5;
- Azure credentials or a read-only Search query key.

```bash
cd ui
npm install
cp .env.local.example .env.local
npm run dev
```

Before publishing UI code, run the checks yourself:

```bash
cd ui
npm ci
npm run typecheck
npm run build

cd deploy/terraform
terraform fmt -check
terraform init -backend=false -input=false
terraform validate

cd ../../..
bash -n ui/deploy/deploy.sh
./ui/deploy/deploy.sh --help
```

This repository intentionally does **not** use GitHub Actions for deployment or verification.

---

# Terraform state and the stable `NAME_SUFFIX`

Cloud Shell does not need to preserve a local `.tfstate` file. Terraform state is remote and normally lives at:

```text
terraform/regdocs-atlas.tfstate
```

inside the existing Blob container configured by `STORAGE_ACCOUNT` and `BLOB_CONTAINER`.

Every deployment mode reconnects to that Blob with `terraform init -reconfigure`.

`NAME_SUFFIX` is a **stable installation identifier**, not a release/version number. Pick it once and reuse it for every update with the same remote Terraform state.

Do not create a new suffix just because Cloud Shell restarted or because a deployment is being updated.

## Verify the state before considering imports

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

If the Blob exists, **do not import the Azure resources**. Reuse the remote state.

If the Blob is missing but the Azure resources still exist, stop before `terraform apply`. That is a state-recovery/import situation and the existing Azure resource IDs should be imported deliberately.

## Accidental-delete protection

Terraform has `prevent_destroy` on the three globally named resources most painful to recreate:

```text
Azure Container Registry
Azure AI Search
Microsoft Foundry account
```

A Terraform plan that would destroy or replace one of those resources fails instead of deleting it.

This also protects the stable names behind `NAME_SUFFIX`. Foundry is soft-deleted by Azure after removal, so accidental deletion can temporarily prevent immediate recreation with the same name.

An intentional teardown requires an explicit code change to remove the relevant `prevent_destroy` lifecycle first. Do not work around the guard by inventing a new `NAME_SUFFIX`.

---

# Cloud Shell setup

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

The production-safe embedding setting is:

```text
EMBEDDING_BATCH_SIZE="32"
```

The Terraform default and the cloud-indexer fallback are also 32. Larger values should not be reintroduced without testing because the larger request batches previously caused indexing failures.

Before a deployment, update the checkout and export a valid container SAS:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master
source ui/deploy/config.env

read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
```

The SAS is used for the remote Terraform backend. Full deployments also use it to verify normalized index inputs. Keep it out of Git and out of `config.env`.

Read-only `--status` and `--error` operations do **not** require the Storage SAS token.

---

# Deploy or update only the UI

For a normal UI/API release:

```bash
./ui/deploy/deploy.sh --ui-only
```

This mode:

- reconnects to remote Terraform state;
- reconciles infrastructure/RBAC/environment variables;
- determines the currently deployed indexer image;
- builds only `regdocs-ui:<git-sha>`;
- deploys the UI through Terraform;
- preserves the indexer image tag in Terraform state;
- never builds the indexer;
- never starts the indexing job.

If ACR is still building the UI image, the script exits safely. Run the same command again after the build completes:

```bash
./ui/deploy/deploy.sh --ui-only
```

This is the normal update command for the Atlas web application.

---

# Infrastructure-only update

Use this when Terraform, RBAC, identities, Foundry/Search settings, or Container App environment values changed but neither application image should move:

```bash
./ui/deploy/deploy.sh --infra-only
```

This mode builds no images and starts no indexing execution.

---

# Full deployment and indexing

For a first deployment or an indexer-code change:

```bash
./ui/deploy/deploy.sh
```

The script builds the UI and indexer images with the current Git SHA. A full deployment starts indexing when the deployed indexer image changed unless an execution is already active.

When the normalized corpus changed and you explicitly need a fresh publication even if the indexer code did not change:

```bash
./ui/deploy/deploy.sh --restart-index
```

Do not use `--restart-index` for an ordinary UI release.

ACR builds and Container Apps indexing continue in Azure if Cloud Shell disconnects. Re-run the same deployment command to resume/check the deployment.

---

# Check indexing status and Log Analytics

The easiest operator command is:

```bash
./ui/deploy/deploy.sh --status
```

This is read-only and does not require a Storage SAS token. It shows:

```text
current UI URL
current UI image
current indexer image
recent indexer job executions
latest execution status
recent logs from the latest execution in Log Analytics
```

The status command queries `ContainerAppConsoleLogs_CL` using the latest Container Apps job execution name.

If logs are empty immediately after a job starts, wait a few minutes for Log Analytics ingestion and run the same command again.

## Direct Log Analytics query for the latest index execution

```bash
source ui/deploy/config.env
az account set --subscription "$SUBSCRIPTION_ID"

WORKSPACE_ID="$(az monitor log-analytics workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "log-regdocs-${NAME_SUFFIX}" \
  --query customerId \
  --output tsv)"

JOB_NAME="job-regdocs-${NAME_SUFFIX}"
LATEST_EXECUTION="$(az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query 'sort_by(@, &properties.startTime)[-1].name' \
  --output tsv)"

az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(48h) | where ContainerGroupName_s startswith '$LATEST_EXECUTION' | project Time=TimeGenerated, Message=Log_s | order by Time asc | take 200" \
  --output table
```

---

# Diagnostics: is Search / Foundry actually working?

Open:

```text
https://<atlas-host>/diagnostics
```

The initial page shows inexpensive configuration checks. Choose **Run live checks** to perform actual server-side calls to:

```text
Azure AI Search keyword retrieval
hybrid/vector retrieval
semantic ranking when configured
HTML document-view retrieval
Microsoft Foundry grounded inference
Stage 6 entities index
Stage 6 relations index
Stage 6 events index
```

These are operator-initiated diagnostics. They are not an automated smoke-test suite.

Ask also emits structured telemetry without question text, including retrieval mode, hybrid fallback, evidence count, whether Foundry was used, Foundry model/deployment, citation validation, retries, Search time, Foundry time, and total time.

---

# User-visible errors and Log Analytics

Real server faults from Ask, Search, document view, evidence lookup, timeline, and relationship graph receive a random reference such as:

```text
ATLAS-0123ABCD4567EF89
```

The same ID is written as a structured `atlas.error` event to Container Apps console output and flows to Log Analytics.

The quickest operator lookup is:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

This is read-only and does not require a Storage SAS token.

You can also open:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Retrieve the operator token after Terraform has been initialized against production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

The token is sensitive. Do not put it in client-side variables, screenshots, tickets, or Git.

## Direct KQL error query

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

Normal validation errors, no-result responses, 404s, and rate limits are not treated as server faults. Ask question text is not added to structured error or Ask telemetry events.

---

# Public URL

REGDOCS Atlas runs on **Azure Container Apps**, not Azure App Service. The Azure-provided hostname therefore follows the Container Apps pattern and is not an `azurewebsites.net` address.

Get the current public URL with:

```bash
./ui/deploy/deploy.sh --status
```

The generated address looks roughly like:

```text
https://app-regdocs-<suffix>.<environment-id>.<region>.azurecontainerapps.io
```

Azure controls the `<environment-id>.<region>.azurecontainerapps.io` suffix, so the platform-provided hostname cannot be reduced to exactly `regdocsatlas.azurecontainerapps.io`.

If you want a genuinely simple address, use a custom domain that you own, for example:

```text
https://regdocsatlas.example.com
```

Container Apps supports custom domains and managed TLS certificates. For a subdomain, point a CNAME directly at the generated Container Apps FQDN and add the required validation TXT record before binding the hostname.

The existing Container App name is intentionally left unchanged during normal updates because renaming it would replace the app. A custom domain gives a stable friendly URL without replacing the workload.

---

# Grounded Ask architecture

`POST /api/ask` retrieves CER evidence from Azure AI Search, then uses Microsoft Foundry to synthesize an answer from only those retrieved passages.

Conceptual questions favor hybrid retrieval; exact phrase or record-like questions can favor keyword search. Hybrid failures can fall back to keyword retrieval. Shelf/Workspace questions refetch exact chunk IDs from Search rather than trusting browser-supplied text.

Generated answer text is held until `[S#]` citations validate against the retrieved evidence. If Foundry generation or citation validation fails, retrieved sources remain available and real server faults receive an `ATLAS-...` error reference.

---

# HTML document viewer

The normal source preview uses normalized HTML rather than embedding the remote REGDOCS PDF. `GET /api/document-view` retrieves ordered indexed chunks around the selected page and reconstructs a readable page-like view with highlighting and evidence navigation.

The HTML view is optimized for search, accessibility, reading, and evidence collection; it does not claim pixel fidelity to the original.

---

# Operational files

```text
ui/README.md                  UI architecture, deployment, status, diagnostics, errors
ui/OPERATIONS.md              short operator command/runbook
ui/deploy/README.md           Cloud Shell/Terraform deployment details
ui/deploy/deploy.sh           deploy/update/status/error command entry point
ui/deploy/config.env          ignored local deployment configuration
ui/deploy/terraform/          Azure infrastructure and RBAC
```

For the Python pipeline, return to [`../README.md`](../README.md) and [`../SYNTAX.md`](../SYNTAX.md).
