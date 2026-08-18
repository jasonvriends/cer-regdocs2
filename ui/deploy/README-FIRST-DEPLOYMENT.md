# REGDOCS Atlas v1 first deployment

Start with:

```bash
./ui/deploy/deploy.sh
```

With no arguments it is **read-only**. It inspects what it can see and prints the next command.

REGDOCS Atlas has six pipeline stages. Stage 6 is the final data-processing stage. The deployment steps below are not additional stages.

## Personal computer

1. Finish Stage 4 normalization.
2. Confirm `workspace/4_normalize/` contains all five normalized outputs:
   - `documents.jsonl`
   - `pages.jsonl`
   - `chunks.jsonl`
   - `tables.jsonl`
   - `provenance.jsonl`
3. Create or choose the Azure Storage account and Blob container that will also hold Terraform state.
4. Create a container SAS with Read, Create, Write, and List permissions.
5. Upload the normalized package:

```bash
export AZURE_STORAGE_SAS_TOKEN='<container-sas>'

python tools/upload_cloud_inputs.py \
  --account <storage-account> \
  --container <container>
```

The uploader validates all five files, verifies remote sizes, and writes:

```text
workspace/4_normalize/source-package.json
```

You do **not** upload:

```text
Markdown
a second PDF copy
the whole workspace directory
the local SQLite database
local Stage 6 output
```

The v1 document viewer reconstructs readable HTML from Stage 5 Search chunks and links back to the original REGDOCS source.

## Cloud Shell setup

Clone/pull the final code and prepare configuration:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master

cp ui/deploy/config.env.example ui/deploy/config.env   # first deployment only
# edit ui/deploy/config.env
```

Keep the existing `NAME_SUFFIX` on updates. It is the stable installation ID, not a release number.

Paste a fresh container SAS into the shell:

```bash
source ui/deploy/config.env
read -rsp "Paste container SAS: " AZURE_STORAGE_SAS_TOKEN; echo
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
```

## Verify the cloud data package

```bash
./ui/deploy/deploy.sh --check-data
```

Do not proceed to Stage 5/6 publication until all five normalized files are reported present.

## Validate before Azure changes

```bash
./ui/deploy/deploy.sh --validate
```

This checks all deployment Bash scripts, Terraform configuration/formatting, Python compilation, TypeScript, and the production Next.js build. It does not change Azure. The wrapper removes an inherited interactive-shell `NODE_ENV` before Next.js validation so Cloud Shell cannot accidentally force the production build into development mode.

Review the Terraform plan:

```bash
./ui/deploy/deploy.sh --plan
```

If the plan proposes an unexpected destructive replacement of a protected global resource, stop and fix the configuration/state problem. Do not invent a new suffix as a workaround.

## Clean infrastructure rebuild

A clean rebuild normally means deleting the **Atlas infrastructure resource group only**, then letting the same Terraform state recreate it.

Preserve the source/storage account and Blob container that hold:

```text
workspace/4_normalize/
terraform/regdocs-atlas.tfstate
```

Do not delete that Storage account/container unless you deliberately intend to re-upload the complete Stage 4 source package and create fresh Terraform state.

The deployment script distinguishes resources that are merely recorded in Terraform state from workloads that still exist in Azure. If the Atlas infrastructure resource group has been deleted while the remote state Blob remains, `--full` first reconciles the foundation without Container Apps workloads, then builds the deterministic UI and publisher images server-side in ACR, waits for both builds to finish, and finally creates the UI + Stage 5 + Stage 6 workloads in the same invocation.

The build path does not probe the ACR repository and does not filter ACR Task history by image. Those Azure CLI paths were observed to hang or enter registry-authentication flows on a fresh registry. Atlas uses `az acr build` directly and waits for the server-side build result. It does not require `az acr login`, a registry username, or a registry password. If a deployment ever asks for a registry password, stop rather than entering one.

If Cloud Shell disconnects during an ACR build, the Azure-side build may continue. Rerunning `--full` is safe; if the workloads were not yet deployed, the script may rebuild the same deterministic commit tag before continuing.

## Follow the guide

Run:

```bash
./ui/deploy/deploy.sh
```

The guide will normally direct a first deployment through these actions:

```text
--infra-only
run the guide again
--full
Stage 5 publication
Stage 6 publication
--status
/diagnostics
```

Use the command the guide prints rather than guessing which action is next.

## Stage 5 Search

Stage 5 reads:

```text
chunks.jsonl
provenance.jsonl
```

It builds the Azure AI Search corpus used by:

```text
Ask
keyword/hybrid/semantic retrieval
source cards
HTML document viewer
Shelf evidence
coverage
```

If the guide says Stage 5 has not succeeded, run:

```bash
./ui/deploy/deploy.sh --restart-index
```

Monitor:

```bash
./ui/deploy/deploy.sh --status
```

## Stage 6 — final data stage

Stage 6 reads normalized `documents.jsonl` and `chunks.jsonl`, performs deterministic + Microsoft Foundry regulatory extraction, validates source evidence, publishes five intelligence indexes, and writes durable output to:

```text
workspace/6_enrich/
```

The five indexes are:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

Start Stage 6 only when intended:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

For the first quality check you may set:

```text
INTELLIGENCE_DOCUMENT_LIMIT="10"
```

Run the pilot, inspect the intelligence and supporting source evidence, then clear the limit and run Stage 6 for the complete corpus. The extraction cache in Blob reuses completed compatible model requests.

There is no manual “copy enrich to Blob” step in the cloud workflow.

## Final acceptance

When both Stage 5 and Stage 6 show `Succeeded`:

```bash
./ui/deploy/deploy.sh --status
```

Open the printed Atlas URL and then:

```text
https://<atlas-host>/diagnostics
```

Use the operator token to run protected live diagnostics. Verify the research UI, especially the HTML source viewer, against [`../../COMPLETION.md`](../../COMPLETION.md).

When that finite checklist passes, REGDOCS Atlas v1 is complete. There is no next pipeline stage.
