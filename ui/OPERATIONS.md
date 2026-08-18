# REGDOCS Atlas operations

This is the short operator runbook. See [`deploy/README.md`](deploy/README.md) for the full Azure deployment guide.

## Normal operator commands

```bash
# Preflight: no Azure calls.
./ui/deploy/deploy.sh --validate

# Preview Terraform against the durable remote state.
./ui/deploy/deploy.sh --plan

# UI/API update only; no Stage 5 or Stage 6 execution.
./ui/deploy/deploy.sh --ui-only

# Terraform/RBAC/config only; no image build or publication job.
./ui/deploy/deploy.sh --infra-only

# Full deployment of UI + shared publisher image/jobs.
./ui/deploy/deploy.sh

# Explicit Stage 5 hybrid-index publication.
./ui/deploy/deploy.sh --restart-index

# Explicit Stage 6 Microsoft Foundry extraction/publication.
./ui/deploy/deploy.sh --restart-intelligence

# Refresh both publication layers.
./ui/deploy/deploy.sh --restart-index --restart-intelligence

# Read-only live status and latest logs for both jobs.
./ui/deploy/deploy.sh --status

# Read-only lookup for one user-visible server error.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

`--no-start` is intentionally unsupported. Use `--ui-only` or `--infra-only`, which have precise no-publication semantics.

## Cloud Shell is disposable

Terraform state is stored in Blob, ACR builds run in Azure, and Container Apps job executions continue in Azure if Cloud Shell disconnects.

After reconnecting:

```bash
cd ~/cer-regdocs2
source ui/deploy/config.env
./ui/deploy/deploy.sh --status
```

If a deployment must continue, export a fresh SAS token and rerun the same deployment command. Do not create a new `NAME_SUFFIX`, import resources, or destroy the deployment merely because Cloud Shell disappeared.

## Stage 5 versus Stage 6

Atlas has two independent publication jobs using the same immutable publisher image:

```text
job-regdocs-<suffix>                Stage 5 hybrid Search chunks + embeddings
job-regdocs-intelligence-<suffix>   Stage 6 Foundry regulatory intelligence
```

Stage 5 handles searchable document chunks. Stage 6 handles evidence-backed entities, relations, events, claims, and obligations.

Stage 6 does **not** start automatically on an ordinary deployment because a corpus-wide Foundry extraction can incur meaningful model cost. Start it intentionally with `--restart-intelligence`.

The Stage 6 extraction cache is checkpointed to Blob every 15 minutes:

```text
workspace/6_enrich/model/extraction.sqlite
```

A restarted job reuses completed requests whose normalized input, model, and prompt version have not changed.

### Small Stage 6 pilot

Set in `config.env`:

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

Review `/diagnostics`, Findings & claims, and Commitments & obligations. Then clear the limit, apply infrastructure again, and rerun Stage 6 for the complete corpus.

## Check publication status and logs

```bash
./ui/deploy/deploy.sh --status
```

No Storage SAS is required. It shows the current Atlas URL/images, recent Stage 5 executions/logs, and recent Stage 6 executions/logs.

Console output comes from Log Analytics table:

```text
ContainerAppConsoleLogs_CL
```

Log Analytics ingestion can lag by a few minutes.

## Prove Foundry is actually being used

Open:

```text
https://<atlas-host>/diagnostics
```

Retrieve the operator token:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

Enter it on `/diagnostics` and run the live checks. They make real calls to Search and Microsoft Foundry and verify the five intelligence indexes:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

Successful Ask answers also expose an expandable run footer showing Foundry deployment, retrieval mode/fallback, semantic use, evidence/citation counts, retries, timings, and current corpus coverage.

## Trace a user-visible server error

A real server failure includes a reference such as:

```text
ATLAS-0123ABCD4567EF89
```

Fastest lookup:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Web lookup:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

The web lookup requires the diagnostics operator token.

Direct KQL:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

Normal validation errors, no-result responses, 404s, and rate limits are not application faults and do not receive `ATLAS-...` references. Ask question text is not included in structured error/Ask telemetry logs.

## Stable naming and delete protection

`NAME_SUFFIX` is a stable installation ID. Reuse it with the same remote Terraform state.

Terraform `prevent_destroy` protects the globally named ACR, Azure AI Search, and Foundry resources from accidental deletion/replacement. Do not work around that protection by inventing a new suffix.

## Index embedding batch size

Keep:

```text
EMBEDDING_BATCH_SIZE="32"
```

The config example, Terraform default, cloud-indexer fallback, and deployment cap all enforce the production-safe 32-request batch size.
