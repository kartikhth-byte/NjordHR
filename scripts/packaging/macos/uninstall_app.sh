#!/usr/bin/env bash
set -euo pipefail

PLIST_LABEL="com.njordhr.local"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
APP_PATH_SYSTEM="/Applications/NjordHR.app"
APP_PATH_USER="$HOME/Applications/NjordHR.app"
APP_SUPPORT_DIR="$HOME/Library/Application Support/NjordHR"
RUNTIME_DIR="$APP_SUPPORT_DIR/runtime"
RUNTIME_ENV_FILE="$RUNTIME_DIR/runtime.env"

REMOVE_DATA="false"
if [[ "${1:-}" == "--remove-data" ]]; then
  REMOVE_DATA="true"
fi

DEFAULT_BACKEND_PORT=5050
DEFAULT_AGENT_PORT=5051
BACKEND_PORT=$DEFAULT_BACKEND_PORT
AGENT_PORT=$DEFAULT_AGENT_PORT

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE" || true
  BACKEND_PORT="${NJORDHR_BACKEND_PORT:-$BACKEND_PORT}"
  AGENT_PORT="${NJORDHR_AGENT_RUNTIME_PORT:-$AGENT_PORT}"
fi

kill_port_listener() {
  local port="$1"
  local name="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "[NjordHR] Stopping $name on port $port (pid: $pids)"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

echo "[NjordHR] Removing LaunchAgent (if installed)..."
if [[ -f "$PLIST_PATH" ]]; then
  launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
fi

kill_port_listener "$BACKEND_PORT" "backend"
kill_port_listener "$AGENT_PORT" "agent"

echo "[NjordHR] Removing app bundle(s)..."
if [[ -d "$APP_PATH_SYSTEM" ]]; then
  rm -rf "$APP_PATH_SYSTEM"
fi
if [[ -d "$APP_PATH_USER" ]]; then
  rm -rf "$APP_PATH_USER"
fi

if [[ "$REMOVE_DATA" == "true" ]]; then
  echo "[NjordHR] Removing application data: $APP_SUPPORT_DIR"
  rm -rf "$APP_SUPPORT_DIR"
else
  echo "[NjordHR] Keeping application data at: $APP_SUPPORT_DIR"
  echo "[NjordHR] Re-run with --remove-data to delete local NjordHR data."
fi

echo "[NjordHR] Uninstall complete."
