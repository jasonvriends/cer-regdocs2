# Blob-to-Cloud-Shell Azure deployment

This workflow separates data transfer from Azure provisioning:

1. Create one private Storage account and Blob container.
2. On the computer holding the corpus, upload `workspace/` and a consistent
   `database/regdocs.db` snapshot with a container SAS.
3. Open Azure Cloud Shell, clone or upload this repository, export the same SAS,
   and run `ui/deploy/deploy.sh`.

The deployment script reads the two normalized Stage 4 files from Blob,
restores the embedding cache when one exists, and then provisions Azure AI
Search, Microsoft Foundry, model deployments, App Service, managed identities,
and RBAC. It publishes and verifies the hybrid index, builds the UI, uploads the
deployable ZIP to the same container, and tells App Service to pull that ZIP by
SAS URL.

The database is retained in Blob as the pipeline ledger and verified by the
deployment, but the web app does not download or query it.

The Storage account does not need to share a region or resource group with the
deployment resources. A SAS authorizes the configured account and container by
URL. Fresh deployments default to East US 2 because the configured embedding
and chat model offers are available there; `RESOURCE_GROUP_LOCATION`,
`SEARCH_LOCATION`, `FOUNDRY_LOCATION`, and `APP_SERVICE_LOCATION` can override
that default independently. The deployer validates the location of any reused
Search, Foundry, or App Service Plan resource and stops with a clear error when
an existing resource cannot satisfy the configured location.

## 1. Create Storage and a container SAS

Create the Storage account and private container before running this script.
Keep anonymous access disabled. Generate a container SAS with these permissions:

- Read
- Add
- Create
- Write
- List

The SAS must remain valid for the entire upload and Cloud Shell deployment. A
full index publication can take hours, so allow a suitable margin and require
HTTPS. App Service itself pulls the ZIP, so do not restrict the SAS to only the
data computer or Cloud Shell IP. If the Storage account firewall limits public
network access, it must also permit the App Service/Kudu outbound path during
deployment; a SAS does not bypass the Storage firewall.

Never commit or paste the SAS into `config.env`.

## 2. Upload from the data computer

Install Azure CLI, then export the SAS without a leading `?`:

```bash
export AZURE_STORAGE_SAS_TOKEN='sv=...&sp=...&sig=...'
export REGDOCS_STORAGE_ACCOUNT='stregdocsatlasexample'
export REGDOCS_BLOB_CONTAINER='regdocs-deployment'
```

Upload the workspace while preserving the `workspace/` prefix:

```bash
az storage blob upload-batch \
  --account-name "$REGDOCS_STORAGE_ACCOUNT" \
  --destination "$REGDOCS_BLOB_CONTAINER" \
  --destination-path workspace \
  --source workspace \
  --sas-token "$AZURE_STORAGE_SAS_TOKEN" \
  --overwrite true
```

Create a consistent SQLite snapshot before uploading. This matters when the
source database uses WAL mode or another pipeline process has it open:

```bash
REGDOCS_DB_UPLOAD_DIR="$(mktemp -d -t regdocs-db-upload.XXXXXXXX)"
python3 - database/regdocs.db "$REGDOCS_DB_UPLOAD_DIR/regdocs.db" <<'PY'
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

az storage blob upload \
  --account-name "$REGDOCS_STORAGE_ACCOUNT" \
  --container-name "$REGDOCS_BLOB_CONTAINER" \
  --name database/regdocs.db \
  --file "$REGDOCS_DB_UPLOAD_DIR/regdocs.db" \
  --sas-token "$AZURE_STORAGE_SAS_TOKEN" \
  --overwrite true
```

The deployer specifically consumes:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
database/regdocs.db
workspace/5_index/embedding-cache.sqlite    optional on the first run
```

Uploading the rest of `workspace/` preserves the expensive Stage 1-3 artifacts
but does not cause Cloud Shell to download them.

## 3. Run from Azure Cloud Shell

In Cloud Shell, obtain this repository and enter the deployment directory:

```bash
git clone <repository-url> cer-regdocs2
cd cer-regdocs2/ui/deploy
cp config.env.example config.env
```

Edit `config.env`. Set the subscription ID, the existing Storage account and
container, globally unique Search/Foundry/App names, offered model versions and
capacity, and `CONFIRM_BILLABLE_DEPLOYMENT=yes`. If Storage remains in Canada
Central while compute is deployed elsewhere, keep its existing account and
container values; no Storage location setting is required.

Cloud Shell must have Python 3 with `venv`, `zip`, and Node.js 22 or newer. If
its Node version is older and `nvm` is available, run `nvm install 22` first.

Export the SAS in Cloud Shell and deploy:

```bash
export AZURE_STORAGE_SAS_TOKEN='sv=...&sp=...&sig=...'
chmod +x deploy.sh
./deploy.sh
```

You can keep the non-secret configuration elsewhere and pass it explicitly:

```bash
./deploy.sh /path/to/regdocs-production.env
```

## Resuming and pilot runs

The script uploads `workspace/5_index/embedding-cache.sqlite` after publication
and also attempts to upload it when the script exits after an error or
interrupt. It uses SQLite's backup API so the uploaded cache is consistent even
when WAL files were involved. Rerun the same command to resume. Keep the SAS
valid until that cache and `deploy/regdocs-atlas-ui.zip` have been written.

For a lower-cost pilot, set `PUBLISH_LIMIT="100"`. Return it to blank before the
production run. Keep the same embedding deployment and dimensions between the
pilot and full publication.

`RECREATE_INDEX=true` deletes and recreates the configured Search index. It is
off by default.

## Security notes

- The SAS remains an environment variable and is never stored in App Service.
- The generated App Service deployment command uses the SAS URL only for the
  immediate ZIP pull from the private container.
- Search and Foundry runtime access use managed identities rather than keys.
- `config.env` is ignored by Git and should not contain secrets.
