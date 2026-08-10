#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <resource-group> <app-name> <search-resource-group> <search-service-name> [index-name] [location]" >&2
  echo "Example: $0 regdocs-atlas regdocs-atlas-ui regdocs-data my-search-service regdocs-chunks canadacentral" >&2
}

if [[ $# -lt 4 || $# -gt 6 ]]; then
  usage
  exit 2
fi

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Run this script in Azure Cloud Shell or install Azure CLI first." >&2
  exit 1
fi

for required_command in node npm zip; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required command is missing: ${required_command}" >&2
    exit 1
  fi
done

node_major=$(node --version | sed -E 's/^v([0-9]+).*/\1/')
if [[ ! "$node_major" =~ ^[0-9]+$ || "$node_major" -lt 22 ]]; then
  echo "Node.js 22 or newer is required; found $(node --version)." >&2
  exit 1
fi

resource_group=$1
app_name=$2
search_resource_group=$3
search_service_name=$4
index_name=${5:-regdocs-chunks}
location=${6:-canadacentral}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ui_dir=$(cd -- "${script_dir}/.." && pwd)
deploy_tmp=$(mktemp -d)
trap 'rm -rf -- "$deploy_tmp"' EXIT

if ! az account show >/dev/null 2>&1; then
  echo "Sign in first with: az login" >&2
  exit 1
fi

local_auth_disabled=$(az search service show \
  --resource-group "$search_resource_group" \
  --name "$search_service_name" \
  --query disableLocalAuth \
  --output tsv)
aad_failure_mode=$(az search service show \
  --resource-group "$search_resource_group" \
  --name "$search_service_name" \
  --query authOptions.aadOrApiKey.aadAuthFailureMode \
  --output tsv)

if [[ "${local_auth_disabled,,}" != "true" && -z "$aad_failure_mode" ]]; then
  echo "Enabling Azure AI Search authentication with both API keys and managed identities..."
  az search service update \
    --resource-group "$search_resource_group" \
    --name "$search_service_name" \
    --aad-auth-failure-mode http401WithBearerChallenge \
    --auth-options aadOrApiKey \
    --output none
fi

echo "Creating or updating Azure resources..."
az group create \
  --name "$resource_group" \
  --location "$location" \
  --output none

app_url=$(az deployment group create \
  --resource-group "$resource_group" \
  --template-file "${script_dir}/main.bicep" \
  --parameters \
    appName="$app_name" \
    location="$location" \
    searchResourceGroupName="$search_resource_group" \
    searchServiceName="$search_service_name" \
    searchIndexName="$index_name" \
  --query properties.outputs.appUrl.value \
  --output tsv)

echo "Building the tested standalone Next.js artifact..."
(
  cd "$ui_dir"
  npm ci
  npm run typecheck
  npm run build
)

mkdir -p "${deploy_tmp}/app/.next"
cp -R "${ui_dir}/.next/standalone/." "${deploy_tmp}/app/"
cp -R "${ui_dir}/.next/static" "${deploy_tmp}/app/.next/static"
if [[ -d "${ui_dir}/public" ]]; then
  cp -R "${ui_dir}/public" "${deploy_tmp}/app/public"
fi

(
  cd "${deploy_tmp}/app"
  zip -qr "${deploy_tmp}/regdocs-atlas-ui.zip" .
)

echo "Deploying to Azure App Service..."
az webapp deploy \
  --resource-group "$resource_group" \
  --name "$app_name" \
  --src-path "${deploy_tmp}/regdocs-atlas-ui.zip" \
  --type zip \
  --clean true \
  --output none

az webapp restart \
  --resource-group "$resource_group" \
  --name "$app_name" \
  --output none

echo "Deployment complete: ${app_url}"
echo "Health check: ${app_url}/api/health"
echo "Search check: ${app_url}/api/search?q=pipeline&top=5"
echo "The managed-identity role can take several minutes to become active after the first deployment."
