# Public stage entry points and release diagnostics

The stable public commands are:

```text
regdocs_1_scout.py
regdocs_2_download.py
regdocs_3_azure.py
regdocs_3_docling.py
regdocs_4_normalize.py
regdocs_5_index.py
```

Each public file is intentionally a thin façade. It handles the repository-wide
`--version` and `--diagnostics` contract and delegates all normal execution to
the adjacent internal `*_core.py` implementation.

This split prevents product/release version semantics from becoming entangled
with parser, schema, analyzer, provider, or artifact-compatibility identities.
The internal cores retain the component identities already written into the
SQLite ledger and durable artifacts.

Examples:

```bash
python pipeline/regdocs_1_scout.py --version
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_4_normalize.py --analysis-provider azure
```

Do not call a `_core.py` implementation directly during normal operations. The
core names are an internal compatibility boundary and may change independently
of the stable public stage command names.
