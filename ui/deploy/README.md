# REGDOCS Atlas Azure deployment

REGDOCS Atlas runs on Azure Container Apps with Azure AI Search, Microsoft Foundry, Azure Container Registry, Log Analytics, managed identities, and Terraform.

Azure Cloud Shell is only the control terminal. Terraform state is remote, ACR builds run in Azure, and Container Apps jobs continue after Cloud Shell disconnects.

The existing corpus Storage account is referenced for Blob inputs and Terraform state but is not owned or deleted by this Terraform deployment.

## Start here

```bash
./ui/deploy/deploy.sh
```

With no arguments, the command is **read-only**. It walks through the deployment state and prints the next command.

REGDOCS Atlas has six pipeline stages; Stage 6 is the final data-processing stage. The deployment guide does not create additional stages.

For a first deployment, also see [`README-FIRST-DEPLOYMENT.md`](README-FIRST-DEPLOYMENT.md).

## Command reference

```bash
# Read-only next-step guide.
./ui/deploy/deploy.sh

# Verify all five Stage 4 files in Blob.
./ui/deploy/deploy.sh --check-data

# Validate Bash/Terraform/Python/TypeScript/Next.js. No Azure changes.
./ui/deploy/deploy.sh --validate

# Preview Terraform against remote state. Nothing is applied.
./ui/deploy/deploy.sh --plan

# UI/API update only. Starts no Stage 5/Stage 6 job.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. No image builds or jobs.
./ui/deploy/deploy.sh --infra-only

# Explicit full infrastructure/workload deployment.
./ui/deploy/deploy.sh --full

# Explicit Stage 5 hybrid-index publication.
./ui/deploy/deploy.sh --restart-index

# Explicit Stage 6 Microsoft Foundry extraction/intelligence publication.
./ui/deploy/deploy.sh --restart-intelligence

# Refresh both publication layers intentionally.
./ui/deploy/deploy.sh --restart-index --restart-intelligence

# Read-only URL/images, Stage 5/6 executions, and recent logs.
./ui/deploy/deploy.sh --status

# Read-only lookup for one user-visible Atlas server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

No-argument execution is not a full deployment. Use `--full` explicitly when a full deployment is intended.

`--no-start` was removed because its behavior was ambiguous.

## Which command should I use?

| Goal | Command |
|---|---|
| Ask the deployment guide what to do next | no arguments |
| Check uploaded normalized data | `--check-data` |
| Validate code/config without Azure changes | `--validate` |
| Review Terraform changes | `--plan` |
| Update only UI/API code | `--ui-only` |
| Apply only Terraform/RBAC/env configuration | `--infra-only` |
| Deploy/update the whole Azure workload definition | `--full` |
| Publish/re-publish the Search corpus | `--restart-index` |
| Run/re-run final Stage 6 intelligence | `--restart-intelligence` |
| See current URL/job execution/log state | `--status` |
| Investigate an `ATLAS-...` failure | `--error` |

## No GitHub Actions

This repository intentionally has no GitHub Actions workflow for deployment or verification.

Run before deployment:

```bash
./ui/deploy/deploy.sh --validate
```

It runs:

```text
bash syntax checks
terraform fmt -check
terraform init -backend=false
terraform validate
python compileall
npm ci
npm run typecheck
npm run build
```

No Azure resource is changed by `--validate`.

For infrastructure changes, follow with:

```bash
./ui/deploy/deploy.sh --plan
```

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

`NAME_SUFFIX` is a stable installation ID. Keep the same value for normal updates.

Keep the production embedding batch at:

```text
EMBEDDING_BATCH_SIZE="32"
```

The deployment caps an older local value above 32 down to 32 before Terraform receives it.

Before `--plan` or a Terraform-backed deployment action, export a private Blob container SAS with Read, Create, Write, and List permission:

```bash
source ui/deploy/config.env
read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
```

Do not save the SAS token in Git or `config.env`.

## Source package

The cloud publication flow uses exactly the canonical Stage 4 package:

```text
documents.jsonl
pages.jsonl
chunks.jsonl
tables.jsonl
provenance.jsonl
```

Upload it from the personal computer with `tools/upload_cloud_inputs.py` and verify it with:

```bash
./ui/deploy/deploy.sh --check-data
```

The production UI does not require a duplicate PDF, Markdown copy, local SQLite database, or whole-workspace upload.

See [`../DATA-CONTRACT.md`](../DATA-CONTRACT.md) for the feature-to-data contract.

## Terraform state is remote

The normal state key is:

```text
terraform/regdocs-atlas.tfstate
```

in the configured existing Blob container. Terraform-backed commands reconnect to that remote state.

A fresh Cloud Shell therefore does not require imports merely because local files disappeared.

If the state Blob is missing while existing Atlas Azure resources remain, stop before apply. That is a deliberate state-recovery/import operation.

Terraform uses `prevent_destroy` on the globally named ACR, Azure AI Search, and Foundry resources. Investigate a destructive/replacement plan instead of inventing a new suffix.

## UI-only update

```bash
./ui/deploy/deploy.sh --ui-only
```

This reconciles Terraform/RBAC/environment changes, builds only the UI image, and preserves the current publisher image. It does not start Stage 5 or Stage 6.

## Full deployment

For first deployment or publisher/infrastructure changes:

```bash
./ui/deploy/deploy.sh --full
```

The full path deploys:

```text
regdocs-ui:<git-sha>
regdocs-indexer:<git-sha>
```

The `regdocs-indexer` image is shared by two independent Container Apps jobs:

```text
job-regdocs-<suffix>                Stage 5 hybrid Search publication
job-regdocs-intelligence-<suffix>   Stage 6 Foundry intelligence publication
```

Stage 6 does not start implicitly merely because application code was deployed.

## Stage 5 Search publication

Stage 5 reads:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
```

It restores/checkpoints the embedding cache and publishes the Search corpus used by Ask and the HTML document viewer.

Run when publication is required:

```bash
./ui/deploy/deploy.sh --restart-index
```

The embedding cache is durable under:

```text
workspace/5_index/embedding-cache.sqlite
```

## Stage 6 — final data stage

Start the intelligence job explicitly:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

It:

1. downloads normalized documents/chunks;
2. restores the durable extraction cache;
3. runs evidence-constrained Microsoft Foundry extraction;
4. validates model evidence chunk IDs;
5. merges model output with deterministic regulatory metadata;
6. publishes entities, relations, events, claims, and obligations;
7. writes durable Stage 6 output/cache back to Blob.

The five indexes are:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

The extraction cache is stored at:

```text
workspace/6_enrich/model/extraction.sqlite
```

### Optional first pilot

In `config.env`:

```text
INTELLIGENCE_DOCUMENT_LIMIT="10"
```

Then reconcile and run Stage 6:

```bash
./ui/deploy/deploy.sh --infra-only
./ui/deploy/deploy.sh --restart-intelligence
./ui/deploy/deploy.sh --status
```

Review the extracted intelligence and source evidence. Clear the limit for the final full-corpus Stage 6 publication.

## Status and Log Analytics

```bash
./ui/deploy/deploy.sh --status
```

This read-only command requires no Storage SAS and prints:

```text
UI URL/image
Stage 5 publisher image
Stage 6 publisher image
recent Stage 5 executions/logs
recent Stage 6 executions/logs
```

Container Apps console output is queried from `ContainerAppConsoleLogs_CL`. Log Analytics ingestion can lag by a few minutes.

## Diagnostics and Foundry proof

Open:

```text
https://<atlas-host>/diagnostics
```

Protected live checks require the diagnostics operator token:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Live diagnostics exercise:

```text
live corpus metadata
keyword Search
hybrid/vector Search
semantic ranking when configured
HTML document retrieval
real Microsoft Foundry grounded inference
entities
relations
events
claims
obligations
```

A successful Ask answer also exposes Foundry deployment, retrieval/fallback, semantic use, evidence/citation counts, retries, timings, and current corpus coverage.

## User-visible errors

A real server failure includes a reference such as:

```text
Reference: ATLAS-0123ABCD4567EF89
```

Look it up:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Or use the protected web page:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
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

For a short stable hostname, bind a custom domain you own. `azurewebsites.net` is an App Service hostname family, not the generated Container Apps hostname.

## Finished deployment

When Stage 5 and Stage 6 both report `Succeeded`, run protected diagnostics and complete [`../../COMPLETION.md`](../../COMPLETION.md). There is no further pipeline stage after Stage 6.
