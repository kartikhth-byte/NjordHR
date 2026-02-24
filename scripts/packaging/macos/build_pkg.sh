#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
PKG_PATH="$BUILD_DIR/NjordHR-unsigned.pkg"
APP_BUNDLE="$BUILD_DIR/NjordHR.app"

command -v pkgbuild >/dev/null 2>&1 || { echo "pkgbuild not found."; exit 1; }

mkdir -p "$BUILD_DIR"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "[NjordHR] App bundle not found. Building first..."
  "$PROJECT_DIR/scripts/packaging/macos/build_app_bundle.sh"
fi

pkgbuild \
  --identifier "com.njordhr.desktop" \
  --version "1.0.0" \
  --component "$APP_BUNDLE" \
  --install-location "/Applications" \
  "$PKG_PATH"

echo "[NjordHR] Package built:"
echo "  $PKG_PATH"
echo "[NjordHR] Install path:"
echo "  /Applications/NjordHR.app"
