#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
PKG_ROOT="$BUILD_DIR/pkgroot"
APP_INSTALL_DIR="$PKG_ROOT/Applications/NjordHR"
PKG_PATH="$BUILD_DIR/NjordHR-unsigned.pkg"

command -v pkgbuild >/dev/null 2>&1 || { echo "pkgbuild not found."; exit 1; }

mkdir -p "$BUILD_DIR"
rm -rf "$PKG_ROOT"
mkdir -p "$APP_INSTALL_DIR"

echo "[NjordHR] Preparing install payload..."
rsync -a \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "build" \
  --exclude "logs/runtime" \
  --exclude "*.db-journal" \
  "$PROJECT_DIR/" "$APP_INSTALL_DIR/"

pkgbuild \
  --identifier "com.njordhr.desktop" \
  --version "1.0.0" \
  --install-location "/" \
  --root "$PKG_ROOT" \
  "$PKG_PATH"

echo "[NjordHR] Package built:"
echo "  $PKG_PATH"
echo "[NjordHR] Install path:"
echo "  /Applications/NjordHR"

