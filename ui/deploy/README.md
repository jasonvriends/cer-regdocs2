# Resumable Azure deployment from Cloud Shell

This deployment is designed for the following constraint: interactive Azure
login works only in Azure Cloud Shell opened on a company-managed computer, and
Cloud Shell can disconnect before a 200,000+ chunk index finishes.

Cloud Shell is therefore only the control terminal. It runs short, idempotent
Terraform and Azure CLI phases. Azure Container Registry builds the images, and
an Azure Container Apps job performs the long index publication independently
of the Cloud Shell session.

No VM or container runs `az login`. The UI and indexer use user-assigned managed
identities for Azure-to-Azure authentication.

## Ownership boundary

Terraform creates and manages a new resource group containing:

- Azure AI Search
- a Microsoft Foundry resource, project, and two model deployments
- Azure Container Registry
- a Container Apps environment, public UI app, and manual indexing job
- Log Analytics, managed identities, and least-privilege role assignments

The existing Storage account and Blob container are **not Terraform resources**.
Terraform constructs their Azure resource ID only for the indexer's Blob role
assignment; it does not import, read keys from, modify, or delete them. They can
remain in Canada Central while the new workload runs in East US 2.

The indexer reads only:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
workspace/5_index/embedding-cache.sqlite    optional on the first run
```

It periodically writes a consistent embedding-cache checkpoint back to the
third path. `database/regdocs.db` remains safely stored as the pipeline ledger,
but neither Search indexing nor the UI needs SQLite.

## Why rerunning is safe

Terraform state lives at `terraform/regdocs-atlas.tfstate` in the existing Blob
container and uses Azure Blob leasing for locking. Each run reconciles the same
resources rather than creating copies.

Images use the current Git commit as an immutable tag. The script:

1. reuses an image if its tag exists;
2. detects an ACR build already queued or running;
3. queues missing builds with `--no-wait` and exits;
4. deploys workloads only after both images exist;
5. does not start another index execution while one is running;
6. reuses a successful execution unless `--restart-index` is explicit.

Search documents use stable keys and `merge_or_upload`, so retrying a failed job
updates existing chunks rather than duplicating them. The remote embedding cache
avoids paying to regenerate vectors already completed by an earlier execution.

## One-time Cloud Shell setup

Clone the repository and create the ignored configuration file:

```bash
git clone <repository-url> cer-regdocs2
cd cer-regdocs2
cp ui/deploy/config.env.example ui/deploy/config.env
code ui/deploy/config.env
```

At minimum, set:

- `SUBSCRIPTION_ID`
- a unique `NAME_SUFFIX`
- `STORAGE_ACCOUNT`
- `STORAGE_RESOURCE_GROUP`
- `BLOB_CONTAINER`
- `CONFIRM_BILLABLE_DEPLOYMENT=yes`

The defaults use East US 2, `GlobalStandard` model deployments, 1,000,000 TPM
embedding capacity, embedding batches of 128 inputs, and Search upload batches
of up to 1,000 documents. Azure can still reject the requested capacity when
the subscription lacks quota; lower `EMBEDDING_CAPACITY` to the allocated quota
if that happens. `DataZoneStandard` can replace `GlobalStandard` if policy
requires inference to remain within the United States data zone.

Generate or reuse a private container SAS. It needs Read, Create, Write, and
List permission for input verification and Terraform state. Keep it out of
`config.env` and Git:

```bash
read -rsp "Paste the container SAS token: " AZURE_STORAGE_SAS_TOKEN
printf '\n'
AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"
export AZURE_STORAGE_SAS_TOKEN
```

Run the deployment from the repository root:

```bash
./ui/deploy/deploy.sh
```

Terraform is preinstalled in current Azure Cloud Shell images. ACR builds Node
and Python containers in Azure, so Cloud Shell needs no Node, Python packages,
Docker, or SQLite CLI.

## After a timeout or disconnect

Return to the same clone, export a valid SAS again if needed, and run the exact
same command:

```bash
cd ~/cer-regdocs2
export AZURE_STORAGE_SAS_TOKEN='sv=...&sp=...&sig=...'
./ui/deploy/deploy.sh
```

The script prints which phase is already complete and continues with the next
one. An indexing job keeps running after Cloud Shell closes. Its latest status
appears at the end of each run and in Azure Portal under the Container Apps job
named `job-regdocs-<suffix>`.

When new `chunks.jsonl` or `provenance.jsonl` files have been uploaded and the
last execution is already successful, explicitly start another publication:

```bash
./ui/deploy/deploy.sh --restart-index
```

To provision the complete deployment without starting the job:

```bash
./ui/deploy/deploy.sh --no-start
```

## Operational notes

- Standard Azure AI Search is the principal fixed monthly cost. Container Apps
  UI scales to zero when idle, while the manual index job costs only while it is
  executing. ACR Basic and Log Analytics add smaller charges.
- A 1,000,000 TPM setting is model quota, not a reservation or a promise to
  consume that volume. Embedding charges are based on processed tokens.
- The UI is available before the first index execution completes, but searches
  will not return the complete corpus until the job succeeds.
- Keep `config.env` and SAS tokens private. Terraform state is sensitive because
  it includes resource configuration and the generated Foundry safety salt.
- A SAS or managed identity does not bypass the Storage firewall. The retained
  account must permit the Container Apps job's outbound access; this template
  does not create a VNet or private endpoint for an already locked-down account.
- `terraform destroy` is intentionally not wrapped by the script. If used
  manually, it removes only Terraform-managed resources, not the existing
  Storage account or corpus blobs.
