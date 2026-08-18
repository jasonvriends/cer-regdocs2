#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/terraform"
CONFIG_FILE="$SCRIPT_DIR/config.env"
MODE="full"
MODE_SET=false
OPERATION="deploy"
ERROR_ID=""
RESTART_INDEX=false

usage() {
  cat <<'EOF'
Usage:
  ./ui/deploy/deploy.sh --validate
  ./ui/deploy/deploy.sh [--config PATH] --plan
  ./ui/deploy/deploy.sh [--config PATH] [--full|--ui-only|--infra-only] [--restart-index]
  ./ui/deploy/deploy.sh [--config PATH] --status
  ./ui/deploy/deploy.sh [--config PATH] --error ATLAS-0123ABCD4567EF89

Safe to run again after a Cloud Shell timeout. Terraform state is remote, ACR
builds continue in Azure, and completed work is reused.

Preflight:
  --validate        Run local Bash, Terraform, Python, TypeScript, and Next.js
                    validation. Makes no Azure API calls and needs no SAS token.
  --plan            Read the existing remote Terraform state and show the
                    infrastructure plan without applying it or building images.

Deployment modes:
  --full            Reconcile infrastructure, build/deploy UI and indexer, and
                    start indexing when the indexer changed. This is the default.
  --ui-only         Reconcile infrastructure, build/deploy only the UI, preserve
                    the deployed indexer image, and never start the indexing job.
  --infra-only      Reconcile Terraform/RBAC/config only. Build no images and
                    never start the indexing job.

Read-only operations:
  --status          Show the live UI URL/image, indexer image, recent job
                    executions, and recent logs for the latest execution from
                    Log Analytics. No SAS token is required.
  --error ID        Find one ATLAS-... server error in Log Analytics. No SAS
                    token is required.

Other options:
  --restart-index   With a full deployment, force a new indexing execution even
                    when the latest execution already succeeded.
  --config PATH     Read deployment settings from PATH (default: config.env).

Examples:
  ./ui/deploy/deploy.sh --validate
  ./ui/deploy/deploy.sh --plan
  ./ui/deploy/deploy.sh --ui-only
  ./ui/deploy/deploy.sh --infra-only
  ./ui/deploy/deploy.sh
  ./ui/deploy/deploy.sh --restart-index
  ./ui/deploy/deploy.sh --status
  ./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

set_mode() {
  local requested="$1"
  [[ "$OPERATION" == "deploy" ]] || fail "Deployment modes cannot be combined with --validate, --plan, --status, or --error."
  if [[ "$MODE_SET" == true && "$MODE" != "$requested" ]]; then
    fail "Choose only one deployment mode: --full, --ui-only, or --infra-only."
  fi
  MODE="$requested"
  MODE_SET=true
}

set_operation() {
  local requested="$1"
  [[ "$OPERATION" == "deploy" ]] || fail "Choose only one operation: --validate, --plan, --status, or --error."
  [[ "$MODE_SET" == false && "$RESTART_INDEX" == false ]] || fail "$requested cannot be combined with deployment-mode flags."
  OPERATION="$requested"
}

while (($#)); do
  case "$1" in
    --config)
      [[ $# -ge 2 ]] || fail "--config requires a path"
      CONFIG_FILE="$2"
      shift 2
      ;;
    --validate)
      set_operation validate
      shift
      ;;
    --plan)
      set_operation plan
      shift
      ;;
    --full)
      set_mode full
      shift
      ;;
    --ui-only)
      set_mode ui-only
      shift
      ;;
    --infra-only)
      set_mode infra-only
      shift
      ;;
    --restart-index)
      [[ "$OPERATION" == "deploy" ]] || fail "--restart-index cannot be combined with --validate, --plan, --status, or --error."
      RESTART_INDEX=true
      shift
      ;;
    --status)
      set_operation status
      shift
      ;;
    --error)
      [[ $# -ge 2 ]] || fail "--error requires an ATLAS-... reference"
      set_operation error
      ERROR_ID="$(tr '[:lower:]' '[:upper:]' <<<"$2")"
      shift 2
      ;;
    --no-start)
      fail "--no-start was removed. Use --ui-only for a UI release or --infra-only for Terraform/RBAC/config only."
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$RESTART_INDEX" == true && "$MODE" != "full" ]]; then
  fail "--restart-index can only be used with the full deployment mode."
fi

log() {
  printf '\n==> %s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_value() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || fail "$name is empty in $CONFIG_FILE"
  [[ "$value" != *REPLACE* ]] || fail "$name still contains REPLACE in $CONFIG_FILE"
}

terraform_output() {
  local name="$1"
  local value
  value="$(terraform -chdir="$TERRAFORM_DIR" output -raw "$name" 2>/dev/null || true)"
  [[ "$value" == "null" ]] && value=""
  printf '%s' "$value"
}

image_tag_from_reference() {
  local image="$1"
  [[ -n "$image" ]] || return 0
  printf '%s' "${image##*:}"
}

if [[ "$OPERATION" == "validate" ]]; then
  for command in bash terraform python npm; do
    require_command "$command"
  done

  log "Validating deployment shell syntax"
  bash -n "$SCRIPT_DIR/deploy.sh"

  log "Validating Terraform formatting and configuration without a backend"
  terraform -chdir="$TERRAFORM_DIR" fmt -check -recursive
  terraform -chdir="$TERRAFORM_DIR" init -backend=false -input=false
  terraform -chdir="$TERRAFORM_DIR" validate

  log "Compiling Python entry points"
  (
    cd "$REPOSITORY_ROOT"
    python -m compileall -q pipeline.py regdocs_atlas tools
  )

  log "Validating the Next.js UI"
  (
    cd "$REPOSITORY_ROOT/ui"
    npm ci
    npm run typecheck
    npm run build
  )

  log "Validation complete"
  echo "No Azure resources were read, created, changed, or deleted."
  exit 0
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  fail "Missing $CONFIG_FILE. Copy config.env.example to config.env and edit it first."
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

LOCATION="${LOCATION:-eastus2}"
SEARCH_LOCATION="${SEARCH_LOCATION:-$LOCATION}"
FOUNDRY_LOCATION="${FOUNDRY_LOCATION:-$LOCATION}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-regdocs-atlas}"
STATE_BLOB="${STATE_BLOB:-terraform/regdocs-atlas.tfstate}"
NORMALIZED_BLOB_PREFIX="${NORMALIZED_BLOB_PREFIX:-workspace/4_normalize}"
EMBEDDING_CACHE_BLOB="${EMBEDDING_CACHE_BLOB:-workspace/5_index/embedding-cache.sqlite}"
STORAGE_SUBSCRIPTION_ID="${STORAGE_SUBSCRIPTION_ID:-$SUBSCRIPTION_ID}"
EMBEDDING_BATCH_SIZE="${EMBEDDING_BATCH_SIZE:-32}"
APP_NAME="app-regdocs-${NAME_SUFFIX:-}"
JOB_NAME="job-regdocs-${NAME_SUFFIX:-}"
LOG_WORKSPACE_NAME="log-regdocs-${NAME_SUFFIX:-}"

[[ "$EMBEDDING_BATCH_SIZE" =~ ^[0-9]+$ ]] || fail "EMBEDDING_BATCH_SIZE must be a whole number."
(( EMBEDDING_BATCH_SIZE >= 1 )) || fail "EMBEDDING_BATCH_SIZE must be at least 1."
if (( EMBEDDING_BATCH_SIZE > 32 )); then
  echo "WARNING: EMBEDDING_BATCH_SIZE=$EMBEDDING_BATCH_SIZE is above the production-safe maximum; using 32 instead." >&2
  EMBEDDING_BATCH_SIZE=32
fi

# Read-only status/error operations deliberately do not require Terraform state
# access or a Storage SAS token.
if [[ "$OPERATION" == "status" || "$OPERATION" == "error" ]]; then
  for name in SUBSCRIPTION_ID NAME_SUFFIX RESOURCE_GROUP; do
    require_value "$name"
  done
  require_command az

  if ! az account show --output none 2>/dev/null; then
    fail "Cloud Shell is not signed in."
  fi
  az account set --subscription "$SUBSCRIPTION_ID"

  WORKSPACE_ID="$(az monitor log-analytics workspace show \
    --resource-group "$RESOURCE_GROUP" \
    --workspace-name "$LOG_WORKSPACE_NAME" \
    --query customerId \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"
  [[ -n "$WORKSPACE_ID" ]] || fail "Could not find Log Analytics workspace $LOG_WORKSPACE_NAME in $RESOURCE_GROUP."

  if [[ "$OPERATION" == "error" ]]; then
    [[ "$ERROR_ID" =~ ^ATLAS-[A-F0-9]{16}$ ]] || fail "Error reference must look like ATLAS-0123ABCD4567EF89."
    log "Looking up $ERROR_ID in Log Analytics"
    az monitor log-analytics query \
      --workspace "$WORKSPACE_ID" \
      --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(30d) | where Log_s contains '$ERROR_ID' | project Time=TimeGenerated, App=ContainerAppName_s, Revision=RevisionName_s, Container=ContainerName_s, Message=Log_s | order by Time desc" \
      --output table \
      --only-show-errors
    exit 0
  fi

  UI_FQDN="$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"
  UI_IMAGE="$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'properties.template.containers[0].image' \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"
  INDEXER_IMAGE="$(az containerapp job show \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'properties.template.containers[0].image' \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"

  log "REGDOCS Atlas deployment status"
  [[ -n "$UI_FQDN" ]] && echo "UI: https://$UI_FQDN" || echo "UI: not found"
  [[ -n "$UI_IMAGE" ]] && echo "UI image: $UI_IMAGE"
  [[ -n "$INDEXER_IMAGE" ]] && echo "Indexer image: $INDEXER_IMAGE"
  echo "Log Analytics: $LOG_WORKSPACE_NAME ($WORKSPACE_ID)"

  echo
  echo "Recent index executions:"
  az containerapp job execution list \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'sort_by(@, &properties.startTime)[-10:].{Name:name,Status:properties.status,Started:properties.startTime,Ended:properties.endTime}' \
    --output table \
    --only-show-errors 2>/dev/null || echo "No indexer job/executions found."

  LATEST_EXECUTION_NAME="$(az containerapp job execution list \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'sort_by(@, &properties.startTime)[-1].name' \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"

  if [[ -n "$LATEST_EXECUTION_NAME" ]]; then
    echo
    echo "Recent Log Analytics output for $LATEST_EXECUTION_NAME:"
    az monitor log-analytics query \
      --workspace "$WORKSPACE_ID" \
      --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(48h) | where ContainerGroupName_s startswith '$LATEST_EXECUTION_NAME' | project Time=TimeGenerated, Message=Log_s | order by Time asc | take 200" \
      --output table \
      --only-show-errors 2>/dev/null || echo "Logs are not available yet. Log Analytics ingestion can take a few minutes."
  fi
  exit 0
fi

for name in \
  SUBSCRIPTION_ID NAME_SUFFIX STORAGE_ACCOUNT STORAGE_SUBSCRIPTION_ID STORAGE_RESOURCE_GROUP \
  BLOB_CONTAINER RESOURCE_GROUP LOCATION SEARCH_LOCATION FOUNDRY_LOCATION \
  STATE_BLOB NORMALIZED_BLOB_PREFIX EMBEDDING_CACHE_BLOB; do
  require_value "$name"
done

if [[ "$OPERATION" == "deploy" ]]; then
  [[ "${CONFIRM_BILLABLE_DEPLOYMENT:-}" == "yes" ]] || fail \
    "Set CONFIRM_BILLABLE_DEPLOYMENT=yes after reviewing the billable Azure resources."
fi
[[ -n "${AZURE_STORAGE_SAS_TOKEN:-}" ]] || fail \
  "Export AZURE_STORAGE_SAS_TOKEN. It is used only by Cloud Shell to access remote Terraform state and, for full deployments, verify index inputs."
[[ "$NAME_SUFFIX" =~ ^[a-z0-9]{3,12}$ ]] || fail \
  "NAME_SUFFIX must be 3-12 lowercase letters and digits."
[[ "$STORAGE_ACCOUNT" =~ ^[a-z0-9]{3,24}$ ]] || fail \
  "STORAGE_ACCOUNT must be 3-24 lowercase letters and digits."

for command in az git terraform; do
  require_command "$command"
done

if ! az account show --output none 2>/dev/null; then
  fail "Cloud Shell is not signed in. Open it from the Azure portal on your company computer; no login occurs inside Container Apps."
fi

log "Selecting Azure subscription"
az account set --subscription "$SUBSCRIPTION_ID"

SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
export ARM_SAS_TOKEN="$SAS_TOKEN"
export ARM_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"

if [[ "$OPERATION" == "deploy" && "$MODE" == "full" ]]; then
  log "Verifying normalized Blob inputs required by the indexing job"
  for filename in chunks.jsonl provenance.jsonl; do
    blob_name="${NORMALIZED_BLOB_PREFIX%/}/$filename"
    az storage blob show \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$BLOB_CONTAINER" \
      --name "$blob_name" \
      --sas-token "$SAS_TOKEN" \
      --query '[name,properties.contentLength]' \
      --output tsv \
      --only-show-errors >/dev/null || fail \
        "Cannot read $blob_name using the supplied SAS token. Check its expiry, permissions, IP restrictions, and Storage firewall."
    echo "Verified $blob_name"
  done
fi

export TF_VAR_subscription_id="$SUBSCRIPTION_ID"
export TF_VAR_resource_group_name="$RESOURCE_GROUP"
export TF_VAR_location="$LOCATION"
export TF_VAR_search_location="$SEARCH_LOCATION"
export TF_VAR_foundry_location="$FOUNDRY_LOCATION"
export TF_VAR_name_suffix="$NAME_SUFFIX"
export TF_VAR_storage_account_name="$STORAGE_ACCOUNT"
export TF_VAR_storage_subscription_id="$STORAGE_SUBSCRIPTION_ID"
export TF_VAR_storage_resource_group_name="$STORAGE_RESOURCE_GROUP"
export TF_VAR_blob_container_name="$BLOB_CONTAINER"
export TF_VAR_normalized_blob_prefix="$NORMALIZED_BLOB_PREFIX"
export TF_VAR_embedding_cache_blob="$EMBEDDING_CACHE_BLOB"
CURRENT_IMAGE_TAG="$(git -C "$REPOSITORY_ROOT" rev-parse --short=12 HEAD)"

# Optional config.env overrides map directly to Terraform variables.
declare -A TF_OVERRIDES=(
  [SEARCH_SKU]=search_sku
  [SEARCH_PARTITIONS]=search_partition_count
  [SEARCH_REPLICAS]=search_replica_count
  [SEARCH_SEMANTIC_PLAN]=search_semantic_sku
  [SEARCH_INDEX]=search_index_name
  [SEARCH_VECTOR_FIELD]=search_vector_field
  [SEARCH_SEMANTIC_CONFIGURATION]=search_semantic_configuration
  [UI_ALLOWED_IP_CIDRS]=ui_allowed_ip_cidrs
  [EMBEDDING_DEPLOYMENT]=embedding_deployment_name
  [EMBEDDING_MODEL]=embedding_model_name
  [EMBEDDING_MODEL_VERSION]=embedding_model_version
  [EMBEDDING_SKU]=embedding_sku
  [EMBEDDING_CAPACITY]=embedding_capacity
  [EMBEDDING_DIMENSIONS]=embedding_dimensions
  [EMBEDDING_BATCH_SIZE]=embedding_batch_size
  [SEARCH_UPLOAD_BATCH_SIZE]=search_upload_batch_size
  [CHAT_DEPLOYMENT]=chat_deployment_name
  [CHAT_MODEL]=chat_model_name
  [CHAT_MODEL_VERSION]=chat_model_version
  [CHAT_SKU]=chat_sku
  [CHAT_CAPACITY]=chat_capacity
)

for config_name in "${!TF_OVERRIDES[@]}"; do
  if [[ -n "${!config_name:-}" ]]; then
    terraform_name="${TF_OVERRIDES[$config_name]}"
    export "TF_VAR_${terraform_name}=${!config_name}"
  fi
done

log "Initializing remote Terraform state in the existing Blob container"
terraform -chdir="$TERRAFORM_DIR" init -reconfigure -input=false \
  -backend-config="storage_account_name=$STORAGE_ACCOUNT" \
  -backend-config="container_name=$BLOB_CONTAINER" \
  -backend-config="key=$STATE_BLOB"

STATE_LIST="$(terraform -chdir="$TERRAFORM_DIR" state list 2>/dev/null || true)"
UI_IN_STATE=false
INDEXER_IN_STATE=false
grep -Eq '^azurerm_container_app\.ui' <<<"$STATE_LIST" && UI_IN_STATE=true
grep -Eq '^azurerm_container_app_job\.indexer' <<<"$STATE_LIST" && INDEXER_IN_STATE=true

LEGACY_IMAGE_TAG="$(terraform_output deployed_image_tag)"
PREVIOUS_UI_IMAGE_TAG="$(terraform_output deployed_ui_image_tag)"
PREVIOUS_INDEXER_IMAGE_TAG="$(terraform_output deployed_indexer_image_tag)"

if [[ "$UI_IN_STATE" == true ]]; then
  ACTUAL_UI_IMAGE="$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'properties.template.containers[0].image' \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"
  ACTUAL_UI_TAG="$(image_tag_from_reference "$ACTUAL_UI_IMAGE")"
  [[ -n "$ACTUAL_UI_TAG" ]] && PREVIOUS_UI_IMAGE_TAG="$ACTUAL_UI_TAG"
fi

if [[ "$INDEXER_IN_STATE" == true ]]; then
  ACTUAL_INDEXER_IMAGE="$(az containerapp job show \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query 'properties.template.containers[0].image' \
    --output tsv \
    --only-show-errors 2>/dev/null || true)"
  ACTUAL_INDEXER_TAG="$(image_tag_from_reference "$ACTUAL_INDEXER_IMAGE")"
  [[ -n "$ACTUAL_INDEXER_TAG" ]] && PREVIOUS_INDEXER_IMAGE_TAG="$ACTUAL_INDEXER_TAG"
fi

[[ -n "$PREVIOUS_UI_IMAGE_TAG" ]] || PREVIOUS_UI_IMAGE_TAG="$LEGACY_IMAGE_TAG"
[[ -n "$PREVIOUS_INDEXER_IMAGE_TAG" ]] || PREVIOUS_INDEXER_IMAGE_TAG="$LEGACY_IMAGE_TAG"

if [[ "$MODE" == "ui-only" && ( "$UI_IN_STATE" != true || "$INDEXER_IN_STATE" != true ) ]]; then
  fail "--ui-only requires an existing deployed UI and indexer. Use a full deployment first."
fi

if [[ "$UI_IN_STATE" == true || "$INDEXER_IN_STATE" == true ]]; then
  [[ -n "$PREVIOUS_UI_IMAGE_TAG" ]] || fail "Could not determine the currently deployed UI image tag."
  [[ -n "$PREVIOUS_INDEXER_IMAGE_TAG" ]] || fail "Could not determine the currently deployed indexer image tag."
  FOUNDATION_DEPLOY_WORKLOADS=true
  FOUNDATION_UI_IMAGE_TAG="$PREVIOUS_UI_IMAGE_TAG"
  FOUNDATION_INDEXER_IMAGE_TAG="$PREVIOUS_INDEXER_IMAGE_TAG"
else
  FOUNDATION_DEPLOY_WORKLOADS=false
  FOUNDATION_UI_IMAGE_TAG="$CURRENT_IMAGE_TAG"
  FOUNDATION_INDEXER_IMAGE_TAG="$CURRENT_IMAGE_TAG"
fi

if [[ "$OPERATION" == "plan" ]]; then
  log "Planning Terraform changes against the existing remote state"
  terraform -chdir="$TERRAFORM_DIR" plan -input=false \
    -lock-timeout=5m \
    -var="deploy_workloads=$FOUNDATION_DEPLOY_WORKLOADS" \
    -var="ui_image_tag=$FOUNDATION_UI_IMAGE_TAG" \
    -var="indexer_image_tag=$FOUNDATION_INDEXER_IMAGE_TAG"
  echo
  echo "Plan only: no Terraform changes were applied, no images were built, and no indexing job was started."
  exit 0
fi

log "Reconciling Azure infrastructure (safe to rerun)"
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve -input=false \
  -lock-timeout=5m \
  -var="deploy_workloads=$FOUNDATION_DEPLOY_WORKLOADS" \
  -var="ui_image_tag=$FOUNDATION_UI_IMAGE_TAG" \
  -var="indexer_image_tag=$FOUNDATION_INDEXER_IMAGE_TAG"

if [[ "$MODE" == "infra-only" ]]; then
  UI_URL="$(terraform_output ui_url)"
  log "Infrastructure reconciliation complete"
  [[ -n "$UI_URL" ]] && echo "UI: $UI_URL"
  echo "No images were built and the indexing job was not started."
  exit 0
fi

ACR_NAME="$(terraform_output container_registry_name)"
ACR_LOGIN_SERVER="$(terraform_output container_registry_login_server)"
[[ -n "$ACR_NAME" && -n "$ACR_LOGIN_SERVER" ]] || fail "Terraform did not return the Container Registry outputs."

UI_IMAGE="regdocs-ui:$CURRENT_IMAGE_TAG"
INDEXER_IMAGE="regdocs-indexer:$CURRENT_IMAGE_TAG"

image_exists() {
  az acr repository show \
    --name "$ACR_NAME" \
    --image "$1" \
    --output none \
    --only-show-errors 2>/dev/null
}

build_active() {
  local image="$1"
  local active
  active="$(az acr task list-runs \
    --registry "$ACR_NAME" \
    --image "$image" \
    --query "[?status=='Queued' || status=='Running' || status=='Started'] | length(@)" \
    --output tsv \
    --only-show-errors 2>/dev/null || echo 0)"
  [[ "${active:-0}" -gt 0 ]]
}

queue_build() {
  local image="$1"
  local dockerfile="$2"
  if image_exists "$image"; then
    echo "Image already built: $image"
    return 1
  fi
  if build_active "$image"; then
    echo "Image build is already queued or running: $image"
    return 1
  fi
  log "Queuing server-side ACR build for $image"
  if ! (
    cd "$REPOSITORY_ROOT"
    az acr build \
      --registry "$ACR_NAME" \
      --image "$image" \
      --file "$dockerfile" \
      --no-wait \
      --no-logs \
      . \
      --only-show-errors \
      --output none
  ); then
    fail "Failed to queue the ACR build for $image"
  fi
  return 0
}

show_recent_builds() {
  az acr task list-runs \
    --registry "$ACR_NAME" \
    --top 5 \
    --query '[].{Image:outputImages[0],Status:status,Started:startTime}' \
    --output table \
    --only-show-errors || true
}

if [[ "$MODE" == "ui-only" ]]; then
  QUEUED=false
  queue_build "$UI_IMAGE" "ui/deploy/containers/ui.Dockerfile" && QUEUED=true

  if [[ "$QUEUED" == true ]] || ! image_exists "$UI_IMAGE"; then
    log "ACR is building the UI image independently of this Cloud Shell session"
    show_recent_builds
    echo
    echo "Run this same --ui-only command again after the build finishes."
    exit 0
  fi

  log "Deploying only the UI; preserving the indexer image and execution state"
  terraform -chdir="$TERRAFORM_DIR" apply -auto-approve -input=false \
    -lock-timeout=5m \
    -var="deploy_workloads=true" \
    -var="ui_image_tag=$CURRENT_IMAGE_TAG" \
    -var="indexer_image_tag=$PREVIOUS_INDEXER_IMAGE_TAG"

  UI_URL="$(terraform_output ui_url)"
  log "UI-only deployment complete"
  echo "UI: $UI_URL"
  echo "UI image tag: $CURRENT_IMAGE_TAG"
  echo "Indexer image tag preserved: $PREVIOUS_INDEXER_IMAGE_TAG"
  echo "The indexing job was not built, changed, or started."
  exit 0
fi

QUEUED=false
queue_build "$UI_IMAGE" "ui/deploy/containers/ui.Dockerfile" && QUEUED=true
queue_build "$INDEXER_IMAGE" "ui/deploy/containers/indexer.Dockerfile" && QUEUED=true

if [[ "$QUEUED" == true ]] || ! image_exists "$UI_IMAGE" || ! image_exists "$INDEXER_IMAGE"; then
  log "ACR is building the images independently of this Cloud Shell session"
  show_recent_builds
  echo
  echo "Run this same full deployment command again after the builds finish."
  exit 0
fi

log "Deploying the UI and indexing job from completed ACR images"
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve -input=false \
  -lock-timeout=5m \
  -var="deploy_workloads=true" \
  -var="ui_image_tag=$CURRENT_IMAGE_TAG" \
  -var="indexer_image_tag=$CURRENT_IMAGE_TAG"

JOB_NAME="$(terraform_output index_job_name)"
UI_URL="$(terraform_output ui_url)"

log "Deployment status"
echo "UI: $UI_URL"
echo "Indexer job: $JOB_NAME"

LATEST_EXECUTION="$(az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query 'sort_by(@, &properties.startTime)[-1].[properties.status,name,properties.template.containers[0].image]' \
  --output tsv \
  --only-show-errors 2>/dev/null || true)"
LATEST_STATUS="$(cut -f1 <<<"$LATEST_EXECUTION")"
LATEST_NAME="$(cut -f2 <<<"$LATEST_EXECUTION")"
LATEST_IMAGE="$(cut -f3 <<<"$LATEST_EXECUTION")"
EXPECTED_INDEXER_IMAGE="$ACR_LOGIN_SERVER/$INDEXER_IMAGE"

case "$LATEST_STATUS" in
  Running|Processing)
    echo "Index execution is already active: $LATEST_NAME ($LATEST_STATUS). No duplicate was started."
    ;;
  Succeeded)
    if [[ "$RESTART_INDEX" == true || "$LATEST_IMAGE" != "$EXPECTED_INDEXER_IMAGE" ]]; then
      if [[ "$RESTART_INDEX" == true ]]; then
        log "Starting a new indexing execution by explicit request"
      else
        log "Starting a new indexing execution because the deployed indexer image changed"
      fi
      az containerapp job start --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" \
        --no-wait --output none --only-show-errors
    else
      echo "Latest index execution succeeded: $LATEST_NAME. Use --restart-index only when Blob inputs changed."
    fi
    ;;
  *)
    log "Starting the resumable Azure indexing job"
    [[ -n "$LATEST_STATUS" ]] && echo "Previous execution: $LATEST_NAME ($LATEST_STATUS)"
    az containerapp job start --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" \
      --no-wait --output none --only-show-errors
    echo "The job now runs in Azure and survives Cloud Shell disconnects. Use --status to inspect executions and Log Analytics output."
    ;;
esac

echo
echo "Recent index executions:"
az containerapp job execution list \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query 'sort_by(@, &properties.startTime)[-5:].{Name:name,Status:properties.status,Started:properties.startTime,Ended:properties.endTime}' \
  --output table \
  --only-show-errors || true

echo
echo "Use ./ui/deploy/deploy.sh --status for recent indexer logs from Log Analytics."
