# REGDOCS Atlas versioning

REGDOCS Atlas uses one repository-wide **release version** for a coherent pipeline release.

The current release is read from the root [`VERSION`](VERSION) file. The initial baseline is `0.0.1`.

## Release version

The release version answers:

> Which integrated REGDOCS Atlas code release was this run executed under?

During the prototype phase, use simple pre-1.0 semantic versions:

- `0.0.x` — internal pipeline checkpoints and fixes;
- `0.1.0` — first deliberately stabilized end-to-end prototype contract;
- later minor versions — meaningful prototype capabilities or contract milestones;
- `1.0.0` — only when a stable supported contract is intentionally declared.

A release is a coherent checkpoint, not every commit. When a release is cut:

1. update `VERSION` once;
2. add the release entry to `RELEASE_NOTES.md`;
3. run `python pipeline/regdocs_release.py --sync-db` for the local ledger;
4. run the relevant smoke/integrity tests;
5. optionally create the matching Git tag, for example `v0.0.2`.

All public pipeline stages belong to the same release. Do not independently call Stage 1, Stage 2, Stage 3, Stage 4, and Stage 5 versions different product releases.

## Public command behavior

Every primary public stage command now has the same version semantics:

```text
python pipeline/regdocs_1_scout.py --version
python pipeline/regdocs_2_download.py --version
python pipeline/regdocs_3_azure.py --version
python pipeline/regdocs_3_docling.py --version
python pipeline/regdocs_4_normalize.py --version
python pipeline/regdocs_5_index.py --version
```

All print only the repository release, currently:

```text
0.0.1
```

Use `--diagnostics` when implementation detail is required. It prints structured JSON containing the release version, component name/version, implementation path and SHA-256, and relevant parser/schema/provider/API identities. For example:

```bash
python pipeline/regdocs_3_azure.py --diagnostics
python pipeline/regdocs_4_normalize.py --diagnostics
```

The public files are deliberately thin release-aware entry points. The existing implementations live in adjacent internal `*_core.py` files and retain their historical implementation identities where those values still participate in durable run/artifact provenance.

## Component and data-contract versions stay separate

Existing implementation/component values, parser versions, `SCHEMA_VERSION`, Azure API versions, Docling package versions, analyzer IDs, normalization contract identities, and sidecar schema versions are not all the same kind of version.

They should not be flattened into the release version when they participate in artifact compatibility or provenance.

Examples:

- Azure `api_version=2025-11-01` identifies the external analyzer contract;
- the installed Docling version identifies the native Docling analysis implementation;
- parser/projection versions identify interpretation rules;
- schema versions identify stored-data contracts;
- normalizer contract/config identities determine whether normalized output may be reused.

These values may change independently inside a repository release and may remain unchanged across several releases.

This distinction is important because bumping the repository from `0.0.1` to `0.0.2` must **not** by itself force expensive Azure Content Understanding or Docling analysis to be recomputed.

The old internal constant name `SCRIPT_VERSION` is now considered a legacy implementation detail, not a product release. New public entry points expose the value as `component_version` in diagnostics. As implementation cores are materially revised, rename or replace legacy `SCRIPT_VERSION` constants with purpose-specific names such as `COMPONENT_VERSION`, `NORMALIZER_CONTRACT_VERSION`, or another compatibility-specific identifier rather than introducing new independent product-version sequences.

## SQLite history

The shared SQLite ledger preserves historical component/script/parser values already recorded by older runs. Do not rewrite them to `0.0.1`; they describe what actually executed at the time.

The release migration adds a separate nullable `runs.release_version` column and a small `pipeline_metadata` table. Historical runs remain `NULL` for `release_version` unless their release can be proven independently. New runs are stamped with the current repository release after `regdocs_release.py --sync-db` installs/updates the release trigger.

This gives the ledger distinct dimensions:

```text
release_version   integrated repository release, e.g. 0.0.1
script_version    legacy durable component implementation value
parser_version    parser/projection contract where applicable
```

For new code and user-facing output, prefer the terms `release_version`, `component_version`, `parser_version`, and `schema_version`. The SQLite `script_version` column remains for backward compatibility and historical provenance until a future additive schema migration gives it a clearer name without rewriting history.
