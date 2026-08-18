# REGDOCS Atlas first deployment

Run `./ui/deploy/deploy.sh` with no arguments. It is read-only and tells you the next phase.

The deployment is intentionally split across your personal computer and Azure Cloud Shell.

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
5. Upload the normalized package with:

```bash
python tools/upload_cloud_inputs.py \
  --account <storage-account> \
  --container <container>
```

The uploader uses `AZURE_STORAGE_SAS_TOKEN`, validates all five files, verifies upload sizes, and writes `workspace/4_normalize/source-package.json`.

You do not upload Markdown or another PDF copy for the HTML document viewer. The deployed viewer reconstructs documents from the Stage 5 Search chunks.

You also do not need to run Stage 6 enrichment locally for the cloud workflow.

## Cloud Shell

After cloning/pulling the repo and editing `ui/deploy/config.env`:

```bash
source ui/deploy/config.env
read -rsp "Paste container SAS: " AZURE_STORAGE_SAS_TOKEN; echo
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"

./ui/deploy/deploy.sh --check-data
./ui/deploy/deploy.sh
```

The no-argument command is a read-only guide. It inspects what is present and tells you the exact next command.

Typical first deployment sequence:

```text
personal computer: Stage 4 normalize
personal computer: upload five-file normalized package
Cloud Shell:        --check-data
Cloud Shell:        --infra-only
Cloud Shell:        run deploy.sh again for guidance
Cloud Shell:        --full
Azure:              Stage 5 Search publication
Cloud Shell:        --restart-intelligence
Azure:              Stage 6 deterministic + Foundry enrichment/publication
Cloud Shell:        --status and /diagnostics
```

Stage 5 reads `chunks.jsonl` and `provenance.jsonl`. It creates the Azure AI Search corpus used by Ask and the HTML document viewer.

Stage 6 reads `documents.jsonl` and `chunks.jsonl`. The Azure intelligence job performs the model extraction, merges deterministic metadata, publishes entities/relations/events/claims/obligations, and uploads its durable outputs back into the same container under `workspace/6_enrich/`.

Therefore there is no manual "copy enrich to Blob" step when you use the cloud Stage 6 job.
