# REGDOCS Atlas operations

## Trace a user-visible server error

Atlas server failures return a short reference such as:

```text
ATLAS-0123ABCD4567EF89
```

The same reference is written as a structured `atlas.error` event to the Container Apps console log, which is already connected to the deployment's Log Analytics workspace.

Validation errors, normal 404s, and rate limits do not receive server-error references because they are not application faults.

### Operator lookup page

Open:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

The page asks for the diagnostics operator token. Retrieve it from the same remote Terraform state used by `deploy.sh`:

```bash
terraform -chdir=ui/deploy/terraform output -raw diagnostics_operator_token
printf '\n'
```

The token is not shown by normal `terraform output` because it is marked sensitive. The lookup API uses the UI managed identity with the read-only `Log Analytics Reader` role and never sends Azure credentials to the browser.

Log Analytics ingestion is not instantaneous. If a new reference is not found immediately, retry after a few minutes.

### Query Log Analytics directly

In the deployment workspace, use:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30d)
| where Log_s contains "ATLAS-0123ABCD4567EF89"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, ContainerName_s, Log_s
| order by TimeGenerated desc
```

The structured log includes the operation, internal exception name/message/stack, and limited non-question context useful for diagnosis. Ask question text is intentionally not logged by the error/telemetry helpers.

## Live service diagnostics

Open `/diagnostics` and choose **Run live checks** to verify Azure AI Search, hybrid/semantic retrieval, the document-view backend, Microsoft Foundry grounded inference, and the Stage 6 intelligence indexes.
