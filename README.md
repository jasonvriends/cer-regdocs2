# REGDOCS Atlas

REGDOCS Atlas collects public Canada Energy Regulator (CER) REGDOCS records, turns them into structured evidence, publishes them to Azure, and provides a web research workbench for search, source verification, and grounded Microsoft Foundry answers.

The project has **six pipeline stages. Stage 6 is the final data-processing stage.** There is no Stage 7.

If you are operating an existing deployment, start with:

```bash
./ui/deploy/deploy.sh
```

With no arguments, `deploy.sh` is read-only. It inspects the configuration/deployment it can see and tells you the next command.

For the finite v1 acceptance gate, see **[COMPLETION.md](COMPLETION.md)**.

---

## What the finished system looks like

```text
CER REGDOCS
    │
    ▼
1. SCOUT       discover registry records and preserve source evidence
    │
    ▼
2. DOWNLOAD    download source files
    │
    ▼
3. ANALYZE     extract document structure/text with Azure or Docling
    │
    ▼
4. NORMALIZE   create one consistent five-file JSONL package
    │
    ├─────────────────────────────────────────────┐
    ▼                                             ▼
5. INDEX                                     6. ENRICH
Azure AI Search                             deterministic + Foundry
regdocs-chunks-hybrid                       regulatory intelligence
    │                                             │
    │                                      ┌──────┼───────────────┐
    │                                      ▼      ▼       ▼       ▼
    │                                  entities relations events claims obligations
    │                                             │
    └──────────────────────┬──────────────────────┘
                           ▼
                    REGDOCS Atlas UI
                    Azure Container Apps
                           │
          ┌────────────────┼──────────────────────────┐
          ▼                ▼                          ▼
      grounded Ask    HTML source viewer     timeline / graph /
      + citations     + Shelf evidence       claims / obligations
```

The authoritative source remains CER REGDOCS. Atlas is an evidence/research layer over those public records.

---

# The six stages

| Stage | Name | Purpose | Typical runtime | Can incur Azure cost? |
|---|---|---|---|---:|
| 1 | Scout | Discover REGDOCS records and preserve registry evidence | Local | No |
| 2 | Download | Download eligible source files | Local | No |
| 3 | Analyze | Extract text, pages, tables, figures, and structure | Local controller / Azure or Docling | Azure path can |
| 4 | Normalize | Convert analyzer output into the canonical JSONL contract | Local | No |
| 5 | Index | Create embeddings and publish the searchable corpus | Azure Container Apps job | Yes |
| 6 | Enrich | Build/publish evidence-backed regulatory intelligence using deterministic logic + Microsoft Foundry | Azure Container Apps job | Yes |

Stage 6 publishes:

```text
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

After Stage 6, the remaining work is deployment verification and normal use of the application—not another pipeline stage.

---

# What the v1 web application does

The current v1 UI implements:

- natural-language grounded Ask;
- keyword, hybrid vector, and optional semantic retrieval;
- company/project/filing/document/content-type filters;
- separate retrieved evidence and final cited evidence;
- an answer footer proving whether Microsoft Foundry was used and showing retrieval/timing/coverage information;
- normalized HTML document viewing with page jumps and evidence highlighting;
- text/table/extracted-figure rendering;
- original REGDOCS source links when available;
- a Shelf for saving source passages;
- Shelf-only questions;
- Shelf CSV export;
- regulatory timeline;
- relationship graph;
- Findings & claims;
- Commitments & obligations;
- live corpus coverage;
- protected live diagnostics;
- `ATLAS-...` error references and Log Analytics lookup.

The UI does not advertise unfinished arbitrary dataset generation or an embedded PDF viewer.

See:

- **[ui/PRODUCT.md](ui/PRODUCT.md)** — v1 capability contract
- **[ui/DATA-CONTRACT.md](ui/DATA-CONTRACT.md)** — exact feature-to-data contract
- **[ui/OPERATIONS.md](ui/OPERATIONS.md)** — operator commands

---

# Document viewing

Atlas's v1 document viewer is an accessible **HTML reconstruction of the normalized/indexed document**, not an embedded PDF.

Stage 4 creates ordered chunks with document IDs and page ranges. Stage 5 publishes those fields to Azure AI Search. When a user opens a source, Atlas retrieves all chunks for that `document_id` in `chunk_index` order and renders them as document pages.

The reader supports:

```text
text
headings
page grouping
page jump
selected-passage highlighting
tables as HTML
extracted figure text
add-to-Shelf
Original in REGDOCS link
```

The viewer does not require a duplicate PDF upload to Azure. When authoritative visual/layout comparison is needed, use **Original in REGDOCS**.

---

# Important local-data safety rule

Protect these local artifacts:

```text
workspace/1_scout/
workspace/2_download/
workspace/3_analyze/
database/regdocs.db
```

Stage 3 Azure analysis can be expensive to reproduce.

Do **not** run this in a working checkout unless you deliberately intend to remove ignored workspace/database files:

```bash
git clean -fdx
```

Stages 4–6 are designed to be recreated from earlier durable results:

```text
workspace/4_normalize/
workspace/5_index/
workspace/6_enrich/
```

---

# Local installation

Examples below assume Linux/WSL. Run them from the repository root.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r regdocs_atlas/requirements.txt

python pipeline.py version
python pipeline.py status
```

Windows PowerShell activation:

```powershell
.venv\Scripts\Activate.ps1
```

For every local command and option, see **[SYNTAX.md](SYNTAX.md)**.

---

# Local pipeline workflow: Stages 1–4

## Stage 1 — Scout

Check coverage:

```bash
python pipeline.py scout coverage
python pipeline.py scout status
```

Run a date range:

```bash
python pipeline.py scout run \
  --start-date 2026-08-01 \
  --end-date 2026-08-17
```

Use `probe` for a small non-production selection first when needed.

## Stage 2 — Download

```bash
python pipeline.py download plan
python pipeline.py download run
```

## Stage 3 — Analyze

Azure Content Understanding is the billable analyzer path:

```bash
python pipeline.py analyze azure plan --all
python pipeline.py analyze azure run --all
```

Docling is the local alternative:

```bash
python pipeline.py analyze docling status
python pipeline.py analyze docling run --max-documents 100
```

Both analyzer paths isolate document work so a single problematic file is less likely to kill a large batch.

## Stage 4 — Normalize

Preview first:

```bash
python pipeline.py normalize plan --provider azure
```

Build the canonical package:

```bash
python pipeline.py normalize run --provider azure
```

Or use Docling output:

```bash
python pipeline.py normalize run --provider docling
```

A real Normalize run replaces the canonical Stage 4 corpus with the selected run output. Do not use a small `--limit` against the canonical output directory merely as a test. Use `plan` or a separate `--output-dir` for tests.

A complete canonical Stage 4 directory contains:

```text
documents.jsonl
pages.jsonl
chunks.jsonl
tables.jsonl
provenance.jsonl
```

---

# Move the normalized corpus to Azure

Production does **not** require uploading the entire local workspace or database.

Upload the five normalized files from your personal computer with:

```bash
export AZURE_STORAGE_SAS_TOKEN='<container-sas>'

python tools/upload_cloud_inputs.py \
  --account <storage-account> \
  --container <container>
```

The uploader validates all five files, verifies the remote sizes, and writes:

```text
workspace/4_normalize/source-package.json
```

The same Blob container also holds durable Terraform state and Stage 5/6 caches/outputs.

---

# Production deployment: use the guide

The production deployment is **Azure Container Apps**, Azure Container Registry, Azure AI Search, Microsoft Foundry, Log Analytics, managed identities, and Terraform.

It is not Azure App Service.

From Cloud Shell:

```bash
cd ~/cer-regdocs2
git checkout master
git pull origin master

cp ui/deploy/config.env.example ui/deploy/config.env   # first time only
# edit ui/deploy/config.env

source ui/deploy/config.env
read -rsp "Paste container SAS: " AZURE_STORAGE_SAS_TOKEN; echo
export AZURE_STORAGE_SAS_TOKEN="${AZURE_STORAGE_SAS_TOKEN#\?}"

./ui/deploy/deploy.sh
```

Running `deploy.sh` without arguments is safe/read-only. Follow the exact **NEXT** command it prints.

The normal first-deployment flow is approximately:

```text
personal computer  finish Stage 4
personal computer  upload five-file normalized package
Cloud Shell        --check-data
Cloud Shell        --validate
Cloud Shell        --plan
Cloud Shell        --infra-only / --full as the guide directs
Azure              Stage 5 publication
Azure              Stage 6 intelligence publication
Cloud Shell        --status
browser            /diagnostics
```

Full walkthrough: **[ui/deploy/README-FIRST-DEPLOYMENT.md](ui/deploy/README-FIRST-DEPLOYMENT.md)**.

---

# Deployment command reference

```bash
# Read-only guide; start here.
./ui/deploy/deploy.sh

# Verify cloud Stage 4 inputs.
./ui/deploy/deploy.sh --check-data

# Local code/Terraform/UI validation. No Azure changes.
./ui/deploy/deploy.sh --validate

# Preview Terraform changes using remote state.
./ui/deploy/deploy.sh --plan

# Terraform/RBAC/config only.
./ui/deploy/deploy.sh --infra-only

# UI/API only; does not start Stage 5 or Stage 6.
./ui/deploy/deploy.sh --ui-only

# Explicit full infrastructure/workload deployment.
./ui/deploy/deploy.sh --full

# Force Stage 5 publication.
./ui/deploy/deploy.sh --restart-index

# Start/resume Stage 6 Foundry intelligence publication.
./ui/deploy/deploy.sh --restart-intelligence

# Read-only deployment/job/log status.
./ui/deploy/deploy.sh --status

# Read-only server-error lookup.
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

The deployment intentionally uses no GitHub Actions workflow. Validation is explicit through `deploy.sh --validate` and Terraform `--plan`.

---

# Stable Azure names and Terraform state

`NAME_SUFFIX` is a stable installation ID. Choose it once and keep using it with the same Terraform state.

Do not create a new suffix for every release.

Remote Terraform state normally lives at:

```text
terraform/regdocs-atlas.tfstate
```

in the configured Blob container.

The globally named ACR, Azure AI Search service, and Microsoft Foundry account use Terraform `prevent_destroy` protection. If Terraform proposes replacing one of those, investigate the plan instead of choosing a new suffix.

The production-safe embedding batch value is:

```text
EMBEDDING_BATCH_SIZE="32"
```

The deployment also caps an older local value above 32 down to 32.

---

# Stage 5 — production Search publication

In the production cloud workflow, Stage 5 runs as a Container Apps job and reads:

```text
workspace/4_normalize/chunks.jsonl
workspace/4_normalize/provenance.jsonl
```

It creates the hybrid Search corpus used by:

```text
Ask
keyword/hybrid/semantic retrieval
source cards
document viewer
Shelf evidence
coverage
```

Start/restart it only when publication is required:

```bash
./ui/deploy/deploy.sh --restart-index
```

Watch it with:

```bash
./ui/deploy/deploy.sh --status
```

Local `pipeline.py index ...` commands remain useful for development/inspection; the Container Apps job is the canonical production publication path.

---

# Stage 6 — final data-processing stage

Stage 6 runs as its own resumable Container Apps job and reads normalized documents/chunks from Blob.

It performs:

```text
deterministic regulatory derivation
+
Microsoft Foundry structured extraction
+
evidence validation
+
five-index Azure AI Search publication
+
durable output upload to workspace/6_enrich/
```

Start it explicitly:

```bash
./ui/deploy/deploy.sh --restart-intelligence
```

The first production run can use a small pilot in `ui/deploy/config.env`:

```text
INTELLIGENCE_DOCUMENT_LIMIT="10"
```

After inspecting the results, clear the limit, reconcile configuration, and run Stage 6 for the complete corpus.

The extraction cache is durable in Blob, so completed model work can be reused after retries/restarts.

There is no manual “upload local enrich” step in the production cloud workflow.

---

# Verify the finished system

First:

```bash
./ui/deploy/deploy.sh --status
```

Then open the URL printed by the command and visit:

```text
https://<atlas-host>/diagnostics
```

Protected live diagnostics exercise:

```text
live corpus metadata
keyword Search
hybrid/vector Search
semantic ranking when configured
HTML document retrieval
Microsoft Foundry grounded inference
regdocs-entities
regdocs-relations
regdocs-events
regdocs-claims
regdocs-obligations
```

Use **[COMPLETION.md](COMPLETION.md)** for the final functional acceptance pass.

---

# Error tracing

Server faults shown to a user contain a reference like:

```text
ATLAS-0123ABCD4567EF89
```

Look it up from Cloud Shell with:

```bash
./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89
```

Or use the protected operator page:

```text
https://<atlas-host>/diagnostics/errors?errorId=ATLAS-0123ABCD4567EF89
```

Application/job output is retained in the deployment's Log Analytics workspace according to its configured retention.

---

# Repository map

```text
pipeline.py                         cautious public local CLI
regdocs_atlas/                      pipeline/runtime implementation
tools/upload_cloud_inputs.py        Stage 4 → Blob validated uploader
tools/run_cloud_indexer.py          cloud Stage 5 runner
tools/run_cloud_intelligence.py     cloud Stage 6 runner
ui/                                 Next.js REGDOCS Atlas workbench
ui/deploy/deploy.sh                 guided production deployment entry point
ui/DATA-CONTRACT.md                 UI/runtime data requirements
ui/PRODUCT.md                       v1 capability contract
ui/OPERATIONS.md                    operator runbook
COMPLETION.md                       finite v1 acceptance checklist
SYNTAX.md                           detailed local command reference
database/                           local SQLite ledger (ignored data)
workspace/                          local durable/generated artifacts (ignored data)
```

---

# Recovery

The SQLite database is useful, but important pipeline evidence is also preserved as durable artifacts.

Useful recovery commands include:

```bash
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild prepare
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db
```

Do not destroy expensive Stage 3 artifacts just because Stage 4–6 can be regenerated.

---

# Definition of done

REGDOCS Atlas v1 is complete when **[COMPLETION.md](COMPLETION.md)** passes against the production deployment.

Future improvements are a later release decision. They are not additional stages required to finish this project.
