# REGDOCS Atlas versioning

REGDOCS Atlas uses one repository-wide **release version** for each coherent pipeline checkpoint. The current release is read from the root [`VERSION`](VERSION) file; as of this document it is `0.0.2`.

## Release version

The release version answers:

> Which integrated REGDOCS Atlas code release was this run executed under?

During the prototype phase:

- `0.0.x` — internal pipeline checkpoints and fixes;
- `0.1.0` — first deliberately stabilized end-to-end prototype contract;
- later minor versions — meaningful prototype capabilities or contract milestones;
- `1.0.0` — only when a stable supported contract is intentionally declared.

A release is a coherent checkpoint, not every commit. When a release is cut:

1. update `VERSION` once;
2. add the release entry to `RELEASE_NOTES.md`;
3. run `python pipeline.py db migrate` for the local ledger;
4. run `python pipeline.py db verify` and the relevant smoke/integrity tests; and
5. optionally create the matching Git tag, for example `v0.0.2`.

All public pipeline stages belong to the same release. Do not independently call Stage 1, Stage 2, Stage 3, Stage 4, and Stage 5 versions different product releases.

## Preferred public command behavior

The root command is now the preferred orchestration surface:

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

The existing `pipeline/regdocs_*.py` stage commands remain supported compatibility entry points and their `--version` behavior still reports the same repository release.

Use stage `--diagnostics` when implementation detail is required. Those diagnostics expose component/parser/schema/provider/API identities separately from the repository release.

## Component and data-contract versions stay separate

Existing implementation/component values, parser versions, schema/migration versions, Azure API versions, Docling package versions, analyzer IDs, normalization contract identities, and sidecar schema versions are not the same kind of version.

They should not be flattened into the release version when they participate in artifact compatibility or provenance.

Examples:

- Azure `api_version=2025-11-01` identifies the external analyzer contract;
- the installed Docling version identifies the native Docling analysis implementation;
- parser/projection versions identify interpretation rules;
- database migration IDs identify ledger shape evolution;
- sidecar/manifest schema versions identify persisted artifact contracts; and
- normalizer contract/config identities determine whether normalized output may be reused.

This distinction is important because bumping the repository from `0.0.1` to `0.0.2` must **not** by itself force expensive Azure Content Understanding or Docling analysis to be recomputed.

The old internal constant name `SCRIPT_VERSION` remains a compatibility detail in legacy implementations. New/refactored modules should prefer purpose-specific names such as `COMPONENT_VERSION`, `NORMALIZER_CONTRACT_VERSION`, `PARSER_VERSION`, or `SCHEMA_VERSION` rather than creating new independent product-version sequences.

## SQLite history and migrations

The shared SQLite ledger preserves historical component/script/parser values already recorded by older runs. Do not rewrite them to a later release; they describe what actually executed at the time.

The release dimension is separate:

```text
runs.release_version
pipeline_metadata['release_version']
```

Historical runs may remain `NULL` for `release_version` when they predate unified release tracking. New runs are stamped with the current repository release by the SQLite trigger.

Starting in `0.0.2`, database shape is tracked separately in:

```text
schema_migrations
```

The migration chain is applied with:

```bash
python pipeline.py db migrate
python pipeline.py db verify
```

Do not use `PRAGMA user_version` as the authoritative migration registry. The legacy Scout implementation historically owns that pragma, so centralized migrations deliberately use named migration IDs instead.

This gives the ledger distinct dimensions:

```text
release_version   integrated repository release, e.g. 0.0.2
script_version    legacy durable component implementation value
parser_version    parser/projection contract where applicable
migration_id      database schema evolution identity
```

For new code and user-facing output, prefer `release_version`, `component_version`, `parser_version`, and explicit schema/migration identifiers. Existing SQLite columns remain backward compatible unless a future additive migration provides a clearer representation without rewriting history.
