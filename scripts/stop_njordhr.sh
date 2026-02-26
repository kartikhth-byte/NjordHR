#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$PROJECT_DIR/logs/runtime"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
AGENT_PID_FILE="$RUNTIME_DIR/agent.pid"
RUNTIME_ENV_FILE="$RUNTIME_DIR/runtime.env"

DEFAULT_BACKEND_PORT="${NJORDHR_PORT:-5050}"
DEFAULT_AGENT_PORT="${NJORDHR_AGENT_PORT:-5051}"

BACKEND_PORT="$DEFAULT_BACKEND_PORT"
AGENT_PORT="$DEFAULT_AGENT_PORT"
if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE" || true
  BACKEND_PORT="${NJORDHR_BACKEND_PORT:-$BACKEND_PORT}"
  AGENT_PORT="${NJORDHR_AGENT_RUNTIME_PORT:-$AGENT_PORT}"
fi

kill_pid_file() {
  local pid_file="$1"
  local name="$2"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[NjordHR] Stopping $name pid=$pid"
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
}

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

kill_pid_file "$BACKEND_PID_FILE" "backend"
kill_pid_file "$AGENT_PID_FILE" "agent"

kill_port_listener "$BACKEND_PORT" "backend"
kill_port_listener "$AGENT_PORT" "agent"

echo "[NjordHR] Stop complete."
