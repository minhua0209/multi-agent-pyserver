#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
PID_FILE="$RUNTIME_DIR/runner.pid"
LOG_FILE="$RUNTIME_DIR/runner.log"

status_runner() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "TaskHub Codex runner is running: pid=$pid"
      echo "Log: $LOG_FILE"
      return 0
    fi
    echo "TaskHub Codex runner pid file exists but process is not running: pid=$pid"
    rm -f "$PID_FILE"
    return 1
  fi
  echo "TaskHub Codex runner is not running"
  return 1
}

stop_runner() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "TaskHub Codex runner is not running"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "Stopped TaskHub Codex runner: pid=$pid"
  else
    echo "Runner process already stopped: pid=$pid"
  fi
  rm -f "$PID_FILE"
}

if [[ "${1:-}" == "status" ]]; then
  status_runner
  exit $?
fi

if [[ "${1:-}" == "stop" ]]; then
  stop_runner
  exit 0
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <TASKHUB_SERVER_URL> [USER_ID] [--once] [--dry-run] [--ui] [--background]"
  echo "       $0 status"
  echo "       $0 stop"
  echo "Example: $0 http://192.168.170.18:8000 root --ui --background"
  exit 1
fi

TASKHUB_SERVER_URL="$1"
shift || true
TASKHUB_USER_ID="root"
if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  TASKHUB_USER_ID="$1"
  shift || true
fi

export TASKHUB_SERVER_URL
export TASKHUB_USER_ID

BACKGROUND=false
ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--background" ]]; then
    BACKGROUND=true
  else
    ARGS+=("$arg")
  fi
done

COMMAND=(
  python3 "$SCRIPT_DIR/taskhub_codex_runner.py"
  --config "$SCRIPT_DIR/config.example.json" \
  --server-url "$TASKHUB_SERVER_URL" \
  --user-id "$TASKHUB_USER_ID" \
  "${ARGS[@]}"
)

if [[ "$BACKGROUND" == "true" ]]; then
  mkdir -p "$RUNTIME_DIR"
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "TaskHub Codex runner is already running: pid=$(cat "$PID_FILE")"
    exit 0
  fi
  rm -f "$PID_FILE"
  nohup "${COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  echo "$!" > "$PID_FILE"
  echo "TaskHub Codex runner started in background: pid=$!"
  echo "Log: $LOG_FILE"
  if printf '%s\n' "${ARGS[@]}" | grep -qx -- "--ui"; then
    echo "Web console: http://127.0.0.1:8787"
  fi
  exit 0
fi

"${COMMAND[@]}"
