#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
CORE="$SCRIPT_DIR/deploy-core.sh"
CONFIG_FILE="$SCRIPT_DIR/config.env"
ORIGINAL_ARGS=("$@")

for ((i=0; i<${#ORIGINAL_ARGS[@]}; i++)); do
  if [[ "${ORIGINAL_ARGS[$i]}" == "--config" && $((i + 1)) -lt ${#ORIGINAL_ARGS[@]} ]]; then
    CONFIG_FILE="${ORIGINAL_ARGS[$((i + 1))]}"
  fi
done

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

has_arg() {
  local wanted="$1"
  local value
  for value in "${ORIGINAL_ARGS[@]}"; do
    [[ "$value" == "$wanted" ]] && return 0
  done
  return 1
}

load_config() {
  [[ -f "$CONFIG_FILE" ]] || fail "Missing $CONFIG_FILE. Copy ui/deploy/config.env.example to ui/deploy/config.env and edit it."
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  NORMALIZED_BLOB_PREFIX="${NORMALIZED_BLOB_PREFIX:-workspace/4_normalize}"
  STATE_BLOB="${STATE_BLOB:-terraform/regdocs-atlas.tfstate}"
  RESOURCE_GROUP="${RESOURCE_GROUP:-rg-regdocs-atlas}"
  STORAGE_SUBSCRIPTION_ID="${STORAGE_SUBSCRIPTION_ID:-${SUBSCRIPTION_ID:-}}"
}

require_config_value() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" && "$value" != *REPLACE* ]] || fail "$name is not configured in $CONFIG_FILE"
}

storage_sas() {
  local value="${AZURE_STORAGE_SAS_TOKEN:-}"
  value="${value#\?}"
  printf '%s' "$value"
}

print_source_contract() {
  cat <<EOF

SOURCE PACKAGE — created on your personal computer by Stage 4 normalize
Upload all five files to:
  https://${STORAGE_ACCOUNT:-<storage-account>}.blob.core.windows.net/${BLOB_CONTAINER:-<container>}/${NORMALIZED_BLOB_PREFIX:-workspace/4_normalize}/

  documents.jsonl   REQUIRED by Stage 6 deterministic + Foundry enrichment
  chunks.jsonl      REQUIRED by Stage 5 Search, Ask, HTML document viewer, Stage 6
  provenance.jsonl  REQUIRED by Stage 5 Search publication
  pages.jsonl       durable normalized archive (not read by deployed runtime today)
  tables.jsonl      durable normalized archive (table content is already represented in chunks)

You do NOT need to upload Markdown or a second PDF copy for the Atlas HTML viewer.
The viewer reconstructs documents from the Stage 5 Azure AI Search chunks.

You do NOT need to run or upload enrich on your personal computer for the cloud deployment.
Stage 6 runs in Azure when you explicitly request it and writes durable results back to:
  ${BLOB_CONTAINER:-<container>}/workspace/6_enrich/
EOF
}

print_upload_help() {
  if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
  fi
  NORMALIZED_BLOB_PREFIX="${NORMALIZED_BLOB_PREFIX:-workspace/4_normalize}"
  print_source_contract
  cat <<EOF

ON YOUR PERSONAL COMPUTER
1. Finish Stage 4 normalize and confirm these exist under workspace/4_normalize/:
     documents.jsonl pages.jsonl chunks.jsonl tables.jsonl provenance.jsonl

2. Create/use one existing Azure Storage account and one Blob container.

3. Create a CONTAINER SAS with Read + Create + Write + List permissions.
   Keep the SAS private and do not save it in config.env or Git.

4. In the REGDOCS repo/environment, set the SAS and upload the validated package:

   # PowerShell
   \$env:AZURE_STORAGE_SAS_TOKEN='<container-sas>'
   python tools/upload_cloud_inputs.py --account '${STORAGE_ACCOUNT:-<storage-account>}' --container '${BLOB_CONTAINER:-<container>}' --prefix '${NORMALIZED_BLOB_PREFIX}'

   # bash/zsh
   export AZURE_STORAGE_SAS_TOKEN='<container-sas>'
   python tools/upload_cloud_inputs.py --account '${STORAGE_ACCOUNT:-<storage-account>}' --container '${BLOB_CONTAINER:-<container>}' --prefix '${NORMALIZED_BLOB_PREFIX}'

The uploader validates all five Stage 4 files, uploads them, verifies remote sizes,
and writes ${NORMALIZED_BLOB_PREFIX}/source-package.json.

THEN MOVE TO CLOUD SHELL
  source ui/deploy/config.env
  read -rsp "Paste the same/fresh container SAS: " AZURE_STORAGE_SAS_TOKEN; echo
  export AZURE_STORAGE_SAS_TOKEN="\${AZURE_STORAGE_SAS_TOKEN#\?}"
  ./ui/deploy/deploy.sh --check-data
  ./ui/deploy/deploy.sh
EOF
}

blob_exists() {
  local name="$1"
  az storage blob show \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$BLOB_CONTAINER" \
    --name "$name" \
    --sas-token "$(storage_sas)" \
    --query properties.contentLength \
    --output tsv \
    --only-show-errors 2>/dev/null
}

check_data() {
  load_config
  for name in STORAGE_ACCOUNT BLOB_CONTAINER NORMALIZED_BLOB_PREFIX; do require_config_value "$name"; done
  command -v az >/dev/null 2>&1 || fail "Azure CLI (az) is required for --check-data."
  [[ -n "$(storage_sas)" ]] || {
    echo "No AZURE_STORAGE_SAS_TOKEN is set."
    print_upload_help
    return 2
  }

  echo "Checking Blob source package: $STORAGE_ACCOUNT/$BLOB_CONTAINER/${NORMALIZED_BLOB_PREFIX%/}/"
  local missing=0
  local filename size label
  for filename in documents.jsonl pages.jsonl chunks.jsonl tables.jsonl provenance.jsonl; do
    size="$(blob_exists "${NORMALIZED_BLOB_PREFIX%/}/$filename" || true)"
    case "$filename" in
      documents.jsonl) label="Stage 6" ;;
      chunks.jsonl) label="Stage 5 + viewer + Ask + Stage 6" ;;
      provenance.jsonl) label="Stage 5" ;;
      *) label="durable archive" ;;
    esac
    if [[ -n "$size" ]]; then
      printf '  OK       %-18s %12s bytes  %s\n' "$filename" "$size" "$label"
    else
      printf '  MISSING  %-18s               %s\n' "$filename" "$label"
      missing=1
    fi
  done

  local manifest_size
  manifest_size="$(blob_exists "${NORMALIZED_BLOB_PREFIX%/}/source-package.json" || true)"
  if [[ -n "$manifest_size" ]]; then
    printf '  OK       %-18s %12s bytes  upload manifest\n' "source-package.json" "$manifest_size"
  else
    echo "  NOTE     source-package.json is absent (older/manual upload). Re-upload with tools/upload_cloud_inputs.py to create it."
  fi

  if [[ "$missing" == 1 ]]; then
    echo
    echo "The cloud source package is incomplete. Do not start Stage 5 or Stage 6 yet."
    print_upload_help
    return 2
  fi

  echo
  echo "Source package is complete for REGDOCS Atlas v1."
  echo "  Stage 5 can build Search + Ask + HTML document viewing."
  echo "  Stage 6 can derive/publish findings, claims, events, relationships, commitments and obligations."
  return 0
}

latest_job_status() {
  local job="$1"
  az containerapp job execution list \
    --name "$job" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'sort_by(@, &properties.startTime)[-1].properties.status' \
    --output tsv \
    --only-show-errors 2>/dev/null || true
}

show_guide() {
  cat <<'EOF'
REGDOCS Atlas v1 — guided deployment
====================================
This command is READ ONLY. Running deploy.sh with no arguments never deploys.

REGDOCS Atlas has six pipeline stages only. Stage 6 is the final data stage.
The numbered items below are deployment steps, not additional pipeline stages.

Complete the project in this order:

  STEP 1  Personal computer: finish Stage 4 normalize
  STEP 2  Azure: create/use one Storage account + Blob container + container SAS
  STEP 3  Personal computer: upload the complete five-file Stage 4 package
  STEP 4  Cloud Shell: verify Blob data and durable Terraform state
  STEP 5  Validate code, review Terraform plan, deploy Azure workloads
  STEP 6  Run Stage 5 Search publication (Ask + HTML document viewer)
  STEP 7  Run Stage 6 regulatory intelligence (FINAL DATA STAGE)
  STEP 8  Verify diagnostics + UI and declare v1 complete
EOF

  if [[ ! -f "$CONFIG_FILE" ]]; then
    cat <<EOF

CURRENT BLOCKER: config.env does not exist.
NEXT:
  cp ui/deploy/config.env.example ui/deploy/config.env
  edit ui/deploy/config.env

Configure at least:
  SUBSCRIPTION_ID
  NAME_SUFFIX
  STORAGE_ACCOUNT
  STORAGE_RESOURCE_GROUP
  BLOB_CONTAINER
  CONFIRM_BILLABLE_DEPLOYMENT=yes

NAME_SUFFIX is your stable installation ID. Reuse it for normal updates.
The Storage account and Blob container must already exist because Terraform state
also lives in that container.
EOF
    print_upload_help
    return 0
  fi

  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  NORMALIZED_BLOB_PREFIX="${NORMALIZED_BLOB_PREFIX:-workspace/4_normalize}"
  STATE_BLOB="${STATE_BLOB:-terraform/regdocs-atlas.tfstate}"
  RESOURCE_GROUP="${RESOURCE_GROUP:-rg-regdocs-atlas}"
  print_source_contract

  local bad=0 name value
  for name in SUBSCRIPTION_ID NAME_SUFFIX STORAGE_ACCOUNT STORAGE_RESOURCE_GROUP BLOB_CONTAINER; do
    value="${!name:-}"
    if [[ -z "$value" || "$value" == *REPLACE* ]]; then
      echo "CONFIG MISSING: $name"
      bad=1
    fi
  done
  if [[ "$bad" == 1 ]]; then
    echo
    echo "NEXT: finish ui/deploy/config.env, then run ./ui/deploy/deploy.sh again."
    return 0
  fi

  if [[ "${CHAT_MODEL:-}" == "gpt-4.1-mini" ]]; then
    cat <<'EOF'

CURRENT BLOCKER: config.env still uses the retired v1 default chat model setting.
The repository's current v1 default is:

  CHAT_MODEL="gpt-5.4-mini"
  CHAT_MODEL_VERSION="2026-03-17"
  CHAT_SKU="GlobalStandard"

Update those CHAT_MODEL values in ui/deploy/config.env, then run this guide again.
Terraform will show the model-deployment change in --plan before anything is applied.
EOF
    return 0
  fi

  if [[ -z "$(storage_sas)" ]]; then
    cat <<'EOF'

CURRENT BLOCKER: AZURE_STORAGE_SAS_TOKEN is not set in this shell.
The SAS is intentionally NOT stored in config.env.

If normalized files are not uploaded yet, use --upload-help on your personal
computer. In Cloud Shell paste a fresh container SAS before state/data checks:

  read -rsp "Paste container SAS: " AZURE_STORAGE_SAS_TOKEN; echo
  export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
  ./ui/deploy/deploy.sh --check-data
  ./ui/deploy/deploy.sh
EOF
    return 0
  fi

  if ! command -v az >/dev/null 2>&1; then
    echo
    echo "Azure CLI is not available. Run this guide in Azure Cloud Shell for live readiness checks."
    return 0
  fi

  echo
  echo "STEP 4 — Blob/data readiness"
  if check_data; then
    :
  else
    return 0
  fi

  local state_size
  state_size="$(blob_exists "$STATE_BLOB" || true)"
  if [[ -n "$state_size" ]]; then
    echo "Terraform state: FOUND  $STATE_BLOB ($state_size bytes)"
  else
    echo "Terraform state: NOT FOUND  $STATE_BLOB"
    echo "This is normal for a first deployment when no prior Atlas resources exist."
    echo "Do not import anything merely because this is a new Cloud Shell session."
  fi

  if ! az account show --output none 2>/dev/null; then
    echo
    echo "NEXT: open/run this from Azure Cloud Shell (signed in), then run deploy.sh again."
    return 0
  fi
  az account set --subscription "$SUBSCRIPTION_ID" >/dev/null

  local app="app-regdocs-$NAME_SUFFIX"
  local stage5="job-regdocs-$NAME_SUFFIX"
  local stage6="job-regdocs-intelligence-$NAME_SUFFIX"
  local app_exists stage5_exists stage6_exists stage5_status stage6_status
  app_exists="$(az containerapp show --name "$app" --resource-group "$RESOURCE_GROUP" --query name -o tsv --only-show-errors 2>/dev/null || true)"
  stage5_exists="$(az containerapp job show --name "$stage5" --resource-group "$RESOURCE_GROUP" --query name -o tsv --only-show-errors 2>/dev/null || true)"
  stage6_exists="$(az containerapp job show --name "$stage6" --resource-group "$RESOURCE_GROUP" --query name -o tsv --only-show-errors 2>/dev/null || true)"

  echo
  echo "STEP 5 — Azure deployment readiness"
  [[ -n "$app_exists" ]] && echo "UI Container App: FOUND" || echo "UI Container App: NOT DEPLOYED"
  [[ -n "$stage5_exists" ]] && echo "Stage 5 job:      FOUND" || echo "Stage 5 job:      NOT DEPLOYED"
  [[ -n "$stage6_exists" ]] && echo "Stage 6 job:      FOUND" || echo "Stage 6 job:      NOT DEPLOYED"

  if [[ -z "$app_exists" || -z "$stage5_exists" || -z "$stage6_exists" ]]; then
    echo
    echo "BEFORE THE NEXT APPLY:"
    echo "  ./ui/deploy/deploy.sh --validate"
    echo "  ./ui/deploy/deploy.sh --plan"
    echo
    if [[ -z "$state_size" ]]; then
      echo "NEXT APPLY (first deployment foundation):"
      echo "  ./ui/deploy/deploy.sh --infra-only"
      echo "Then rerun ./ui/deploy/deploy.sh for the next instruction."
    else
      echo "NEXT APPLY (deploy/update workloads):"
      echo "  ./ui/deploy/deploy.sh --full"
      echo "ACR builds run in Azure. If builds are still running when the command exits, rerun --full afterward."
    fi
    return 0
  fi

  stage5_status="$(latest_job_status "$stage5")"
  stage6_status="$(latest_job_status "$stage6")"
  echo
  echo "STEP 6 — Stage 5 Search publication"
  echo "Latest Stage 5 status: ${stage5_status:-never run}"
  if [[ "$stage5_status" == "Running" || "$stage5_status" == "Processing" ]]; then
    echo "NEXT: Stage 5 is running. Use ./ui/deploy/deploy.sh --status."
    return 0
  fi
  if [[ "$stage5_status" != "Succeeded" ]]; then
    echo "NEXT: ./ui/deploy/deploy.sh --restart-index"
    echo "Stage 5 creates the Search corpus used by Ask and the HTML document viewer."
    return 0
  fi

  echo
  echo "STEP 7 — Stage 6 regulatory intelligence (FINAL DATA STAGE)"
  echo "Latest Stage 6 status: ${stage6_status:-never run}"
  if [[ "$stage6_status" == "Running" || "$stage6_status" == "Processing" ]]; then
    echo "NEXT: Stage 6 is running. Use ./ui/deploy/deploy.sh --status."
    return 0
  fi
  if [[ "$stage6_status" != "Succeeded" ]]; then
    echo "NEXT: ./ui/deploy/deploy.sh --restart-intelligence"
    echo "You do NOT run enrich locally first. The Azure Stage 6 job does deterministic + Foundry enrichment,"
    echo "publishes five intelligence indexes, and writes workspace/6_enrich back to Blob."
    [[ -n "${INTELLIGENCE_DOCUMENT_LIMIT:-}" ]] && echo "Pilot limit configured: INTELLIGENCE_DOCUMENT_LIMIT=$INTELLIGENCE_DOCUMENT_LIMIT"
    return 0
  fi

  cat <<EOF

STEP 8 — PROJECT READY FOR FINAL ACCEPTANCE
Stage 5: SUCCEEDED
Stage 6: SUCCEEDED

There is no further pipeline stage.

NEXT:
  ./ui/deploy/deploy.sh --status
  open https://$app.../diagnostics using the actual URL printed by --status
  run protected live diagnostics
  complete COMPLETION.md against the deployed UI

The v1 UI should provide:
  grounded Ask + citations
  filters and live coverage
  HTML document viewing + page jump + Original in REGDOCS
  Shelf + Shelf-only Ask + CSV export
  regulatory timeline
  relationship graph
  Findings & claims
  Commitments & obligations
  ATLAS error tracing

Normal later UI releases use:
  ./ui/deploy/deploy.sh --ui-only

When normalized source data changes:
  upload the new five-file Stage 4 package from your personal computer
  ./ui/deploy/deploy.sh --restart-index
  ./ui/deploy/deploy.sh --restart-intelligence
EOF
}

if [[ ${#ORIGINAL_ARGS[@]} -eq 0 ]] || has_arg --guide; then
  show_guide
  exit 0
fi

if has_arg --upload-help; then
  print_upload_help
  exit 0
fi

if has_arg --check-data; then
  check_data
  exit $?
fi

if [[ ! -x "$CORE" ]]; then
  fail "Missing executable $CORE. The deployment package is incomplete."
fi

# Full/publication actions require the complete normalized source package. UI-only,
# infra-only, plan, status, and error operations do not.
if has_arg --full || has_arg --restart-index || has_arg --restart-intelligence; then
  check_data || exit $?
fi

if has_arg --validate; then
  bash -n "$0"
  bash -n "$CORE"
fi

exec "$CORE" "${ORIGINAL_ARGS[@]}"
