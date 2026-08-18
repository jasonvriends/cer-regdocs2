#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GUIDE="$SCRIPT_DIR/deploy-guide.sh"

usage() {
  cat <<'EOF'
REGDOCS Atlas deployment

Start here (read-only):
  ./ui/deploy/deploy.sh
  ./ui/deploy/deploy.sh --guide

Personal-computer / Blob preparation:
  ./ui/deploy/deploy.sh --upload-help
  ./ui/deploy/deploy.sh --check-data

Validation and planning:
  ./ui/deploy/deploy.sh --validate
  ./ui/deploy/deploy.sh --plan

Deployment (explicit; no-argument execution never deploys):
  ./ui/deploy/deploy.sh --infra-only
  ./ui/deploy/deploy.sh --ui-only
  ./ui/deploy/deploy.sh --full

Publication:
  ./ui/deploy/deploy.sh --restart-index
  ./ui/deploy/deploy.sh --restart-intelligence
  ./ui/deploy/deploy.sh --restart-index --restart-intelligence

Operations:
  ./ui/deploy/deploy.sh --status
  ./ui/deploy/deploy.sh --error ATLAS-0123ABCD4567EF89

The guide explains the personal-computer -> Blob -> Cloud Shell -> Stage 5 ->
Stage 6 sequence and prints the next command based on what already exists.
EOF
}

if [[ $# -eq 0 ]]; then
  exec "$GUIDE"
fi

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
  esac
done

exec "$GUIDE" "$@"
