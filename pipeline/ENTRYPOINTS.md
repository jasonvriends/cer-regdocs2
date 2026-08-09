# Public pipeline entry points

The preferred command surface is the root `pipeline.py` router:

```bash
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize --provider azure
python pipeline.py normalize --provider docling
python pipeline.py index
```

Operational package commands are also available:

```bash
python pipeline.py version
python pipeline.py status
python pipeline.py diagnostics
python pipeline.py db migrate
python pipeline.py db status
python pipeline.py db verify
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
```

The historical stage entry points remain supported compatibility commands:

```text
pipeline/regdocs_1_scout.py
pipeline/regdocs_2_download.py
pipeline/regdocs_3_azure.py
pipeline/regdocs_3_docling.py
pipeline/regdocs_4_normalize.py
pipeline/regdocs_5_index.py
```

In release `0.0.2`, the unified CLI deliberately delegates normal stage execution to those existing entry points so retry/billing behavior, child-process isolation, SQLite semantics, and artifact formats remain unchanged while the package refactor begins.

The stage files continue to expose repository-wide `--version` and component-level `--diagnostics`. Do not call a `_core.py` implementation directly during normal operations; the core filenames are temporary internal compatibility boundaries and may change as their logic moves into `regdocs_atlas` modules.
