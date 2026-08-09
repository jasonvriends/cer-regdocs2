# REGDOCS Atlas versioning

REGDOCS Atlas uses one repository-wide release version for coherent pilot checkpoints. The current release is read from the root [`VERSION`](VERSION) file; the current release is `0.0.5`.

## Release version

The release version answers:

> Which integrated REGDOCS Atlas code release was this run executed under?

During the pilot:

- `0.0.x` — internal pilot checkpoints and fixes;
- `0.1.0` — first deliberately stabilized end-to-end pilot contract;
- `1.0.0` — only if a supported stable contract is intentionally declared later.

A release is a coherent checkpoint, not every commit.

When a release is cut:

1. update `VERSION` once;
2. add the release entry to `RELEASE_NOTES.md`;
3. preview database changes with `python pipeline.py db migrate --plan`;
4. run `python pipeline.py db migrate` when needed;
5. run `python pipeline.py db verify`; and
6. perform the relevant real-corpus/status/rebuild checks for the feature being changed.

The pilot intentionally does not maintain a GitHub Actions CI workflow or dedicated repository test suite.

## Preferred command surface

The root command is the public interface:

```text
python pipeline.py version
python pipeline.py scout
python pipeline.py download
python pipeline.py analyze azure
python pipeline.py analyze docling
python pipeline.py normalize --provider azure
python pipeline.py normalize --provider docling
python pipeline.py index
```

The older `pipeline/regdocs_*.py` commands are transitional implementation entry points while their logic moves into `regdocs_atlas/stages/`. New user-facing behavior should be added through `pipeline.py` / `regdocs_atlas`, not by creating additional public stage launchers.

## Component and data-contract identities stay separate

Release version is not the same thing as analyzer, parser, schema, provider, or artifact compatibility identity.

Examples:

- Azure `api_version=2025-11-01` identifies the external analyzer contract;
- the installed Docling version identifies the local analyzer implementation;
- parser/projection versions identify interpretation rules;
- database migration IDs identify ledger shape evolution;
- sidecar/manifest schema versions identify persisted artifact contracts; and
- normalizer/config identities determine whether normalized output may be reused.

A repository release bump by itself must not force expensive Stage 3 analysis to be recomputed.

Historical `SCRIPT_VERSION` values remain in some legacy implementations because those exact values are already stored in SQLite or artifacts. Treat them as component/provenance identities, not independent product releases.

## SQLite history and migrations

Historical run provenance is preserved. Do not rewrite old component/parser/provider identities to the latest release.

The release dimension is tracked separately in:

```text
runs.release_version
pipeline_metadata['release_version']
```

Database schema evolution is tracked in:

```text
schema_migrations
```

Use:

```bash
python pipeline.py db migrate --plan
python pipeline.py db migrate
python pipeline.py db verify
```

Applied migration rows carry a checksum/fingerprint so migration drift fails closed. Existing databases receive a consistent SQLite backup by default before migration, and migration performs schema, integrity, and foreign-key verification afterward.

Do not use `PRAGMA user_version` as the authoritative migration registry; the historical Scout implementation already used that pragma.

The important identity dimensions are therefore:

```text
release_version    integrated REGDOCS Atlas release
component_version  implementation provenance where needed
parser_version     parser/projection contract
migration_id       SQLite schema evolution
artifact schema    durable sidecar/manifest compatibility
provider/API       Azure or Docling analysis identity
```
