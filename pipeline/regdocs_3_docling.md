# `regdocs_3_docling.py`

Experimental Stage 3b of REGDOCS: analyze the same verified current Stage 2
source files locally with Docling so its output can be compared with Azure
Content Understanding.

This is an alternate analysis backend, not a replacement decision.

## Install

Docling is intentionally kept out of the core requirements because its runtime
and model dependencies are much heavier:

```bash
python -m pip install -r pipeline/requirements-docling.txt
```

## Run

Start with one document or a small sample:

```bash
python pipeline/regdocs_3_docling.py --document-id 4647200
python pipeline/regdocs_3_docling.py --limit 10
```

Use `--dry-run` to inspect selection without conversion.

## Artifacts and ledger identity

Docling writes under:

```text
workspace/3_analyze/docling/
├── raw/docling-standard/<docling-version>/<document-id>/<sha256>.json
└── markdown/docling-standard/<docling-version>/<document-id>/<sha256>.md
```

The existing `analyses` table is reused with:

```text
analyzer_id = docling-standard
api_version = installed Docling package version
artifact_source = docling
```

The raw JSON preserves the complete native `DoclingDocument.export_to_dict()`
result under `regdocsDocling.native`. It also contains a conservative REGDOCS
compatibility projection under `contents[]` so the current Stage 4 normalizer
can consume Docling results while the native Docling representation remains
available for comparison and future provider-specific adapters.

The compatibility projection is intentionally experimental. It maps text,
page provenance, basic headings, and table structure into the subset of the
existing Stage 4 input contract needed for normalization. It does not claim
that Docling and Azure have equivalent native schemas or semantics.

## Stage 4 provider choice

For the initial multi-provider implementation, use the provider launcher:

```bash
python pipeline/regdocs_4_normalize_provider.py --analysis-provider azure
python pipeline/regdocs_4_normalize_provider.py --analysis-provider docling
```

All normal Stage 4 options can be passed through, for example:

```bash
python pipeline/regdocs_4_normalize_provider.py \
  --analysis-provider docling \
  --document-id 4647200 \
  --output-dir workspace/4_normalize/docling-pilot
```

Use a separate output directory for pilots. Like the canonical Stage 4 command,
a filtered non-dry run writes a complete replacement output set to its chosen
output directory.

The Docling provider selector resolves the newest successful
`docling-standard` version recorded in the ledger. Azure retains the existing
`prebuilt-layout / 2025-11-01` default.
