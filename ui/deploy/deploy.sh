#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="${1:-$SCRIPT_DIR/config.env}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Missing deployment configuration: $CONFIG_FILE" >&2
  echo "Copy $SCRIPT_DIR/config.env.example to $SCRIPT_DIR/config.env and edit it first." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

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

normalized_location() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]'
}

require_matching_location() {
  local resource_label="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$(normalized_location "$actual")" != "$(normalized_location "$expected")" ]]; then
    fail "$resource_label already exists in $actual, but this deployment is configured for $expected. Use a new resource name or set the matching per-resource location in $CONFIG_FILE."
  fi
}

is_true() {
  [[ "${1,,}" == "true" ]]
}

retry() {
  local attempts="$1"
  local delay="$2"
  shift 2
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if "$@"; then
      return 0
    fi
    if ((attempt == attempts)); then
      return 1
    fi
    echo "Waiting for Azure to become ready (attempt $attempt/$attempts)..." >&2
    sleep "$delay"
  done
}

ensure_role() {
  local principal_id="$1"
  local principal_type="$2"
  local role="$3"
  local scope="$4"
  local existing
  existing="$(az role assignment list \
    --assignee "$principal_id" \
    --role "$role" \
    --scope "$scope" \
    --query '[0].id' \
    --output tsv 2>/dev/null || true)"
  if [[ -z "$existing" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type "$principal_type" \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

ensure_model_deployment() {
  local deployment="$1"
  local model="$2"
  local version="$3"
  local sku="$4"
  local capacity="$5"
  local actual
  if az cognitiveservices account deployment show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT" \
    --deployment-name "$deployment" \
    --output none 2>/dev/null; then
    actual="$(az cognitiveservices account deployment show \
      --resource-group "$RESOURCE_GROUP" \
      --name "$FOUNDRY_ACCOUNT" \
      --deployment-name "$deployment" \
      --query '[properties.model.name,properties.model.version] | join(`:`, @)' \
      --output tsv)"
    [[ "$actual" == "$model:$version" ]] || fail \
      "Foundry deployment $deployment is $actual, expected $model:$version. Use a new deployment name or reconcile it in Azure."
    return
  fi
  az cognitiveservices account deployment create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT" \
    --deployment-name "$deployment" \
    --model-format OpenAI \
    --model-name "$model" \
    --model-version "$version" \
    --sku-name "$sku" \
    --sku-capacity "$capacity" \
    --output none
}

blob_exists() {
  [[ "$(az storage blob exists \
    --account-name "$STORAGE_ACCOUNT" \
    --blob-endpoint "$STORAGE_BLOB_ENDPOINT" \
    --container-name "$BLOB_CONTAINER" \
    --name "$1" \
    --sas-token "$STORAGE_SAS_TOKEN" \
    --query exists \
    --output tsv \
    --only-show-errors)" == "true" ]]
}

require_blob_access() {
  local blob_name="$1"
  local metadata
  if ! metadata="$(az storage blob show \
    --account-name "$STORAGE_ACCOUNT" \
    --blob-endpoint "$STORAGE_BLOB_ENDPOINT" \
    --container-name "$BLOB_CONTAINER" \
    --name "$blob_name" \
    --sas-token "$STORAGE_SAS_TOKEN" \
    --query '[name,properties.contentLength]' \
    --output tsv \
    --only-show-errors)"; then
    fail "Cannot read required Blob input $blob_name. Its region does not need to match the deployment region; check the Storage account/container names, SAS expiry and permissions, SAS IP restrictions, and the Storage firewall."
  fi
  [[ -n "$metadata" ]] || fail "Required Blob input is missing: $blob_name"
  echo "Verified Blob input: $metadata"
}

download_blob() {
  local blob_name="$1"
  local destination="$2"
  mkdir -p "$(dirname -- "$destination")"
  az storage blob download \
    --account-name "$STORAGE_ACCOUNT" \
    --blob-endpoint "$STORAGE_BLOB_ENDPOINT" \
    --container-name "$BLOB_CONTAINER" \
    --name "$blob_name" \
    --file "$destination" \
    --sas-token "$STORAGE_SAS_TOKEN" \
    --overwrite true \
    --output none \
    --only-show-errors
}

upload_blob() {
  local source="$1"
  local blob_name="$2"
  az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --blob-endpoint "$STORAGE_BLOB_ENDPOINT" \
    --container-name "$BLOB_CONTAINER" \
    --name "$blob_name" \
    --file "$source" \
    --sas-token "$STORAGE_SAS_TOKEN" \
    --overwrite true \
    --output none \
    --only-show-errors
}

sync_embedding_cache() {
  [[ -s "$EMBEDDING_CACHE_FILE" ]] || return 0
  local snapshot="$DEPLOY_TMP_DIR/embedding-cache-upload.sqlite"
  python3 - "$EMBEDDING_CACHE_FILE" "$snapshot" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
  upload_blob "$snapshot" "$EMBEDDING_CACHE_BLOB"
}

RESOURCE_GROUP_LOCATION="${RESOURCE_GROUP_LOCATION:-${LOCATION:-}}"
SEARCH_LOCATION="${SEARCH_LOCATION:-${LOCATION:-}}"
FOUNDRY_LOCATION="${FOUNDRY_LOCATION:-${LOCATION:-}}"
APP_SERVICE_LOCATION="${APP_SERVICE_LOCATION:-${LOCATION:-}}"

for name in \
  SUBSCRIPTION_ID LOCATION RESOURCE_GROUP STORAGE_ACCOUNT BLOB_CONTAINER \
  RESOURCE_GROUP_LOCATION SEARCH_LOCATION FOUNDRY_LOCATION APP_SERVICE_LOCATION \
  NORMALIZED_BLOB_PREFIX DATABASE_BLOB EMBEDDING_CACHE_BLOB UI_PACKAGE_BLOB \
  SEARCH_SERVICE FOUNDRY_ACCOUNT FOUNDRY_PROJECT APP_SERVICE_PLAN WEB_APP \
  SEARCH_SKU SEARCH_INDEX SEARCH_VECTOR_FIELD SEARCH_SEMANTIC_CONFIGURATION \
  EMBEDDING_DEPLOYMENT EMBEDDING_MODEL EMBEDDING_MODEL_VERSION EMBEDDING_SKU \
  EMBEDDING_CAPACITY EMBEDDING_DIMENSIONS CHAT_DEPLOYMENT CHAT_MODEL \
  CHAT_MODEL_VERSION CHAT_SKU CHAT_CAPACITY APP_SERVICE_SKU NODE_RUNTIME; do
  require_value "$name"
done

[[ "${CONFIRM_BILLABLE_DEPLOYMENT:-}" == "yes" ]] || fail \
  "Set CONFIRM_BILLABLE_DEPLOYMENT=yes after reviewing the billable resources."
[[ -n "${AZURE_STORAGE_SAS_TOKEN:-}" ]] || fail \
  "Export AZURE_STORAGE_SAS_TOKEN before running. Do not save the SAS in config.env."
[[ "$STORAGE_ACCOUNT" =~ ^[a-z0-9]{3,24}$ ]] || fail \
  "STORAGE_ACCOUNT must contain 3-24 lowercase letters and digits."
[[ "$SEARCH_SERVICE" =~ ^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$ ]] || fail \
  "SEARCH_SERVICE must be 2-60 lowercase letters, digits, or internal hyphens."
[[ "$WEB_APP" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$ ]] || fail \
  "WEB_APP must be 2-60 letters, digits, or internal hyphens."
[[ -z "${PUBLISH_LIMIT:-}" || "${PUBLISH_LIMIT:-}" =~ ^[1-9][0-9]*$ ]] || fail \
  "PUBLISH_LIMIT must be blank or a positive integer."

require_command az
require_command curl
require_command node
require_command npm
require_command python3
require_command zip
NODE_MAJOR="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
[[ "$NODE_MAJOR" =~ ^[0-9]+$ && "$NODE_MAJOR" -ge 22 ]] || fail \
  "Node.js 22 or newer is required; Cloud Shell has $(node --version)."
az account show --output none >/dev/null 2>&1 || fail "Cloud Shell is not signed in to Azure."
az account set --subscription "$SUBSCRIPTION_ID"

# A leading question mark is convenient when a SAS is copied from a URL, but
# Azure CLI expects only the token. Keep it in memory and never print it.
STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
STORAGE_BLOB_ENDPOINT="${STORAGE_BLOB_ENDPOINT:-https://${STORAGE_ACCOUNT}.blob.core.windows.net}"
STORAGE_BLOB_ENDPOINT="${STORAGE_BLOB_ENDPOINT%/}"
NORMALIZED_BLOB_PREFIX="${NORMALIZED_BLOB_PREFIX%/}"

DEPLOY_TMP_DIR="$(mktemp -d -t regdocs-atlas-deploy.XXXXXXXX)"
CACHE_SYNCED="false"
cleanup() {
  local status=$?
  if [[ "${CACHE_SYNCED:-false}" != "true" && -s "${EMBEDDING_CACHE_FILE:-}" ]]; then
    echo "Saving the embedding cache to Blob Storage before exit..." >&2
    if sync_embedding_cache; then
      CACHE_SYNCED="true"
    else
      echo "WARNING: Could not save $EMBEDDING_CACHE_BLOB; preserve $EMBEDDING_CACHE_FILE before leaving Cloud Shell." >&2
    fi
  fi
  if [[ -n "${DEPLOY_TMP_DIR:-}" && -d "$DEPLOY_TMP_DIR" && "$DEPLOY_TMP_DIR" == /tmp/regdocs-atlas-deploy.* ]]; then
    rm -rf -- "$DEPLOY_TMP_DIR"
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

NORMALIZED_PATH="$DEPLOY_TMP_DIR/data/workspace/4_normalize"
EMBEDDING_CACHE_FILE="$DEPLOY_TMP_DIR/data/workspace/5_index/embedding-cache.sqlite"
VENV_PATH="$DEPLOY_TMP_DIR/venv"
UI_PACKAGE_FILE="$DEPLOY_TMP_DIR/regdocs-atlas-ui.zip"

log "Validating and downloading deployment inputs from Blob Storage"
require_blob_access "$NORMALIZED_BLOB_PREFIX/chunks.jsonl"
require_blob_access "$NORMALIZED_BLOB_PREFIX/provenance.jsonl"
if is_true "${REQUIRE_DATABASE_BLOB:-true}"; then
  require_blob_access "$DATABASE_BLOB"
fi
download_blob "$NORMALIZED_BLOB_PREFIX/chunks.jsonl" "$NORMALIZED_PATH/chunks.jsonl"
download_blob "$NORMALIZED_BLOB_PREFIX/provenance.jsonl" "$NORMALIZED_PATH/provenance.jsonl"
if blob_exists "$EMBEDDING_CACHE_BLOB"; then
  log "Restoring resumable embedding cache from Blob Storage"
  download_blob "$EMBEDDING_CACHE_BLOB" "$EMBEDDING_CACHE_FILE"
  CACHE_SYNCED="true"
else
  mkdir -p "$(dirname -- "$EMBEDDING_CACHE_FILE")"
  echo "No embedding cache was found; the first publication will create one."
fi

log "Registering Azure resource providers"
for provider in Microsoft.Search Microsoft.CognitiveServices Microsoft.Web; do
  az provider register --namespace "$provider" --wait --output none
done

log "Deployment locations: group metadata=$RESOURCE_GROUP_LOCATION; Search=$SEARCH_LOCATION; Foundry=$FOUNDRY_LOCATION; App Service=$APP_SERVICE_LOCATION"
if ! az group show --name "$RESOURCE_GROUP" --output none 2>/dev/null; then
  log "Creating deployment resource group"
  az group create \
    --name "$RESOURCE_GROUP" \
    --location "$RESOURCE_GROUP_LOCATION" \
    --tags application=regdocs-atlas managed-by=ui-deploy \
    --output none
else
  GROUP_ACTUAL_LOCATION="$(az group show --name "$RESOURCE_GROUP" --query location --output tsv)"
  echo "Reusing resource group $RESOURCE_GROUP (metadata location: $GROUP_ACTUAL_LOCATION)."
fi

log "Creating Azure AI Search"
if ! az search service show --resource-group "$RESOURCE_GROUP" --name "$SEARCH_SERVICE" --output none 2>/dev/null; then
  az search service create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SEARCH_SERVICE" \
    --location "$SEARCH_LOCATION" \
    --sku "$SEARCH_SKU" \
    --partition-count "$SEARCH_PARTITIONS" \
    --replica-count "$SEARCH_REPLICAS" \
    --semantic-search "$SEARCH_SEMANTIC_PLAN" \
    --identity-type SystemAssigned \
    --auth-options aadOrApiKey \
    --aad-auth-failure-mode http401WithBearerChallenge \
    --public-network-access enabled \
    --output none
else
  SEARCH_ACTUAL_LOCATION="$(az search service show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SEARCH_SERVICE" \
    --query location \
    --output tsv)"
  require_matching_location "Azure AI Search service $SEARCH_SERVICE" "$SEARCH_LOCATION" "$SEARCH_ACTUAL_LOCATION"
  az search service update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SEARCH_SERVICE" \
    --identity-type SystemAssigned \
    --semantic-search "$SEARCH_SEMANTIC_PLAN" \
    --auth-options aadOrApiKey \
    --aad-auth-failure-mode http401WithBearerChallenge \
    --output none
fi
SEARCH_ID="$(az search service show --resource-group "$RESOURCE_GROUP" --name "$SEARCH_SERVICE" --query id --output tsv)"
SEARCH_PRINCIPAL_ID="$(az search service show --resource-group "$RESOURCE_GROUP" --name "$SEARCH_SERVICE" --query identity.principalId --output tsv)"
SEARCH_ENDPOINT="https://${SEARCH_SERVICE}.search.windows.net"

log "Creating Microsoft Foundry resource, project, and model deployments"
if ! az cognitiveservices account show --resource-group "$RESOURCE_GROUP" --name "$FOUNDRY_ACCOUNT" --output none 2>/dev/null; then
  az cognitiveservices account create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT" \
    --location "$FOUNDRY_LOCATION" \
    --kind AIServices \
    --sku S0 \
    --custom-domain "$FOUNDRY_ACCOUNT" \
    --allow-project-management true \
    --assign-identity \
    --yes \
    --output none
else
  FOUNDRY_ACTUAL_LOCATION="$(az cognitiveservices account show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT" \
    --query location \
    --output tsv)"
  require_matching_location "Foundry account $FOUNDRY_ACCOUNT" "$FOUNDRY_LOCATION" "$FOUNDRY_ACTUAL_LOCATION"
fi
if ! az cognitiveservices account project show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT" \
  --project-name "$FOUNDRY_PROJECT" \
  --output none 2>/dev/null; then
  az cognitiveservices account project create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT" \
    --project-name "$FOUNDRY_PROJECT" \
    --location "$FOUNDRY_LOCATION" \
    --output none
fi
ensure_model_deployment \
  "$EMBEDDING_DEPLOYMENT" "$EMBEDDING_MODEL" "$EMBEDDING_MODEL_VERSION" \
  "$EMBEDDING_SKU" "$EMBEDDING_CAPACITY"
ensure_model_deployment \
  "$CHAT_DEPLOYMENT" "$CHAT_MODEL" "$CHAT_MODEL_VERSION" \
  "$CHAT_SKU" "$CHAT_CAPACITY"
FOUNDRY_ID="$(az cognitiveservices account show --resource-group "$RESOURCE_GROUP" --name "$FOUNDRY_ACCOUNT" --query id --output tsv)"
FOUNDRY_PROJECT_ID="$(az cognitiveservices account project show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT" \
  --project-name "$FOUNDRY_PROJECT" \
  --query id \
  --output tsv)"
FOUNDRY_OPENAI_ENDPOINT="https://${FOUNDRY_ACCOUNT}.openai.azure.com"
FOUNDRY_PROJECT_ENDPOINT="https://${FOUNDRY_ACCOUNT}.services.ai.azure.com/api/projects/${FOUNDRY_PROJECT}"

log "Creating Linux App Service"
if ! az appservice plan show --resource-group "$RESOURCE_GROUP" --name "$APP_SERVICE_PLAN" --output none 2>/dev/null; then
  az appservice plan create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_SERVICE_PLAN" \
    --location "$APP_SERVICE_LOCATION" \
    --is-linux \
    --sku "$APP_SERVICE_SKU" \
    --output none
else
  APP_PLAN_ACTUAL_LOCATION="$(az appservice plan show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_SERVICE_PLAN" \
    --query location \
    --output tsv)"
  require_matching_location "App Service plan $APP_SERVICE_PLAN" "$APP_SERVICE_LOCATION" "$APP_PLAN_ACTUAL_LOCATION"
fi
if ! az webapp show --resource-group "$RESOURCE_GROUP" --name "$WEB_APP" --output none 2>/dev/null; then
  az webapp create \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$APP_SERVICE_PLAN" \
    --name "$WEB_APP" \
    --runtime "$NODE_RUNTIME" \
    --output none
fi
WEB_PRINCIPAL_ID="$(az webapp identity assign \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEB_APP" \
  --query principalId \
  --output tsv)"
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEB_APP" \
  --linux-fx-version "$NODE_RUNTIME" \
  --startup-file "node server.js" \
  --always-on true \
  --min-tls-version 1.2 \
  --output none

log "Assigning least-privilege Azure roles"
if [[ -z "${DEPLOYER_OBJECT_ID:-}" ]]; then
  DEPLOYER_OBJECT_ID="$(az ad signed-in-user show --query id --output tsv 2>/dev/null || true)"
fi
[[ -n "$DEPLOYER_OBJECT_ID" ]] || fail \
  "Could not resolve the Cloud Shell user. Set DEPLOYER_OBJECT_ID in config.env."
DEPLOYER_PRINCIPAL_TYPE="${DEPLOYER_PRINCIPAL_TYPE:-User}"
ensure_role "$DEPLOYER_OBJECT_ID" "$DEPLOYER_PRINCIPAL_TYPE" "Search Service Contributor" "$SEARCH_ID"
ensure_role "$DEPLOYER_OBJECT_ID" "$DEPLOYER_PRINCIPAL_TYPE" "Search Index Data Contributor" "$SEARCH_ID"
ensure_role "$DEPLOYER_OBJECT_ID" "$DEPLOYER_PRINCIPAL_TYPE" "Search Index Data Reader" "$SEARCH_ID"
ensure_role "$DEPLOYER_OBJECT_ID" "$DEPLOYER_PRINCIPAL_TYPE" "Cognitive Services OpenAI User" "$FOUNDRY_ID"
ensure_role "$SEARCH_PRINCIPAL_ID" ServicePrincipal "Cognitive Services OpenAI User" "$FOUNDRY_ID"
ensure_role "$WEB_PRINCIPAL_ID" ServicePrincipal "Search Index Data Reader" "$SEARCH_ID"
ensure_role "$WEB_PRINCIPAL_ID" ServicePrincipal "Cognitive Services OpenAI User" "$FOUNDRY_ID"
# Foundry User role ID. The display name is currently being renamed from
# Azure AI User, so the stable ID avoids tenant-to-tenant naming differences.
ensure_role "$WEB_PRINCIPAL_ID" ServicePrincipal \
  "53ca6127-db72-4b80-b1b0-d745d6d5456d" "$FOUNDRY_PROJECT_ID"

log "Preparing Python deployment environment"
python3 -m venv "$VENV_PATH"
"$VENV_PATH/bin/python" -m pip install \
  --requirement "$REPOSITORY_ROOT/regdocs_atlas/requirements-deploy.txt"

export AZURE_SEARCH_ENDPOINT="$SEARCH_ENDPOINT"
export AZURE_SEARCH_HYBRID_INDEX="$SEARCH_INDEX"
export AZURE_SEARCH_VECTOR_FIELD="$SEARCH_VECTOR_FIELD"
export AZURE_SEARCH_SEMANTIC_CONFIGURATION="$SEARCH_SEMANTIC_CONFIGURATION"
export AZURE_OPENAI_ENDPOINT="$FOUNDRY_OPENAI_ENDPOINT"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT="$EMBEDDING_DEPLOYMENT"
export AZURE_OPENAI_EMBEDDING_MODEL="$EMBEDDING_MODEL"
export AZURE_OPENAI_EMBEDDING_DIMENSIONS="$EMBEDDING_DIMENSIONS"

if is_true "${PUBLISH_INDEX:-true}"; then
  log "Publishing hybrid Search index from Blob-downloaded normalized data"
  CACHE_SYNCED="false"
  publish_args=(
    "$REPOSITORY_ROOT/tools/publish_hybrid_index.py"
    --normalized-dir "$NORMALIZED_PATH"
    --cache-db "$EMBEDDING_CACHE_FILE"
  )
  if [[ -n "${PUBLISH_LIMIT:-}" ]]; then
    publish_args+=(--limit "$PUBLISH_LIMIT")
  fi
  if is_true "${RECREATE_INDEX:-false}"; then
    publish_args+=(--recreate-index)
  fi
  retry 20 15 "$VENV_PATH/bin/python" "${publish_args[@]}" || fail \
    "Index publication failed after waiting for Azure role propagation."

  log "Saving resumable embedding cache to Blob Storage"
  sync_embedding_cache
  CACHE_SYNCED="true"

  verify_args=(
    "$REPOSITORY_ROOT/tools/verify_ai_deployment.py"
    --normalized-dir "$NORMALIZED_PATH"
  )
  if [[ -n "${PUBLISH_LIMIT:-}" ]]; then
    verify_args+=(--expected-count "$PUBLISH_LIMIT")
  fi
  "$VENV_PATH/bin/python" "${verify_args[@]}"
fi

az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEB_APP" \
  --settings \
    AZURE_SEARCH_ENDPOINT="$SEARCH_ENDPOINT" \
    AZURE_SEARCH_INDEX="$SEARCH_INDEX" \
    AZURE_SEARCH_VECTOR_FIELD="$SEARCH_VECTOR_FIELD" \
    AZURE_SEARCH_SEMANTIC_CONFIGURATION="$SEARCH_SEMANTIC_CONFIGURATION" \
    FOUNDRY_PROJECT_ENDPOINT="$FOUNDRY_PROJECT_ENDPOINT" \
    FOUNDRY_MODEL_DEPLOYMENT="$CHAT_DEPLOYMENT" \
    SCM_DO_BUILD_DURING_DEPLOYMENT=false \
    HOSTNAME=0.0.0.0 \
  --output none

if is_true "${DEPLOY_UI:-true}"; then
  log "Building the Next.js UI in Cloud Shell"
  (
    cd "$REPOSITORY_ROOT/ui"
    npm ci
    npm run typecheck
    npm run build
  )
  APP_PACKAGE_DIR="$DEPLOY_TMP_DIR/app"
  mkdir -p "$APP_PACKAGE_DIR/.next"
  cp -R "$REPOSITORY_ROOT/ui/.next/standalone/." "$APP_PACKAGE_DIR/"
  cp -R "$REPOSITORY_ROOT/ui/.next/static" "$APP_PACKAGE_DIR/.next/static"
  if [[ -d "$REPOSITORY_ROOT/ui/public" ]]; then
    cp -R "$REPOSITORY_ROOT/ui/public" "$APP_PACKAGE_DIR/public"
  fi
  (
    cd "$APP_PACKAGE_DIR"
    zip -q -r "$UI_PACKAGE_FILE" .
  )

  log "Uploading the UI ZIP to the input Blob container"
  upload_blob "$UI_PACKAGE_FILE" "$UI_PACKAGE_BLOB"
  UI_PACKAGE_URL="${STORAGE_BLOB_ENDPOINT}/${BLOB_CONTAINER}/${UI_PACKAGE_BLOB}?${STORAGE_SAS_TOKEN}"

  log "Starting App Service deployment from the Blob-hosted ZIP"
  az webapp deploy \
    --resource-group "$RESOURCE_GROUP" \
    --name "$WEB_APP" \
    --src-url "$UI_PACKAGE_URL" \
    --type zip \
    --async true \
    --clean true \
    --restart true \
    --output none
fi

APP_URL="https://${WEB_APP}.azurewebsites.net"
log "Checking deployed application"
retry 40 15 curl --fail --silent --show-error "$APP_URL/api/health" >/dev/null || fail \
  "Deployment completed, but the health endpoint is not ready: $APP_URL/api/health"

cat <<EOF

REGDOCS Atlas deployment completed.

Application:      $APP_URL
Health:           $APP_URL/api/health
Search endpoint:  $SEARCH_ENDPOINT
Search index:     $SEARCH_INDEX
Foundry project:  $FOUNDRY_PROJECT_ENDPOINT
Input container:  ${STORAGE_BLOB_ENDPOINT}/${BLOB_CONTAINER}
UI ZIP blob:      $UI_PACKAGE_BLOB

Rerun this same command with a valid SAS to resume publication or update the UI.
EOF
