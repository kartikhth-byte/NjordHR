#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${NJORDHR_PORT:-5050}"
BACKEND_URL="${NJORDHR_SERVER_URL:-http://127.0.0.1:${BACKEND_PORT}}"
AGENT_URL="${NJORDHR_AGENT_BASE_URL:-http://127.0.0.1:${NJORDHR_AGENT_PORT:-5051}}"

cd "$PROJECT_DIR"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

CONFIG_PATH="${NJORDHR_CONFIG_PATH:-$PROJECT_DIR/config.ini}"
if [[ -f "$CONFIG_PATH" ]]; then
  eval "$(
    "${NJORDHR_PYTHON_BIN:-python3}" - "$CONFIG_PATH" <<'PY'
import configparser
import os
import shlex
import sys

cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])

mapping = {
    "SUPABASE_URL": ("Advanced", "supabase_url"),
    "SUPABASE_SECRET_KEY": ("Credentials", "Supabase_Secret_Key"),
    "SUPABASE_SERVICE_ROLE_KEY": ("Credentials", "Supabase_Service_Role_Key"),
    "USE_SUPABASE_DB": ("Advanced", "use_supabase_db"),
    "USE_DUAL_WRITE": ("Advanced", "use_dual_write"),
    "USE_SUPABASE_READS": ("Advanced", "use_supabase_reads"),
    "USE_LOCAL_AGENT": ("Advanced", "use_local_agent"),
    "USE_CLOUD_EXPORT": ("Advanced", "use_cloud_export"),
}

for env_name, (section, key) in mapping.items():
    if os.getenv(env_name):
        continue
    value = cfg.get(section, key, fallback="").strip()
    if not value:
        continue
    print(f"export {env_name}={shlex.quote(value)}")
PY
  )"
fi

export NJORDHR_PORT="$BACKEND_PORT"
export NJORDHR_SERVER_URL="$BACKEND_URL"
export USE_LOCAL_AGENT="${USE_LOCAL_AGENT:-true}"
export NJORDHR_AGENT_BASE_URL="$AGENT_URL"
export USE_SUPABASE_DB="${USE_SUPABASE_DB:-false}"
export NJORDHR_AUTH_MODE="${NJORDHR_AUTH_MODE:-local}"

exec "${NJORDHR_PYTHON_BIN:-python3}" backend_server.py
