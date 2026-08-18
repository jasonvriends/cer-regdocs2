# REGDOCS Atlas operations

This file is the short operator runbook. For the full deployment guide, see [`README.md`](README.md) and [`deploy/README.md`](deploy/README.md).

## Quick command reference

```bash
# Normal UI/API update. Applies Terraform changes too, but never starts indexing.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only. No image builds and no indexing.
./ui/deploy/deploy.sh --infra-only

# Full UI + indexer deployment.
./ui/deploy/deploy.sh

# Full deployment and explicitly start a fresh index publication.
./ui/deploy/deploy.sh --restart-index

# Read-only deployment/indexing status plus latest indexer Log Analytics output.
./ui/deploy/deploy.sh --status

# Read-only server error lookup in Log Analytics.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

`--no-start` is intentionally not supported. Use `--ui-only` or `--infra-only`, which have precise no-indexing semantics.

## Check indexing status

Run:

```bash
./ui/deploy/deploy.sh --status
```

This requires Azure CLI access but **does not require a Storage SAS token**. It shows:

- the current Atlas URL;
- deployed UI image;
- deployed indexer image;
- recent Container Apps job executions and their status;
- recent console output for the latest indexing execution from Log Analytics.

The indexer logs come from `ContainerAppConsoleLogs_CL` and are scoped to the latest Container Apps job execution. Log Analytics ingestion can lag by a few minutes.

For a direct Azure CLI query, first get the workspace customer ID:

```bash
WORKSPACE_ID="$(az monitor log-analytics workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "log-regdocs-${NAME_SUFFIX}" \
  --query customerId \
  --output tsv)"
```

Then get the latest execution name:

```bash
JOB_NAME="job-regdocs-${NAME_SUFFIX}"
LATEST_EXECUTION="$(az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query 'sort_by(@, &properties.startTime)[-1].name' \
  --output tsv)"
```

Then query its logs:

```bash
az monitor log-analytics query \
  --workspace "$WORKSPACE_ID" \
  --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(48h) | where ContainerGroupName_s startswith '$LATEST_EXECUTION' | project Time=TimeGenerated, Message=Log_s | order by Time asc | take 200" \
  --output table
```

## Trace a user-visible server error

Atlas server failures return a short reference such as:

```text
ATLAS-0123ABCD4567EF89
```

The same reference is written as a structured `atlas.error` event to the Container Apps console log and flows to Log Analytics.

The fastest command-line lookup is:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

This is read-only and does not require the Storage SAS token.

### Operator lookup page

You can also open:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

The page asks for the diagnostics operator token. Retrieve it from the same remote Terraform state used by `deploy.sh`:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

The token is marked sensitive. The lookup API uses the UI managed identity with `Log Analytics Reader`; Azure credentials are not sent to the browser.

Validation errors, normal 404s, no-result responses, and rate limits are not logged as application faults and do not receive `ATLAS-...` references.

### Direct KQL error query

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

Ask question text is intentionally not added to structured error or Ask telemetry events.

## Live service diagnostics

Open:

```text
https://<atlas-host>/diagnostics
```

Choose **Run live checks** to verify Azure AI Search, hybrid/semantic retrieval, the HTML document-view backend, Microsoft Foundry grounded inference, and the Stage 6 intelligence indexes.

## Stable deployment identity and delete protection

`NAME_SUFFIX` is a stable installation identifier, not a release number. Pick it once and keep using it with the same remote Terraform state.

Terraform has `prevent_destroy` protection on the globally named ACR, Azure AI Search, and Microsoft Foundry resources. An accidental `terraform destroy` or replacement plan that would remove one of those resources fails instead of silently deleting it.

An intentional teardown therefore requires a deliberate code change to remove the relevant `prevent_destroy` lifecycle before deletion. Do not work around a failed destroy by inventing a new `NAME_SUFFIX`.

## Indexing batch size

The production embedding batch size is:

```text
EMBEDDING_BATCH_SIZE="32"
```

The Terraform default and cloud-indexer fallback are also 32. Larger batches should not be reintroduced without testing because they previously caused indexing failures.
