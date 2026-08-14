#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/terraform"
CONFIG_FILE="$SCRIPT_DIR/config.env"
RESTART_INDEX=false
NO_START=false

usage() {
  cat <<'EOF'
Usage: ./ui/deploy/deploy.sh [--config PATH] [--restart-index] [--no-start]

Safe to run again after a Cloud Shell timeout. Completed Terraform resources,
ACR images, and an active or successful indexing execution are reused.

  --config PATH     Read deployment settings from PATH (default: config.env)
  --restart-index   Start a new index execution even if the last one succeeded
  --no-start        Provision everything but do not start the indexing job
EOF
}

while (($#)); do
  case "$1" in
    --config)
      [[ $# -ge 2 ]] || { echo "ERROR: --config requires a path" >&2; exit 2; }
      CONFIG_FILE="$2"
      shift 2
      ;;
    --restart-index)
      RESTART_INDEX=true
      shift
      ;;
    --no-start)
      NO_START=true
      shift
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

log() {
  printf '\n==> %s\n' "$*"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
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
START_INDEX_JOB="${START_INDEX_JOB:-true}"
if [[ "$NO_START" == true ]]; then
  START_INDEX_JOB=false
fi

for name in \
  SUBSCRIPTION_ID NAME_SUFFIX STORAGE_ACCOUNT STORAGE_SUBSCRIPTION_ID STORAGE_RESOURCE_GROUP \
  BLOB_CONTAINER RESOURCE_GROUP LOCATION SEARCH_LOCATION FOUNDRY_LOCATION \
  STATE_BLOB NORMALIZED_BLOB_PREFIX EMBEDDING_CACHE_BLOB; do
  require_value "$name"
done

[[ "${CONFIRM_BILLABLE_DEPLOYMENT:-}" == "yes" ]] || fail \
  "Set CONFIRM_BILLABLE_DEPLOYMENT=yes after reviewing the billable Azure resources."
[[ -n "${AZURE_STORAGE_SAS_TOKEN:-}" ]] || fail \
  "Export AZURE_STORAGE_SAS_TOKEN. It is used only by Cloud Shell to verify inputs and store Terraform state."
[[ "$NAME_SUFFIX" =~ ^[a-z0-9]{3,12}$ ]] || fail \
  "NAME_SUFFIX must be 3-12 lowercase letters or digits."
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

log "Verifying the two required normalized Blob inputs before creating billable resources"
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
export TF_VAR_image_tag="$CURRENT_IMAGE_TAG"

# Optional config.env overrides map directly to Terraform variables.
declare -A TF_OVERRIDES=(
  [SEARCH_SKU]=search_sku
  [SEARCH_PARTITIONS]=search_partition_count
  [SEARCH_REPLICAS]=search_replica_count
  [SEARCH_SEMANTIC_PLAN]=search_semantic_sku
  [SEARCH_INDEX]=search_index_name
  [SEARCH_VECTOR_FIELD]=search_vector_field
  [SEARCH_SEMANTIC_CONFIGURATION]=search_semantic_configuration
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

PREVIOUS_IMAGE_TAG="$(terraform -chdir="$TERRAFORM_DIR" output -raw deployed_image_tag 2>/dev/null || true)"
if terraform -chdir="$TERRAFORM_DIR" state list 2>/dev/null | \
    grep -Eq '^azurerm_container_app\.(ui|job)|^azurerm_container_app_job\.indexer'; then
  FOUNDATION_DEPLOY_WORKLOADS=true
  FOUNDATION_IMAGE_TAG="${PREVIOUS_IMAGE_TAG:-$CURRENT_IMAGE_TAG}"
else
  FOUNDATION_DEPLOY_WORKLOADS=false
  FOUNDATION_IMAGE_TAG="$CURRENT_IMAGE_TAG"
fi

log "Reconciling Azure infrastructure (safe to rerun)"
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve -input=false \
  -lock-timeout=5m \
  -var="deploy_workloads=$FOUNDATION_DEPLOY_WORKLOADS" \
  -var="image_tag=$FOUNDATION_IMAGE_TAG"

ACR_NAME="$(terraform -chdir="$TERRAFORM_DIR" output -raw container_registry_name)"
ACR_LOGIN_SERVER="$(terraform -chdir="$TERRAFORM_DIR" output -raw container_registry_login_server)"
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
  az acr build \
    --registry "$ACR_NAME" \
    --image "$image" \
    --file "$dockerfile" \
    --no-wait \
    --no-logs \
    "$REPOSITORY_ROOT" \
    --only-show-errors \
    --output none
  return 0
}

QUEUED=false
queue_build "$UI_IMAGE" "ui/deploy/containers/ui.Dockerfile" && QUEUED=true
queue_build "$INDEXER_IMAGE" "ui/deploy/containers/indexer.Dockerfile" && QUEUED=true

if [[ "$QUEUED" == true ]] || ! image_exists "$UI_IMAGE" || ! image_exists "$INDEXER_IMAGE"; then
  log "ACR is building the images independently of this Cloud Shell session"
  az acr task list-runs \
    --registry "$ACR_NAME" \
    --top 5 \
    --query '[].{Image:outputImages[0],Status:status,Started:startTime}' \
    --output table \
    --only-show-errors || true
  echo
  echo "Exit Cloud Shell if needed. Run this same command again after the builds finish."
  exit 0
fi

log "Deploying the UI and indexing job from completed ACR images"
terraform -chdir="$TERRAFORM_DIR" apply -auto-approve -input=false \
  -lock-timeout=5m \
  -var="deploy_workloads=true" \
  -var="image_tag=$CURRENT_IMAGE_TAG"

JOB_NAME="$(terraform -chdir="$TERRAFORM_DIR" output -raw index_job_name)"
UI_URL="$(terraform -chdir="$TERRAFORM_DIR" output -raw ui_url)"

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
    if [[ "${START_INDEX_JOB,,}" == "true" ]]; then
      log "Starting the resumable Azure indexing job"
      [[ -n "$LATEST_STATUS" ]] && echo "Previous execution: $LATEST_NAME ($LATEST_STATUS)"
      az containerapp job start --name "$JOB_NAME" --resource-group "$RESOURCE_GROUP" \
        --no-wait --output none --only-show-errors
      echo "The job now runs in Azure and survives Cloud Shell disconnects. Rerun this script to check it."
    else
      echo "Index job was not started because START_INDEX_JOB=false or --no-start was supplied."
    fi
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
