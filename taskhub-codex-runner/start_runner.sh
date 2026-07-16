#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <TASKHUB_SERVER_URL> [USER_ID] [--once] [--dry-run]"
  echo "Example: $0 http://192.168.170.18:8000 王大锤 --once"
  exit 1
fi

TASKHUB_SERVER_URL="$1"
TASKHUB_USER_ID="${2:-王大锤}"
shift || true
if [[ $# -gt 0 ]]; then
  shift || true
fi

export TASKHUB_SERVER_URL
export TASKHUB_USER_ID

python3 "$SCRIPT_DIR/taskhub_codex_runner.py" \
  --config "$SCRIPT_DIR/config.example.json" \
  --server-url "$TASKHUB_SERVER_URL" \
  --user-id "$TASKHUB_USER_ID" \
  "$@"
