# REGDOCS Atlas Azure deployment

REGDOCS Atlas runs on Azure Container Apps with Azure AI Search, Microsoft Foundry, Azure Container Registry, Log Analytics, managed identities, and Terraform. Azure Cloud Shell is only the control terminal; long-running builds and jobs continue in Azure after Cloud Shell disconnects.

The existing corpus Storage account is referenced for Blob inputs and Terraform state but is not owned or deleted by this Terraform deployment.

## Command reference

```bash
# Validate code and Terraform locally. No Azure calls; no SAS required.
./ui/deploy/deploy.sh --validate

# Preview Terraform changes against remote state. Nothing is applied.
./ui/deploy/deploy.sh --plan

# UI/API update only. Applies Terraform/RBAC/env changes, builds only the UI,
# preserves the publisher image, and starts no Stage 5/Stage 6 job.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. No image builds and no jobs.
./ui/deploy/deploy.sh --infra-only

# Full deployment. Builds/deploys UI + shared publisher image. Stage 5 starts
# when its publisher image changed; Stage 6 does not start implicitly.
./ui/deploy/deploy.sh

# Explicit Stage 5 hybrid-index publication.
./ui/deploy/deploy.sh --restart-index

# Explicit Stage 6 Microsoft Foundry extraction + intelligence publication.
./ui/deploy/deploy.sh --restart-intelligence

# Refresh both publication layers intentionally.
./ui/deploy/deploy.sh --restart-index --restart-intelligence

# Read-only URL/images plus Stage 5/Stage 6 execution history and recent logs.
./ui/deploy/deploy.sh --status

# Read-only lookup for a user-visible Atlas server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

`--full` is accepted as an explicit alias for the default full deployment. `--no-start` was removed because its behavior was ambiguous.

| Change | Command |
|---|---|
| UI/API only | `--ui-only` |
| UI plus Terraform/RBAC/env | `--ui-only` |
| Terraform/RBAC/env only | `--infra-only` |
| Publisher/indexer code | full deployment |
| Normalized corpus needs Stage 5 republish | `--restart-index` |
| Stage 6 model intelligence needs refresh | `--restart-intelligence` |
| Both publication layers need refresh | both restart switches |
| Inspect current system | `--status` |

## Preflight without GitHub Actions

This repository intentionally has no GitHub Actions workflow. Run this before deploying:

```bash
./ui/deploy/deploy.sh --validate
```

It runs:

```text
bash -n ui/deploy/deploy.sh
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
python -m compileall pipeline.py regdocs_atlas tools
npm ci
npm run typecheck
npm run build
```

No Azure resource is read or changed by `--validate`.

For Terraform changes, follow with:

```bash
source ui/deploy/config.env
read -rsp "Paste SAS: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"

./ui/deploy/deploy.sh --plan
```

`--plan` connects to the existing remote state and shows what Terraform would change. It does not apply, build images, or start jobs.

## Cloud Shell setup

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

Keep this value at 32 unless a tested change proves otherwise:

```text
EMBEDDING_BATCH_SIZE="32"
```

The script also caps an older local value above 32 down to 32 before Terraform receives it.

Before `--plan` or any deployment mode, export a private Blob container SAS with Read, Create, Write, and List permission:

```bash
source ui/deploy/config.env
read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
```

Do not save the SAS token in Git or `config.env`.

## Terraform state is remote

The normal state key is:

```text
terraform/regdocs-atlas.tfstate
```

in the configured existing Blob container. Every Terraform-backed command runs `terraform init -reconfigure` against that Blob.

A fresh Cloud Shell therefore does **not** require Terraform imports if the state Blob still exists.

Verify it before considering imports:

```bash
az storage blob show \
  --account-name "$STORAGE_ACCOUNT" \
  --container-name "$BLOB_CONTAINER" \
  --name "${STATE_BLOB:-terraform/regdocs-atlas.tfstate}" \
  --sas-token "$AZURE_STORAGE_SAS_TOKEN" \
  --query '{name:name,size:properties.contentLength,lastModified:properties.lastModified}' \
  -o table
```

If the Blob exists, reuse it. If the state Blob is missing while the Azure resources still exist, stop before `terraform apply`; that is a deliberate state-recovery/import operation.

`NAME_SUFFIX` is a stable installation ID, not a release number. Keep using the same suffix with the same remote state.

Terraform also uses `lifecycle.prevent_destroy` on the globally named ACR, Search, and Foundry resources. A plan that would delete or replace one of them fails instead of silently destroying it.

## UI-only updates

For an ordinary UI/API change:

```bash
./ui/deploy/deploy.sh --ui-only
```

This reconnects to remote state, applies Terraform/RBAC/environment changes, builds only `regdocs-ui:<git-sha>`, and deploys it while preserving the existing shared publisher image. It does not start Stage 5 or Stage 6.

If the ACR build is still running, the command exits safely. Run the same command again when the build is complete.

## Full deployment

For first deployment or publisher/infrastructure changes:

```bash
./ui/deploy/deploy.sh
```

The full path builds:

```text
regdocs-ui:<git-sha>
regdocs-indexer:<git-sha>
```

The `regdocs-indexer` image is shared by two independent Container Apps jobs:

```text
job-regdocs-<suffix>                Stage 5 hybrid search publication
job-regdocs-intelligence-<suffix>   Stage 6 Foundry intelligence publication
```

The jobs have separate execution histories. A Stage 6 failure therefore does not make a successful Stage 5 publication look failed.

## Stage 5 indexing

Force a new hybrid-index publication when normalized corpus inputs changed:

```bash
./ui/deploy/deploy.sh --restart-index
```

The Stage 5 job downloads normalized chunks/provenance, restores the Blob-backed embedding cache, generates missing Foundry embeddings, and publishes Azure AI Search chunks.

The embedding cache is periodically checkpointed to:

```text
workspace/5_index/embedding-cache.sqlite
```

## Stage 6 Microsoft Foundry intelligence

Start the model intelligence job explicitly:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

This is deliberately explicit because it can make many Foundry model calls.

The job:

1. downloads `workspace/4_normalize/documents.jsonl` and `chunks.jsonl`;
2. restores the durable Stage 6 extraction cache if present;
3. runs evidence-constrained Microsoft Foundry extraction;
4. validates that every model record cites real input chunk IDs;
5. merges model artifacts with deterministic Stage 6 metadata;
6. publishes entities, relations, events, claims, and obligations;
7. uploads Stage 6 outputs and its extraction cache back to Blob.

Published Search indexes are:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

The model extraction cache is checkpointed every 15 minutes to:

```text
workspace/6_enrich/model/extraction.sqlite
```

If the job times out or restarts, completed requests are reused.

### Run a small Stage 6 pilot first

In `config.env`:

```bash
INTELLIGENCE_DOCUMENT_LIMIT="10"
export TF_VAR_intelligence_document_limit="$INTELLIGENCE_DOCUMENT_LIMIT"
```

Then:

```bash
./ui/deploy/deploy.sh --infra-only
./ui/deploy/deploy.sh --restart-intelligence
./ui/deploy/deploy.sh --status
```

After reviewing the extracted claims/obligations and diagnostics, clear the limit:

```bash
INTELLIGENCE_DOCUMENT_LIMIT=""
export TF_VAR_intelligence_document_limit="$INTELLIGENCE_DOCUMENT_LIMIT"
```

Apply and run Stage 6 again. The first pilot's cached requests remain reusable.

## Check Stage 5 and Stage 6 status

```bash
./ui/deploy/deploy.sh --status
```

This is read-only and requires no SAS. It prints:

```text
UI URL and image
Stage 5 publisher image
Stage 6 publisher image
recent Stage 5 executions
recent Log Analytics output for the latest Stage 5 execution
recent Stage 6 executions
recent Log Analytics output for the latest Stage 6 execution
```

Container Apps console logs are queried from `ContainerAppConsoleLogs_CL`. Log Analytics ingestion can lag by a few minutes.

## Diagnostics and proof that Foundry is being used

Open:

```text
https://<atlas-host>/diagnostics
```

The shallow configuration page is inexpensive. Live checks require the diagnostics operator token because they make real Search/Foundry calls and expose operational failure details.

Get the token:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Enter it on `/diagnostics` and choose **Run live checks**.

The live diagnostics verify:

```text
live corpus metadata
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

A successful Ask answer also includes an expandable line showing the Foundry deployment, retrieval mode/fallback, semantic use, retrieved evidence count, cited evidence count, retries, timings, and current corpus coverage.

## User-visible errors

A real server failure is returned with a reference such as:

```text
Reference: ATLAS-0123ABCD4567EF89
```

Look it up directly:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Or use:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

The web lookup requires the same diagnostics operator token.

Direct KQL:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

## Public URL

Azure Container Apps generates a hostname similar to:

```text
https://app-regdocs-<suffix>.<environment-id>.<region>.azurecontainerapps.io
```

Print the current one with:

```bash
./ui/deploy/deploy.sh --status
```

For a genuinely short stable hostname such as `regdocsatlas.example.com`, bind a custom domain you own to the Container App. `azurewebsites.net` is an App Service hostname and is not the generated hostname format for Container Apps.
