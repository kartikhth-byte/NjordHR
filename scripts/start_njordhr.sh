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

# Load persisted runtime overrides (cloud mode, Supabase keys, auth mode, etc.).
if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

# Provisioning support: seed missing runtime keys from bundled defaults when present.
if [[ -f "$PROJECT_DIR/default_runtime.env" ]]; then
  touch "$RUNTIME_ENV_FILE"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    [[ "$line" == \#* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    if ! grep -qE "^${key}=" "$RUNTIME_ENV_FILE"; then
      echo "${key}=${value}" >> "$RUNTIME_ENV_FILE"
    fi
  done < "$PROJECT_DIR/default_runtime.env"
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

CONFIG_PATH="${NJORDHR_CONFIG_PATH:-$PROJECT_DIR/config.ini}"
if [[ ! -f "$CONFIG_PATH" ]]; then
  if [[ -f "$PROJECT_DIR/config.example.ini" ]]; then
    mkdir -p "$(dirname "$CONFIG_PATH")"
    cp "$PROJECT_DIR/config.example.ini" "$CONFIG_PATH"
    echo "[NjordHR] Created config from template at: $CONFIG_PATH"
  else
    echo "[NjordHR] Missing config: $CONFIG_PATH"
    exit 1
  fi
fi
export NJORDHR_CONFIG_PATH="$CONFIG_PATH"
"$PYTHON_BIN" - "$CONFIG_PATH" "$HOME/Documents/NjordHR/Downloads" "$PROJECT_DIR/Verified_Resumes" "$PROJECT_DIR/logs" <<'PY'
import configparser
import os
import sys

cfg_path, default_download, default_verified, default_log = sys.argv[1:5]
cfg = configparser.ConfigParser()
cfg.read(cfg_path)

if "Credentials" not in cfg:
    cfg["Credentials"] = {}
if "Settings" not in cfg:
    cfg["Settings"] = {}
if "Advanced" not in cfg:
    cfg["Advanced"] = {}

def is_placeholder(raw):
    v = (raw or "").strip().lower()
    if not v:
        return True
    if "change_me" in v or "your_" in v or "/absolute/path/" in v:
        return True
    return False

def norm(path):
    return os.path.abspath(os.path.expanduser(path))

if is_placeholder(cfg["Settings"].get("Default_Download_Folder", "")):
    cfg["Settings"]["Default_Download_Folder"] = norm(default_download)
if is_placeholder(cfg["Settings"].get("Additional_Local_Folder", "")):
    cfg["Settings"]["Additional_Local_Folder"] = norm(default_verified)
if is_placeholder(cfg["Advanced"].get("log_dir", "")):
    cfg["Advanced"]["log_dir"] = norm(default_log)

with open(cfg_path, "w", encoding="utf-8") as fh:
    cfg.write(fh)
PY

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

set_runtime_env() {
  local key="$1"
  local value="$2"
  touch "$RUNTIME_ENV_FILE"
  "$PYTHON_BIN" - "$RUNTIME_ENV_FILE" "$key" "$value" <<'PY'
import os
import sys

path, key, value = sys.argv[1:4]
lines = []
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh]

updated = False
for i, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[i] = f"{key}={value}"
        updated = True
        break
if not updated:
    lines.append(f"{key}={value}")

with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines).rstrip("\n") + "\n")
PY
}

set_runtime_env "NJORDHR_BACKEND_PORT" "$BACKEND_PORT"
set_runtime_env "NJORDHR_AGENT_RUNTIME_PORT" "$AGENT_PORT"
set_runtime_env "NJORDHR_SERVER_URL" "$BACKEND_URL"
set_runtime_env "NJORDHR_AGENT_URL" "$AGENT_URL"
set_runtime_env "NJORDHR_PASSWORD_HASH_METHOD" "${NJORDHR_PASSWORD_HASH_METHOD:-pbkdf2:sha256:600000}"

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
