#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
APP_BUNDLE="$BUILD_DIR/NjordHR.app"
PKG_IDENTIFIER="${NJORDHR_PKG_IDENTIFIER:-com.njordhr.desktop.appbundle}"
PKG_VERSION="${NJORDHR_PKG_VERSION:-$(date +%Y.%m.%d.%H%M)}"
PKG_PATH_VERSIONED="$BUILD_DIR/NjordHR-${PKG_VERSION}-unsigned.pkg"
PKG_PATH_LATEST="$BUILD_DIR/NjordHR-unsigned.pkg"

command -v pkgbuild >/dev/null 2>&1 || { echo "pkgbuild not found."; exit 1; }
command -v pkgutil >/dev/null 2>&1 || { echo "pkgutil not found."; exit 1; }

mkdir -p "$BUILD_DIR"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "[NjordHR] App bundle not found. Building first..."
  "$PROJECT_DIR/scripts/packaging/macos/build_app_bundle.sh"
fi

pkgbuild \
  --identifier "$PKG_IDENTIFIER" \
  --version "$PKG_VERSION" \
  --component "$APP_BUNDLE" \
  --install-location "/Applications" \
  "$PKG_PATH_VERSIONED"

cp "$PKG_PATH_VERSIONED" "$PKG_PATH_LATEST"

TMP_EXPAND_DIR="$(mktemp -d /tmp/njordhr_pkg_verify.XXXXXX)"
trap 'rm -rf "$TMP_EXPAND_DIR"' EXIT
pkgutil --expand-full "$PKG_PATH_VERSIONED" "$TMP_EXPAND_DIR" >/dev/null
if ! find "$TMP_EXPAND_DIR" -maxdepth 8 -name "NjordHR.app" | grep -q "NjordHR.app"; then
  echo "[NjordHR] ERROR: Package payload verification failed (NjordHR.app not found)."
  exit 1
fi

echo "[NjordHR] Package built:"
echo "  $PKG_PATH_VERSIONED"
echo "[NjordHR] Latest alias:"
echo "  $PKG_PATH_LATEST"
echo "[NjordHR] Install path:"
echo "  /Applications/NjordHR.app"
echo "[NjordHR] Identifier: $PKG_IDENTIFIER"
echo "[NjordHR] Version: $PKG_VERSION"
