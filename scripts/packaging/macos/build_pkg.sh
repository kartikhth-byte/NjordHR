#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
APP_BUNDLE="$BUILD_DIR/NjordHR.app"
PKG_IDENTIFIER="${NJORDHR_PKG_IDENTIFIER:-com.njordhr.desktop.appbundle}"
PKG_VERSION="${NJORDHR_PKG_VERSION:-$(date +%Y.%m.%d.%H%M)}"
PKG_PATH_VERSIONED="$BUILD_DIR/NjordHR-${PKG_VERSION}-unsigned.pkg"
PKG_PATH_LATEST="$BUILD_DIR/NjordHR-unsigned.pkg"
PKG_ROOT="$BUILD_DIR/pkgroot"
COMPONENT_PLIST="$BUILD_DIR/component.plist"

command -v pkgbuild >/dev/null 2>&1 || { echo "pkgbuild not found."; exit 1; }
command -v pkgutil >/dev/null 2>&1 || { echo "pkgutil not found."; exit 1; }

mkdir -p "$BUILD_DIR"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "[NjordHR] App bundle not found. Building first..."
  "$PROJECT_DIR/scripts/packaging/macos/build_app_bundle.sh"
fi

rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/Applications"
cp -R "$APP_BUNDLE" "$PKG_ROOT/Applications/NjordHR.app"

rm -f "$COMPONENT_PLIST"
pkgbuild --analyze --root "$PKG_ROOT" "$COMPONENT_PLIST"
if [[ -f "$COMPONENT_PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :0:BundleIsRelocatable false" "$COMPONENT_PLIST" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :0:BundleHasStrictIdentifier true" "$COMPONENT_PLIST" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :0:BundleIsVersionChecked true" "$COMPONENT_PLIST" >/dev/null 2>&1 || true
fi

pkgbuild \
  --identifier "$PKG_IDENTIFIER" \
  --version "$PKG_VERSION" \
  --root "$PKG_ROOT" \
  --component-plist "$COMPONENT_PLIST" \
  --install-location "/" \
  "$PKG_PATH_VERSIONED"

cp "$PKG_PATH_VERSIONED" "$PKG_PATH_LATEST"

TMP_VERIFY_BASE="$(mktemp -d /tmp/njordhr_pkg_verify_base.XXXXXX)"
TMP_EXPAND_DIR="$TMP_VERIFY_BASE/expanded"
trap 'rm -rf "$TMP_VERIFY_BASE"' EXIT
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
