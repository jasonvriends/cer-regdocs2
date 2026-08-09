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

Commands launched through `pipeline.py` use the shared REGDOCS Atlas mode header, canonical `workspace/pipeline.log`, and the preferred `database/locks/pipeline.lock` mutation lock. Human status output uses zero-padded progress based on the run total; use `--json` when structured output is preferred.

Operational package commands are also available:

```bash
python pipeline.py version
python pipeline.py status
python pipeline.py status --json
python pipeline.py diagnostics

python pipeline.py cost rates
python pipeline.py cost azure
python pipeline.py cost azure --run-id 61

python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db status
python pipeline.py db verify

python pipeline.py rebuild inventory
python pipeline.py rebuild plan
python pipeline.py rebuild create --output database/regdocs.rebuilt.db
python pipeline.py rebuild verify --db database/regdocs.rebuilt.db

# inspect the durable Scout recovery queue only
python pipeline.py recover scout --db database/regdocs.rebuilt.db

# selectively fetch fresh authoritative REGDOCS detail evidence for queued IDs
python pipeline.py recover scout --execute --db database/regdocs.rebuilt.db --priority HIGH --limit 100
```

Azure dollar estimates use usage meters preserved in Content Understanding results plus rates supplied through the documented `REGDOCS_AZURE_CU_*_PER_1000_USD` environment variables. No service price is hard-coded. Docling reports service cost as `n/a (local compute)`.

Normal Stage 2 runs write deterministic metadata sidecars by default as a durable recovery artifact. Use `python pipeline.py download --no-sidecars` only when intentionally opting out, or `python pipeline.py download --sidecars-only` to backfill sidecars without network downloads.

`pipeline/requirements.txt` is the single authoritative dependency-version set for the complete pipeline. `pipeline/requirements-docling.txt` is only a temporary compatibility redirect while legacy Docling diagnostics are retired.

The historical stage entry points remain supported compatibility commands:

```text
pipeline/regdocs_1_scout.py
pipeline/regdocs_2_download.py
pipeline/regdocs_3_azure.py
pipeline/regdocs_3_docling.py
pipeline/regdocs_4_normalize.py
pipeline/regdocs_5_index.py
```

The unified CLI deliberately delegates normal stage execution to those existing entry points so retry/billing behavior, child-process isolation, SQLite semantics, and established artifact formats remain unchanged while the package refactor proceeds. Stage-specific locks/logs therefore remain temporarily as compatibility and defense-in-depth when those legacy commands are invoked directly; `pipeline.py` is the preferred unified operational path.

The stage files continue to expose repository-wide `--version` and component-level `--diagnostics`. Do not call a `_core.py` implementation directly during normal operations; the core filenames are temporary internal compatibility boundaries and may change as their logic moves into `regdocs_atlas` modules.

For migration/recovery details, see [`../docs/DATABASE_RECOVERY.md`](../docs/DATABASE_RECOVERY.md).
