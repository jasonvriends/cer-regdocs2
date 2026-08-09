#!/usr/bin/env bash
# Source this file once from the repository root:
#   source scripts/completions/install-bash.sh
#
# It enables REGDOCS completion in the current shell and adds one idempotent
# source line to ~/.bashrc for future Bash shells.

if [[ -z "${BASH_VERSION:-}" ]]; then
    printf 'ERROR: REGDOCS completion installer must be sourced from Bash.\n' >&2
    return 2 2>/dev/null || exit 2
fi

_completion_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_completion_file="${_completion_dir}/regdocs.bash"
_bashrc="${HOME}/.bashrc"
_source_line="source \"${_completion_file}\""

if [[ ! -f "${_completion_file}" ]]; then
    printf 'ERROR: completion file not found: %s\n' "${_completion_file}" >&2
    return 2
fi

if [[ ! -f "${_bashrc}" ]] || ! grep -Fqx "${_source_line}" "${_bashrc}"; then
    printf '\n# REGDOCS Atlas CLI completion\n%s\n' "${_source_line}" >> "${_bashrc}"
    printf 'Added REGDOCS completion to %s\n' "${_bashrc}"
else
    printf 'REGDOCS completion is already present in %s\n' "${_bashrc}"
fi

# shellcheck source=regdocs.bash
source "${_completion_file}"
printf 'REGDOCS completion enabled in this shell. Try: python pipeline.py down<TAB>\n'

unset _completion_dir _completion_file _bashrc _source_line
