# Resumable Azure deployment from Cloud Shell

This deployment is designed for disposable Azure Cloud Shell sessions. Cloud Shell is only the control terminal: Terraform state is remote, Azure Container Registry builds images in Azure, and the Container Apps indexing job continues after Cloud Shell disconnects.

The production stack contains:

- Azure AI Search;
- Microsoft Foundry resource/project plus embedding and chat deployments;
- Azure Container Registry;
- Azure Container Apps environment;
- public Next.js UI Container App;
- resumable/manual indexing Container Apps job;
- Log Analytics;
- user-assigned managed identities and RBAC.

The existing corpus Storage account and Blob container stay outside Terraform ownership.

---

# Terraform state is remote

The default Terraform state key is:

```text
terraform/regdocs-atlas.tfstate
```

in the existing Blob container configured by:

```text
STORAGE_ACCOUNT
BLOB_CONTAINER
STATE_BLOB
```

`deploy.sh` runs `terraform init -reconfigure` against that remote Blob on every deployment. Losing Cloud Shell local files does **not** mean the Terraform state is lost.

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

If the state Blob is missing but the Azure resources still exist, stop before `terraform apply`; rebuild the state with deliberate imports first.

---

# One-time Cloud Shell setup

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

Optional UI ingress restriction:

```bash
UI_ALLOWED_IP_CIDRS='["203.0.113.10/32","198.51.100.0/24"]'
```

Use the public egress address seen by Azure. The same allowlist protects the UI and its server-side `/api/*` routes.

Generate or reuse a private container SAS with Read, Create, Write, and List permission. Keep it out of Git and out of `config.env`.

```bash
read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
export AZURE_STORAGE_SAS_TOKEN
```

---

# Full deployment

Use the full deployment when Terraform/RBAC/environment variables/Search/Foundry/index inputs changed or when provisioning the environment for the first time.

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master
./ui/deploy/deploy.sh
```

The script:

1. verifies normalized Blob inputs;
2. reconnects Terraform to the remote state Blob;
3. reconciles Azure infrastructure;
4. builds missing images in ACR using the current Git commit as the immutable image tag;
5. deploys the UI and indexing job after images exist;
6. avoids duplicate indexing executions while one is already active;
7. reuses a successful indexing execution unless a new indexer image or explicit restart requires another run.

If ACR builds were queued, the script may exit intentionally. Rerun the same command after the builds complete:

```bash
./ui/deploy/deploy.sh
```

Cloud Shell may be closed while ACR builds or the Container Apps indexing job continues.

## Explicitly republish changed normalized data

```bash
./ui/deploy/deploy.sh --restart-index
```

## Provision without starting an otherwise-needed index job

```bash
./ui/deploy/deploy.sh --no-start
```

### Important image-tag behavior

The full script tags **both** UI and indexer images with the current Git commit. If the indexer image tag changes, deployment logic can start another indexing execution even when the source data itself did not change.

For a UI-code-only release, use the UI-only procedure below instead.

---

# Deploy only the UI

Use this when Terraform, RBAC, Search, Foundry, and index data are already correct and only Next.js/UI/API code changed.

This path does not require the Blob SAS and does not touch the indexing job.

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

Check the revision:

```bash
az containerapp revision list \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[].{Revision:name,Active:properties.active,Traffic:properties.trafficWeight,Created:properties.createdTime}" \
  -o table
```

Get the URL:

```bash
UI_HOST="$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn \
  -o tsv)"

echo "https://$UI_HOST"
```

## First deployment of an infrastructure-aware UI feature

If a UI release also adds Terraform resources, roles, environment variables, or secrets, run the **full deployment once first**. The UI-only command updates only the image and cannot apply Terraform changes.

The `ATLAS-...` Log Analytics error lookup is one such feature: its first deployment needs the Terraform changes that grant Log Analytics read access and inject the workspace/token configuration.

---

# Diagnostics and errors

The UI exposes:

```text
/diagnostics
```

for configuration checks and explicit live checks against Search, Foundry, the document reader, and Stage 6 intelligence indexes.

Server faults shown to users include a reference such as:

```text
Reference: ATLAS-0123ABCD4567EF89
```

The same ID is written to Container Apps console output and therefore to the deployment's Log Analytics workspace.

Operator lookup page:

```text
/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Retrieve the operator token after Terraform has been initialized against the production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

The token is a sensitive Terraform output and must not be exposed in browser environment variables or Git.

Direct Log Analytics query:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

See [`../OPERATIONS.md`](../OPERATIONS.md) for the operator workflow and [`../README.md`](../README.md) for full UI behavior and architecture.

---

# Deployment resources and data ownership

Terraform creates/manages the REGDOCS Atlas resource group and the application/search/AI infrastructure inside it.

The existing Storage account is referenced but not imported into Terraform ownership. `terraform destroy` therefore removes Terraform-managed Atlas resources but does not delete the retained source Storage account or corpus blobs.

The indexing job reads:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
workspace/5_index/embedding-cache.sqlite    optional/resumable cache
```

The SQLite pipeline ledger remains useful for the local acquisition pipeline but is not required by the production UI or Search indexing job.

---

# Operational notes

- Standard Azure AI Search is the main fixed monthly cost.
- The UI Container App can scale to zero when idle.
- The indexing job costs only while executions run.
- ACR and Log Analytics add smaller ongoing charges.
- Foundry embedding/chat charges depend on processed tokens/requests.
- The UI can be available before indexing is complete; search completeness depends on the latest successful publication.
- Keep `config.env`, SAS tokens, Terraform state, and the diagnostics operator token private.
- A SAS or managed identity does not bypass Storage firewall/network restrictions.
- Terraform state includes sensitive configuration and must remain private.
