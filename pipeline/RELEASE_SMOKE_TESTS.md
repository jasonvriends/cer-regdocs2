# Release/version smoke checks

After pulling a release/versioning change, verify:

```bash
python pipeline/regdocs_release.py --version
python pipeline/regdocs_release.py --status

python pipeline/regdocs_1_scout.py --version
python pipeline/regdocs_2_download.py --version
python pipeline/regdocs_3_azure.py --version
python pipeline/regdocs_3_docling.py --version
python pipeline/regdocs_4_normalize.py --version
python pipeline/regdocs_5_index.py --version
```

Every stage version command must print exactly the same value as the root
`VERSION` file.

Then inspect implementation diagnostics without creating a pipeline run:

```bash
python pipeline/regdocs_1_scout.py --diagnostics
python pipeline/regdocs_2_download.py --diagnostics
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_3_docling.py --diagnostics
python pipeline/regdocs_4_normalize.py --diagnostics
python pipeline/regdocs_5_index.py --diagnostics
```

Diagnostics must include `release_version`, `component`, `component_version`,
`implementation`, and `implementation_sha256`. Provider-specific stages should
also expose their relevant parser/API/analyzer identities.

Finally run the normal help path for each public entry point to prove delegation
still reaches the existing implementation:

```bash
python pipeline/regdocs_1_scout.py --help >/dev/null
python pipeline/regdocs_2_download.py --help >/dev/null
python pipeline/regdocs_3_azure.py --help >/dev/null
python pipeline/regdocs_3_docling.py --help >/dev/null
python pipeline/regdocs_4_normalize.py --help >/dev/null
python pipeline/regdocs_5_index.py --help >/dev/null
```
