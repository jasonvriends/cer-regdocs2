# Resumable Azure deployment from Cloud Shell

This deployment is designed for disposable Azure Cloud Shell sessions. Cloud Shell is only the control terminal: Terraform state is remote, Azure Container Registry builds images in Azure, and the Container Apps indexing job continues after Cloud Shell disconnects.

The production stack contains Azure AI Search, Microsoft Foundry, Azure Container Registry, Azure Container Apps, Log Analytics, managed identities, and RBAC. The existing corpus Storage account and Blob container remain outside Terraform ownership.

---

# Command syntax

```bash
# UI/API release. Applies Terraform changes, builds/deploys only the UI,
# preserves the indexer image, and never starts indexing.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. No image builds and no indexing.
./ui/deploy/deploy.sh --infra-only

# Full deployment. Builds/deploys UI + indexer and starts indexing when needed.
./ui/deploy/deploy.sh

# Full deployment plus a forced new indexing execution.
./ui/deploy/deploy.sh --restart-index

# Read-only current URL/images/indexing executions plus latest indexer logs.
./ui/deploy/deploy.sh --status

# Read-only Log Analytics lookup for one user-visible server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

`--full` is accepted as an explicit alias for the default full deployment.

`--no-start` has been removed. It was ambiguous because a changed indexer image could still trigger indexing. Use `--ui-only` or `--infra-only` instead.

| Change | Command |
|---|---|
| Next.js UI/API | `./ui/deploy/deploy.sh --ui-only` |
| UI + Terraform/RBAC/env | `./ui/deploy/deploy.sh --ui-only` |
| Terraform/RBAC/env only | `./ui/deploy/deploy.sh --infra-only` |
| Search/Foundry/indexer infrastructure | `./ui/deploy/deploy.sh` |
| Indexer code | `./ui/deploy/deploy.sh` |
| Republish normalized corpus | `./ui/deploy/deploy.sh --restart-index` |
| First deployment | `./ui/deploy/deploy.sh` |
| Inspect deployment/indexing | `./ui/deploy/deploy.sh --status` |
| Trace server error | `./ui/deploy/deploy.sh --error ATLAS-...` |

The UI and indexer have separate Terraform image tags. `--ui-only` changes only the UI tag and cannot accidentally turn a UI commit into an indexer update.

---

# Remote Terraform state and stable resource names

The default Terraform state key is:

```text
terraform/regdocs-atlas.tfstate
```

in the existing Blob container configured by `STORAGE_ACCOUNT`, `BLOB_CONTAINER`, and `STATE_BLOB`.

Every deployment mode runs `terraform init -reconfigure` against that Blob. Losing Cloud Shell local files does **not** mean Terraform state is lost.

`NAME_SUFFIX` is a stable installation identifier. Pick it once and keep using it with the same remote state. It is not a release number and should not change for normal updates.

## Verify state before importing anything

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

If the Blob exists, reuse it. **Do not import the Azure resources.**

If the state Blob is missing but Azure resources still exist, stop before `terraform apply`; rebuild the state deliberately from the existing Azure resource IDs.

## Accidental-delete protection

Terraform uses `lifecycle.prevent_destroy` on:

```text
Azure Container Registry
Azure AI Search
Microsoft Foundry account
```

A Terraform plan that would delete or replace one of those globally named resources fails instead.

This is intentional. Foundry deletions are soft-deleted by Azure and the same name can be unavailable until the resource is recovered/purged or the retention window passes.

For an intentional teardown, first make a deliberate code change removing the relevant `prevent_destroy` lifecycle. Do not work around the protection by choosing a new `NAME_SUFFIX`.

---

# Cloud Shell setup

```bash
git clone <repository-url> cer-regdocs2
cd cer-regdocs2
cp ui/deploy/config.env.example ui/deploy/config.env
code ui/deploy/config.env
```

At minimum set:

```text
SUBSCRIPTION_ID
NAME_SUFFIX
STORAGE_ACCOUNT
STORAGE_RESOURCE_GROUP
BLOB_CONTAINER
CONFIRM_BILLABLE_DEPLOYMENT=yes
```

The safe indexing value is:

```text
EMBEDDING_BATCH_SIZE="32"
```

Terraform also defaults to 32, and the cloud indexer falls back to 32 if the environment variable is absent.

Optional UI ingress restriction:

```bash
UI_ALLOWED_IP_CIDRS='["203.0.113.10/32","198.51.100.0/24"]'
```

Before a deployment, update the checkout and export a private container SAS with Read, Create, Write, and List permission:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master
source ui/deploy/config.env

read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
```

The SAS is used for the remote Terraform backend. Full deployments also use it to verify normalized index inputs. Keep it out of Git and `config.env`.

`--status` and `--error` are read-only and do not require the SAS token.

---

# Update only the UI

```bash
./ui/deploy/deploy.sh --ui-only
```

This mode:

1. reconnects to remote Terraform state;
2. reconciles Terraform/RBAC/environment variables;
3. reads the currently deployed indexer image tag;
4. builds only `regdocs-ui:<git-sha>`;
5. deploys that UI image through Terraform;
6. preserves the indexer image tag;
7. never builds or starts the indexing job.

If ACR is still building the UI image, the script exits safely. Run the same command again after the build completes.

---

# Update only infrastructure/config

```bash
./ui/deploy/deploy.sh --infra-only
```

This reconciles Terraform/RBAC/configuration while preserving the deployed UI and indexer images. It builds no images and starts no indexing execution.

---

# Full deployment

```bash
./ui/deploy/deploy.sh
```

The full mode:

1. verifies normalized Blob inputs;
2. reconnects to remote Terraform state;
3. reconciles Azure infrastructure;
4. builds missing UI and indexer images using the current Git SHA;
5. deploys both images;
6. avoids duplicate indexing while an execution is active;
7. starts a new indexing execution when the deployed indexer image changed.

If ACR builds are queued, the script may exit intentionally. Run the same command again after the builds finish.

Cloud Shell may be closed while ACR builds or the Container Apps indexing job continues.

## Explicitly republish normalized data

```bash
./ui/deploy/deploy.sh --restart-index
```

Use this when the normalized corpus changed and you intentionally want another publication even if the indexer code did not change.

Do not use it for an ordinary UI release.

---

# Check indexing status through Log Analytics

Run:

```bash
./ui/deploy/deploy.sh --status
```

This is read-only and shows:

```text
current UI URL
current UI image
current indexer image
recent Container Apps job executions
latest execution status
recent Log Analytics output for the latest execution
```

The Log Analytics query uses `ContainerAppConsoleLogs_CL` and scopes logs to the latest job execution through `ContainerGroupName_s`.

If a new execution has no logs yet, Log Analytics ingestion may still be catching up. Run `--status` again after a few minutes.

## Direct CLI query

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

# Diagnostics and Foundry verification

Open:

```text
https://<atlas-host>/diagnostics
```

The page can perform operator-initiated live checks against Search keyword retrieval, hybrid/vector retrieval, optional semantic ranking, the HTML document reader, Microsoft Foundry grounded inference, and the Stage 6 entities/relations/events indexes.

This is the quickest way to prove the deployed app is actually reaching Foundry rather than merely having Foundry environment variables configured.

---

# User errors

Server faults shown to users include a reference such as:

```text
Reference: ATLAS-0123ABCD4567EF89
```

The same ID is written to Container Apps console output and Log Analytics.

Fast CLI lookup:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Operator web lookup:

```text
/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Retrieve the operator token after Terraform is initialized against production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Direct KQL:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

See [`../OPERATIONS.md`](../OPERATIONS.md) for the short operator workflow.

---

# Public URL

The workload runs on Azure Container Apps. Its generated Azure hostname has the form:

```text
https://app-regdocs-<suffix>.<environment-id>.<region>.azurecontainerapps.io
```

Use:

```bash
./ui/deploy/deploy.sh --status
```

to print the actual current URL.

The Azure-provided Container Apps hostname cannot be shortened to an App Service-style `regdocsatlas.azurewebsites.net`. If you want a simple stable hostname such as `regdocsatlas.example.com`, bind a custom domain you own to the Container App. The current workload name is intentionally kept stable because renaming the existing Container App would replace it.

---

# Manual verification

There are no GitHub Actions in this repository. Before merging/deploying infrastructure or UI changes, run locally or in Cloud Shell:

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

---

# Data ownership and operational notes

Terraform manages the REGDOCS Atlas resource group and application/search/AI infrastructure. The existing Storage account is referenced for input/state but is not imported into Terraform ownership.

The indexing job reads:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
workspace/5_index/embedding-cache.sqlite    optional/resumable cache
```

Operational reminders:

- `NAME_SUFFIX` should remain stable across normal updates.
- Standard Azure AI Search is the main fixed monthly cost.
- The UI Container App can scale to zero when idle.
- The indexing job costs only while executions run.
- ACR, Log Analytics, and Foundry add usage-dependent charges.
- Keep `config.env`, SAS tokens, Terraform state, and the diagnostics operator token private.
- A SAS or managed identity does not bypass Storage firewall/network restrictions.
- Terraform delete protection intentionally blocks accidental deletion of the globally named ACR, Search, and Foundry resources.
