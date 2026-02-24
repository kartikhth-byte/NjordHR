#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PKG_PATH="${1:-$PROJECT_DIR/build/macos/NjordHR-unsigned.pkg}"

if [[ ! -f "$PKG_PATH" ]]; then
  echo "[NjordHR] Package not found: $PKG_PATH"
  echo "[NjordHR] Build first with: ./scripts/packaging/macos/build_pkg.sh"
  exit 1
fi

echo "[NjordHR] Installing package for QA verification..."
sudo installer -pkg "$PKG_PATH" -target /

if [[ ! -d "/Applications/NjordHR.app" ]]; then
  echo "[NjordHR] ERROR: /Applications/NjordHR.app not found after install."
  exit 1
fi

echo "[NjordHR] QA verification passed."
echo "[NjordHR] Installed app path: /Applications/NjordHR.app"

