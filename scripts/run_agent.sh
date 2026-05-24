#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_DIR"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

export NJORDHR_AGENT_HOST="${NJORDHR_AGENT_HOST:-127.0.0.1}"
export NJORDHR_AGENT_PORT="${NJORDHR_AGENT_PORT:-5051}"

exec "${NJORDHR_PYTHON_BIN:-python3}" -m agent
