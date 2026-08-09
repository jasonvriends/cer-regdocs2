# Release/version smoke checks

After pulling a release or migration change, start with the unified surface:

```bash
python pipeline.py version
python pipeline.py diagnostics
python pipeline.py db migrate
python pipeline.py db verify
python pipeline.py db status
python pipeline.py rebuild inventory
python pipeline.py rebuild plan
```

`pipeline.py version` must equal the root `VERSION` file. `db migrate` must be idempotent: a second invocation should report no newly applied migrations.

Then verify that every compatibility stage reports the same release:

```bash
python pipeline/regdocs_1_scout.py --version
python pipeline/regdocs_2_download.py --version
python pipeline/regdocs_3_azure.py --version
python pipeline/regdocs_3_docling.py --version
python pipeline/regdocs_4_normalize.py --version
python pipeline/regdocs_5_index.py --version
```

Inspect implementation diagnostics without creating a pipeline run:

```bash
python pipeline/regdocs_1_scout.py --diagnostics
python pipeline/regdocs_2_download.py --diagnostics
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_3_docling.py --diagnostics
python pipeline/regdocs_4_normalize.py --diagnostics
python pipeline/regdocs_5_index.py --diagnostics
```

Finally prove unified CLI delegation reaches each existing implementation without doing work:

```bash
python pipeline.py scout --help >/dev/null
python pipeline.py download --help >/dev/null
python pipeline.py analyze azure --help >/dev/null
python pipeline.py analyze docling --help >/dev/null
python pipeline.py normalize --help >/dev/null
python pipeline.py index --help >/dev/null
```

For schema development, run the migration tests against both a clean database and an existing/legacy-shaped fixture before publishing a release.
