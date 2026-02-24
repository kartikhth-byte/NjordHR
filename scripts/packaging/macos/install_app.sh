#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
APP_SRC="$PROJECT_DIR/build/macos/NjordHR.app"

if [[ ! -d "$APP_SRC" ]]; then
  echo "[NjordHR] App bundle not found. Building first..."
  "$PROJECT_DIR/scripts/packaging/macos/build_app_bundle.sh"
fi

INSTALL_TARGET="/Applications/NjordHR.app"
USER_APPS_DIR="$HOME/Applications"
FALLBACK_TARGET="$USER_APPS_DIR/NjordHR.app"

copy_app() {
  local src="$1"
  local dst="$2"
  rm -rf "$dst"
  cp -R "$src" "$dst"
}

if [[ -w "/Applications" ]]; then
  copy_app "$APP_SRC" "$INSTALL_TARGET"
  echo "[NjordHR] Installed app:"
  echo "  $INSTALL_TARGET"
  open -a "$INSTALL_TARGET" >/dev/null 2>&1 || true
else
  mkdir -p "$USER_APPS_DIR"
  copy_app "$APP_SRC" "$FALLBACK_TARGET"
  echo "[NjordHR] Installed app:"
  echo "  $FALLBACK_TARGET"
  open -a "$FALLBACK_TARGET" >/dev/null 2>&1 || true
fi

echo "[NjordHR] Use Finder/Launchpad to open 'NjordHR'."

