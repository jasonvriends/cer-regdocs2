# Resumable Azure deployment from Cloud Shell

This deployment is designed for disposable Azure Cloud Shell sessions. Cloud Shell is only the control terminal: Terraform state is remote, Azure Container Registry builds images in Azure, and the Container Apps indexing job continues after Cloud Shell disconnects.

The production stack contains Azure AI Search, Microsoft Foundry, Azure Container Registry, Azure Container Apps, Log Analytics, managed identities, and RBAC. The existing corpus Storage account and Blob container remain outside Terraform ownership.

---

# Deployment command syntax

Use one of these four commands:

```bash
# UI/API release. Applies Terraform changes, builds/deploys only the UI,
# preserves the indexer image, and never starts indexing.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. Builds no images and never starts indexing.
./ui/deploy/deploy.sh --infra-only

# Full deployment. Builds/deploys UI + indexer and starts indexing when needed.
./ui/deploy/deploy.sh

# Full deployment plus a forced new indexing execution.
./ui/deploy/deploy.sh --restart-index
```

`--full` is accepted as an explicit alias for the default full mode:

```bash
./ui/deploy/deploy.sh --full
```

`--no-start` has been removed. It was ambiguous because a changed indexer image could still trigger a run. Use `--ui-only` or `--infra-only` instead.

## Which mode should I use?

| Change | Command |
|---|---|
| Next.js UI/API code | `./ui/deploy/deploy.sh --ui-only` |
| UI code plus Terraform/RBAC/env changes | `./ui/deploy/deploy.sh --ui-only` |
| Terraform/RBAC/env only | `./ui/deploy/deploy.sh --infra-only` |
| Search/Foundry infrastructure plus workloads | `./ui/deploy/deploy.sh` |
| Indexer code | `./ui/deploy/deploy.sh` |
| Normalized corpus must be republished | `./ui/deploy/deploy.sh --restart-index` |
| First deployment | `./ui/deploy/deploy.sh` |

The UI and indexer have separate Terraform image tags. `--ui-only` advances only the UI tag and keeps the deployed indexer tag unchanged.

---

# Terraform state is remote

The default Terraform state key is:

```text
terraform/regdocs-atlas.tfstate
```

in the existing Blob container configured by `STORAGE_ACCOUNT`, `BLOB_CONTAINER`, and `STATE_BLOB`.

Every deployment mode runs `terraform init -reconfigure` against that Blob. Losing Cloud Shell local files does **not** mean the Terraform state is lost.

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

If the state Blob is missing but the Azure resources still exist, stop before `terraform apply`; rebuild state deliberately from the existing Azure resource IDs.

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

Before each deployment, update the checkout and export a private container SAS with Read, Create, Write, and List permission:

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

---

# UI-only deployment

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

This is the correct mode for ordinary UI/API changes and for UI releases that also add Terraform-managed RBAC or environment values.

If ACR is still building the UI image, the script exits safely. Run the same command again after the build completes.

---

# Infrastructure-only deployment

```bash
./ui/deploy/deploy.sh --infra-only
```

This reconciles Terraform/RBAC/configuration with the currently deployed UI and indexer tags. It builds no images and starts no indexing execution.

Use it when infrastructure changes but neither application image should move.

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

When the corpus changed and you intentionally want a new index publication even if the indexer image itself did not change:

```bash
./ui/deploy/deploy.sh --restart-index
```

Do not use `--restart-index` for an ordinary UI release.

---

# Diagnostics and errors

The UI exposes:

```text
/diagnostics
```

for configuration and operator-initiated live checks against Search, Foundry, the document reader, and Stage 6 indexes.

Server faults shown to users include a reference such as:

```text
Reference: ATLAS-0123ABCD4567EF89
```

The same ID is written to Container Apps console output and Log Analytics.

Operator lookup page:

```text
/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Retrieve the operator token after Terraform is initialized against production state:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

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

# Data ownership and operational notes

Terraform manages the REGDOCS Atlas resource group and application/search/AI infrastructure. The existing Storage account is referenced for input/state but is not imported into Terraform ownership.

The indexing job reads:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
workspace/5_index/embedding-cache.sqlite    optional/resumable cache
```

Operational reminders:

- Standard Azure AI Search is the main fixed monthly cost.
- The UI Container App can scale to zero when idle.
- The indexing job costs only while executions run.
- ACR, Log Analytics, and Foundry add usage-dependent charges.
- Keep `config.env`, SAS tokens, Terraform state, and the diagnostics operator token private.
- A SAS or managed identity does not bypass Storage firewall/network restrictions.
- `terraform destroy` removes Terraform-managed Atlas resources but does not delete the retained source Storage account or corpus blobs.
