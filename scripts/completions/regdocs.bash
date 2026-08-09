# Bash completion for the REGDOCS Atlas public CLI.
#
# Supports both:
#   python pipeline.py down<TAB>
#   python3 pipeline.py analyze az<TAB>
# and, when pipeline.py is executable/in PATH, direct invocation as well.

_regdocs_compgen() {
    COMPREPLY=( $(compgen -W "$1" -- "$2") )
}

_regdocs_pipeline_complete_from() {
    local pipeline_index="$1"
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev=""
    if (( COMP_CWORD > 0 )); then
        prev="${COMP_WORDS[COMP_CWORD-1]}"
    fi

    local command="${COMP_WORDS[pipeline_index+1]:-}"
    local second="${COMP_WORDS[pipeline_index+2]:-}"
    local third="${COMP_WORDS[pipeline_index+3]:-}"

    # Known enum-valued switches.
    case "$prev" in
        --provider)
            _regdocs_compgen "azure docling" "$cur"
            return 0
            ;;
        --priority)
            _regdocs_compgen "HIGH NORMAL LOW" "$cur"
            return 0
            ;;
        --page-size)
            _regdocs_compgen "20 50 100 200" "$cur"
            return 0
            ;;
        --facets)
            _regdocs_compgen "all none" "$cur"
            return 0
            ;;
    esac

    # Top-level command.
    if (( COMP_CWORD == pipeline_index + 1 )); then
        _regdocs_compgen \
            "scout download analyze normalize index db rebuild recover cost status diagnostics version help" \
            "$cur"
        return 0
    fi

    case "$command" in
        scout)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "coverage status audit schema repair probe run" "$cur"
                return 0
            fi
            case "$second" in
                coverage|audit|schema)
                    _regdocs_compgen "--db --help" "$cur"
                    ;;
                status)
                    _regdocs_compgen "--json --db --progress-file --help" "$cur"
                    ;;
                repair)
                    _regdocs_compgen "--db --raw-dir --progress-file --log-file --lock-file --expand-containers --no-expand-containers --expand-compounds --no-expand-compounds --container-max-depth --container-max-items --details --no-details --detail-refresh-days --refresh-details --concurrency --min-delay --max-delay --max-retries --retry-backoff --verbose --force-lock --help" "$cur"
                    ;;
                probe|run)
                    _regdocs_compgen "--start-date --end-date --db --raw-dir --progress-file --log-file --lock-file --page-size --limit --facets --expand-containers --no-expand-containers --expand-compounds --no-expand-compounds --container-max-depth --container-max-items --details --no-details --detail-refresh-days --refresh-details --concurrency --min-delay --max-delay --max-retries --retry-backoff --verbose --force-lock --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        download)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "status plan sidecars run" "$cur"
                return 0
            fi
            case "$second" in
                status)
                    _regdocs_compgen "--json --db --help" "$cur"
                    ;;
                plan)
                    _regdocs_compgen "--db --downloads --output-dir --document-id --limit --include-html --force --retry-failed --help" "$cur"
                    ;;
                sidecars)
                    _regdocs_compgen "--db --downloads --output-dir --document-id --limit --sidecar-dir --dry-run --force-lock --help" "$cur"
                    ;;
                run)
                    _regdocs_compgen "--db --downloads --output-dir --document-id --limit --include-html --force --retry-failed --attempts --concurrency --min-delay --max-delay --connect-timeout --read-timeout --max-file-size-mb --reconcile --no-reconcile --verify-existing --archive-replaced --no-archive-replaced --sidecars --write-sidecars --no-sidecars --sidecar-dir --partial-max-age-hours --audit-dir --lock-file --verbose --force-lock --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        analyze)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "azure docling" "$cur"
                return 0
            fi
            if [[ "$second" == "azure" ]]; then
                if (( COMP_CWORD == pipeline_index + 3 )); then
                    _regdocs_compgen "plan run" "$cur"
                    return 0
                fi
                _regdocs_compgen "--all --limit --document-id --db --endpoint --key --api-version --polling-interval --download-dir --output-dir --lock-file --force-lock --state-file --worker-sleep-seconds --analyzer-id --force --no-reconcile-artifacts --no-verify-hash --help" "$cur"
                return 0
            fi
            if [[ "$second" == "docling" ]]; then
                if (( COMP_CWORD == pipeline_index + 3 )); then
                    _regdocs_compgen "status run" "$cur"
                    return 0
                fi
                _regdocs_compgen "--db --download-dir --output-dir --state-file --lock-file --force-lock --analyzer-id --max-attempts --max-documents --sleep-seconds --retry-quarantined --help" "$cur"
                return 0
            fi
            ;;

        normalize)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "status plan run" "$cur"
                return 0
            fi
            case "$second" in
                status)
                    _regdocs_compgen "--db --help" "$cur"
                    ;;
                plan|run)
                    _regdocs_compgen "--provider --db --analysis-dir --output-dir --document-id --limit --target-words --max-words --concurrency --stop-on-error --lock-file --force-lock --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        index)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "plan publish query" "$cur"
                return 0
            fi
            case "$second" in
                plan|publish)
                    _regdocs_compgen "--normalized-dir --output-dir --endpoint --api-key --index-name --document-id --limit --batch-size --max-batch-bytes --recreate-index --help" "$cur"
                    ;;
                query)
                    _regdocs_compgen "--endpoint --api-key --index-name --top --filter --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        db)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "migrate status verify" "$cur"
                return 0
            fi
            case "$second" in
                migrate)
                    _regdocs_compgen "--db --plan --no-backup --backup-dir --force-lock --help" "$cur"
                    ;;
                status|verify)
                    _regdocs_compgen "--db --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        rebuild)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "inventory plan prepare create verify compare" "$cur"
                return 0
            fi
            case "$second" in
                inventory|plan)
                    _regdocs_compgen "--help" "$cur"
                    ;;
                prepare)
                    _regdocs_compgen "--db --no-verify-raw --no-verify-analysis --help" "$cur"
                    ;;
                create)
                    _regdocs_compgen "--output --flat --help" "$cur"
                    ;;
                verify)
                    _regdocs_compgen "--db --help" "$cur"
                    ;;
                compare)
                    _regdocs_compgen "--source --rebuilt --help" "$cur"
                    ;;
            esac
            return 0
            ;;

        recover)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "scout" "$cur"
                return 0
            fi
            if [[ "$second" == "scout" ]]; then
                _regdocs_compgen "--execute --db --priority --limit --ids-only --timeout --force-lock --help" "$cur"
                return 0
            fi
            ;;

        cost)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "rates azure" "$cur"
                return 0
            fi
            if [[ "$second" == "azure" ]]; then
                _regdocs_compgen "--run-id --help" "$cur"
            else
                _regdocs_compgen "--help" "$cur"
            fi
            return 0
            ;;

        status)
            _regdocs_compgen "--json --help" "$cur"
            return 0
            ;;

        diagnostics|version)
            _regdocs_compgen "--help" "$cur"
            return 0
            ;;

        help)
            if (( COMP_CWORD == pipeline_index + 2 )); then
                _regdocs_compgen "scout download analyze normalize index" "$cur"
                return 0
            fi
            if [[ "$second" == "analyze" ]] && (( COMP_CWORD == pipeline_index + 3 )); then
                _regdocs_compgen "azure docling" "$cur"
                return 0
            fi
            ;;
    esac

    return 1
}

_regdocs_python_completion() {
    local i
    for (( i=1; i<${#COMP_WORDS[@]}; i++ )); do
        case "${COMP_WORDS[i]}" in
            pipeline.py|*/pipeline.py)
                _regdocs_pipeline_complete_from "$i"
                return $?
                ;;
        esac
    done
    # This completion hook is registered on python/python3.  For every other
    # Python invocation, return non-zero and let bash/default filename
    # completion take over.
    return 1
}

_regdocs_direct_completion() {
    _regdocs_pipeline_complete_from 0
}

# -o default / bashdefault preserve normal path completion when a REGDOCS
# option expects a path or when the command is not a pipeline.py invocation.
complete -o bashdefault -o default -F _regdocs_python_completion python python3
complete -o bashdefault -o default -F _regdocs_direct_completion pipeline.py ./pipeline.py
