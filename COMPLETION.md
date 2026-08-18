# REGDOCS Atlas v1 completion checklist

REGDOCS Atlas has six data-processing stages. Stage 6 is the final stage. This checklist is the finite acceptance gate for calling the project complete.

Do not add another pipeline stage to satisfy this checklist. Fix defects in the existing six-stage pipeline, deployment, or UI instead.

## A. Source pipeline

- [ ] Stage 1 Scout coverage required for the release corpus is complete.
- [ ] Stage 2 eligible source files are downloaded or intentionally excluded with evidence.
- [ ] Stage 3 selected analyzer output is complete enough for the release corpus.
- [ ] Stage 4 canonical normalization completes and produces the five-file package.
- [ ] The five Stage 4 files are uploaded with `tools/upload_cloud_inputs.py`.
- [ ] `./ui/deploy/deploy.sh --check-data` reports the cloud package complete.

## B. Code and infrastructure

- [ ] `./ui/deploy/deploy.sh --validate` succeeds.
- [ ] `./ui/deploy/deploy.sh --plan` is reviewed and does not propose an unintended destructive replacement.
- [ ] `NAME_SUFFIX` is the existing stable installation ID.
- [ ] `EMBEDDING_BATCH_SIZE=32` is in effect.
- [ ] Terraform remote state is attached to the intended Blob.
- [ ] `./ui/deploy/deploy.sh --full` completes the production infrastructure/workload update.
- [ ] No GitHub Actions workflow is required or present for deployment/verification.

## C. Stage 5 Search

- [ ] `./ui/deploy/deploy.sh --restart-index` completes successfully when a publication is required.
- [ ] The configured Stage 5 index contains the expected normalized chunks.
- [ ] Keyword retrieval works.
- [ ] Hybrid/vector retrieval works.
- [ ] Semantic reranking works when configured.
- [ ] Corpus coverage reports the live index and filing-date range.

## D. Stage 6 — final data stage

For the first production run, a small `INTELLIGENCE_DOCUMENT_LIMIT` may be used to inspect quality before the full run. Clear the limit for final corpus publication.

- [ ] `./ui/deploy/deploy.sh --restart-intelligence` completes successfully.
- [ ] `regdocs-entities` is populated.
- [ ] `regdocs-relations` is populated.
- [ ] `regdocs-events` is populated.
- [ ] `regdocs-claims` is populated.
- [ ] `regdocs-obligations` is populated.
- [ ] Stage 6 durable output is written to `workspace/6_enrich/` in Blob.
- [ ] Model-derived records retain evidence and remain marked `unreviewed` unless reviewed.

## E. Web application acceptance

Run the following against the final deployed application. This is functional acceptance, not a new pipeline stage.

- [ ] Ask returns a grounded answer from Microsoft Foundry.
- [ ] The answer shows retrieved evidence separately from final cited evidence.
- [ ] The answer footer identifies Foundry/retrieval mode and live coverage.
- [ ] Company/project/filing/document/content-type filters work.
- [ ] A cited source opens in the Atlas HTML document viewer.
- [ ] The viewer reconstructs the complete indexed document in `chunk_index` order.
- [ ] Page jump works.
- [ ] Search/citation evidence is highlighted in the document view.
- [ ] Text chunks render readably.
- [ ] Table chunks render as HTML tables when tabular structure is available.
- [ ] Figure chunks show extracted figure text.
- [ ] **Original in REGDOCS** opens the authoritative source when `source_url` exists.
- [ ] A passage can be added to the Shelf from a source card or document viewer.
- [ ] Shelf-only Ask works.
- [ ] Shelf CSV export works.
- [ ] Regulatory timeline works for a scoped document/filing/company/project.
- [ ] Relationship graph works for a scoped document/filing/company/project.
- [ ] Findings & claims works and opens supporting evidence.
- [ ] Commitments & obligations works and opens supporting evidence.
- [ ] Outstanding/deadline filters only use explicit extracted status/deadline data.
- [ ] Coverage shows live Search metadata rather than hard-coded dates.

## F. Operations acceptance

- [ ] `./ui/deploy/deploy.sh --status` shows the production URL and Stage 5/6 execution state.
- [ ] `/diagnostics` shallow configuration loads.
- [ ] Protected live diagnostics pass Search, hybrid/semantic retrieval, document retrieval, Foundry, and all five intelligence indexes.
- [ ] The diagnostics operator token is retrievable from Terraform state by an operator.
- [ ] A real server fault returns an `ATLAS-...` reference.
- [ ] `./ui/deploy/deploy.sh --error ATLAS-...` can find the corresponding Log Analytics event after ingestion.

## G. Documentation/release acceptance

- [ ] Root `README.md` describes the current six-stage architecture and Container Apps deployment.
- [ ] `ui/README.md` describes only implemented v1 UI capabilities.
- [ ] `ui/DATA-CONTRACT.md` matches the deployed Search/index schemas.
- [ ] `ui/deploy/README-FIRST-DEPLOYMENT.md` matches `deploy.sh`.
- [ ] `ui/OPERATIONS.md` matches the status/error/diagnostics commands.
- [ ] No visible UI button claims an unimplemented v1 feature.

## Complete

When A through G pass, REGDOCS Atlas v1 is complete.

Future work is a new release decision, not unfinished work in this release.
