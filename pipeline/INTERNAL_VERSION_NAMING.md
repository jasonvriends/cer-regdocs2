# Internal implementation version naming

The repository-wide release is the only product/pipeline release number.

Existing internal cores still contain some historical `SCRIPT_VERSION` constants
because those exact values are already persisted in SQLite rows and, for some
stages, participate in normalization/artifact identity. Treat those constants as
legacy **component implementation identities**, not releases.

Rules for future edits:

1. Do not introduce a new independent stage release sequence.
2. Public `--version` always reports root `VERSION`.
3. Public `--diagnostics` reports `component_version` plus parser/schema/API data.
4. When materially revising a core that still uses `SCRIPT_VERSION`, rename the
   constant in that same compatibility change to a purpose-specific name such as
   `COMPONENT_VERSION` or `NORMALIZER_CONTRACT_VERSION` and migrate references
   without rewriting historical rows.
5. Do not bump analyzer/parser/schema identities for unrelated release changes.
