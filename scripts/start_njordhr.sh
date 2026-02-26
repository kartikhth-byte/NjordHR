#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${NJORDHR_RUNTIME_DIR:-$PROJECT_DIR/logs/runtime}"
mkdir -p "$RUNTIME_DIR"
PYTHON_BIN="${NJORDHR_PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[NjordHR] Python runtime not found: $PYTHON_BIN"
  exit 1
fi

LOCK_DIR="/tmp/njordhr-launch.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[NjordHR] Launcher already running. Try again in a few seconds."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
AGENT_PID_FILE="$RUNTIME_DIR/agent.pid"
RUNTIME_ENV_FILE="$RUNTIME_DIR/runtime.env"

OPEN_BROWSER="true"
if [[ "${1:-}" == "--no-open" ]]; then
  OPEN_BROWSER="false"
fi

if [[ -f "$PROJECT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
fi

is_listening() {
  local port="$1"
  lsof -n -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

pick_free_port() {
  local start_port="$1"
  local port="$start_port"
  while is_listening "$port"; do
    port=$((port + 1))
    if [[ "$port" -gt $((start_port + 100)) ]]; then
      echo "[NjordHR] Could not find free port near $start_port" >&2
      exit 1
    fi
  done
  echo "$port"
}

wait_http() {
  local url="$1"
  local retries="${2:-40}"
  local i
  for i in $(seq 1 "$retries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

DEFAULT_BACKEND_PORT="${NJORDHR_PORT:-5050}"
DEFAULT_AGENT_PORT="${NJORDHR_AGENT_PORT:-5051}"
BACKEND_PORT="${NJORDHR_BACKEND_PORT:-$DEFAULT_BACKEND_PORT}"
AGENT_PORT="${NJORDHR_AGENT_RUNTIME_PORT:-$DEFAULT_AGENT_PORT}"

if ! wait_http "http://127.0.0.1:${BACKEND_PORT}/config/runtime" 1; then
  if is_listening "$BACKEND_PORT"; then
    BACKEND_PORT="$(pick_free_port "$DEFAULT_BACKEND_PORT")"
  fi
fi

if ! wait_http "http://127.0.0.1:${AGENT_PORT}/health" 1; then
  if is_listening "$AGENT_PORT"; then
    AGENT_PORT="$(pick_free_port "$DEFAULT_AGENT_PORT")"
  fi
fi

if [[ "$AGENT_PORT" == "$BACKEND_PORT" ]]; then
  AGENT_PORT="$(pick_free_port "$DEFAULT_AGENT_PORT")"
  if [[ "$AGENT_PORT" == "$BACKEND_PORT" ]]; then
    AGENT_PORT="$(pick_free_port $((BACKEND_PORT + 1)))"
  fi
fi

BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"

echo "NJORDHR_BACKEND_PORT=$BACKEND_PORT" > "$RUNTIME_ENV_FILE"
echo "NJORDHR_AGENT_RUNTIME_PORT=$AGENT_PORT" >> "$RUNTIME_ENV_FILE"
echo "NJORDHR_SERVER_URL=$BACKEND_URL" >> "$RUNTIME_ENV_FILE"
echo "NJORDHR_AGENT_URL=$AGENT_URL" >> "$RUNTIME_ENV_FILE"

if wait_http "${BACKEND_URL}/config/runtime" 1; then
  echo "[NjordHR] Backend already running at ${BACKEND_URL}"
else
  echo "[NjordHR] Starting backend at ${BACKEND_URL}"
  (
    cd "$PROJECT_DIR"
    export NJORDHR_PORT="$BACKEND_PORT"
    export NJORDHR_SERVER_URL="$BACKEND_URL"
    export USE_LOCAL_AGENT="${USE_LOCAL_AGENT:-true}"
    export NJORDHR_AUTO_SHUTDOWN_ON_UI_IDLE="${NJORDHR_AUTO_SHUTDOWN_ON_UI_IDLE:-true}"
    export NJORDHR_UI_IDLE_SHUTDOWN_SECONDS="${NJORDHR_UI_IDLE_SHUTDOWN_SECONDS:-75}"
    nohup "$PYTHON_BIN" backend_server.py >> "$RUNTIME_DIR/backend.out" 2>> "$RUNTIME_DIR/backend.err" &
    echo $! > "$BACKEND_PID_FILE"
  )
  if ! wait_http "${BACKEND_URL}/config/runtime" 100; then
    echo "[NjordHR] Backend failed to start. Check $RUNTIME_DIR/backend.err"
    exit 1
  fi
fi

if wait_http "${AGENT_URL}/health" 1; then
  echo "[NjordHR] Agent already running at ${AGENT_URL}"
else
  echo "[NjordHR] Starting local agent at ${AGENT_URL}"
  (
    cd "$PROJECT_DIR"
    export NJORDHR_AGENT_HOST="127.0.0.1"
    export NJORDHR_AGENT_PORT="$AGENT_PORT"
    nohup "$PYTHON_BIN" agent_server.py >> "$RUNTIME_DIR/agent.out" 2>> "$RUNTIME_DIR/agent.err" &
    echo $! > "$AGENT_PID_FILE"
  )
  if ! wait_http "${AGENT_URL}/health" 50; then
    echo "[NjordHR] Agent failed to start. Check $RUNTIME_DIR/agent.err"
    exit 1
  fi
fi

curl -fsS -X PUT "${AGENT_URL}/settings" \
  -H "Content-Type: application/json" \
  -d "{\"api_base_url\":\"${BACKEND_URL}\",\"cloud_sync_enabled\":true}" >/dev/null 2>&1 || true

if [[ "$OPEN_BROWSER" == "true" ]]; then
  open "${BACKEND_URL}" >/dev/null 2>&1 || true
fi

echo "[NjordHR] Ready."
echo "[NjordHR] Backend: ${BACKEND_URL}"
echo "[NjordHR] Agent:   ${AGENT_URL}"
echo "[NjordHR] Logs:    ${RUNTIME_DIR}"
