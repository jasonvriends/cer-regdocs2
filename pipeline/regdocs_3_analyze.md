# Stage 3 Azure runbook moved

The Azure Stage 3 provider was renamed for clarity.

Use:

- [`regdocs_3_azure.py`](regdocs_3_azure.py) — durable Azure supervisor
- [`regdocs_3_azure_worker.py`](regdocs_3_azure_worker.py) — one-document Azure worker
- [`regdocs_3_azure.md`](regdocs_3_azure.md) — current Azure Stage 3 runbook

The old public command `regdocs_3_analyze.py` is no longer the Azure entry point.
Use `python pipeline/regdocs_3_azure.py ...` instead.
