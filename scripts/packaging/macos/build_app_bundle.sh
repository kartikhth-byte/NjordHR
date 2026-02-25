#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
APP_NAME="NjordHR"
APP_DIR="$BUILD_DIR/${APP_NAME}.app"
APP_BUNDLE_ID="${NJORDHR_APP_BUNDLE_ID:-com.njordhr.desktop.localapp}"
EMBED_RUNTIME="${NJORDHR_EMBED_RUNTIME:-true}"
PAYLOAD_DIR="$APP_DIR/Contents/Resources/app"
RUNTIME_DIR="$APP_DIR/Contents/Resources/runtime"
RUN_SCRIPT="$APP_DIR/Contents/Resources/run_njordhr.sh"

command -v osacompile >/dev/null 2>&1 || { echo "osacompile not found."; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "rsync not found."; exit 1; }

mkdir -p "$BUILD_DIR"

TMP_SCPT="$(mktemp /tmp/njordhr_launcher.XXXXXX.scpt)"
trap 'rm -f "$TMP_SCPT"' EXIT

cat > "$TMP_SCPT" <<EOF
on run
  do shell script quoted form of POSIX path of "${RUN_SCRIPT}"
end run
EOF

rm -rf "$APP_DIR"
osacompile -o "$APP_DIR" "$TMP_SCPT"

mkdir -p "$PAYLOAD_DIR"

echo "[NjordHR] Copying app payload..."
rsync -a \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".pycache_tmp" \
  --exclude "build" \
  --exclude "logs/runtime" \
  --exclude "*.db-journal" \
  --exclude "Backup_*" \
  --exclude "AI_Search_Results" \
  --exclude "NjordHR.bbprojectd" \
  "$PROJECT_DIR/" "$PAYLOAD_DIR/"

if [[ "$EMBED_RUNTIME" == "true" ]]; then
  echo "[NjordHR] Building embedded Python runtime (this may take a few minutes)..."
  rm -rf "$RUNTIME_DIR"
  /usr/bin/python3 -m venv "$RUNTIME_DIR"
  "$RUNTIME_DIR/bin/pip" install --upgrade pip setuptools wheel >/dev/null
  "$RUNTIME_DIR/bin/pip" install -r "$PAYLOAD_DIR/requirements.txt" >/dev/null
fi

cat > "$RUN_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
APP_RES_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$APP_RES_DIR/app"

if [[ -x "$APP_RES_DIR/runtime/bin/python3" ]]; then
  export NJORDHR_PYTHON_BIN="$APP_RES_DIR/runtime/bin/python3"
  export PATH="$APP_RES_DIR/runtime/bin:$PATH"
fi

exec "$PROJECT_DIR/scripts/start_njordhr.sh"
EOF
chmod +x "$RUN_SCRIPT"

if [[ -f "$PROJECT_DIR/Truncated_Njord_logo.jpg" ]]; then
  cp "$PROJECT_DIR/Truncated_Njord_logo.jpg" "$APP_DIR/Contents/Resources/"
fi

if [[ -f "$APP_DIR/Contents/Info.plist" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ${APP_BUNDLE_ID}" "$APP_DIR/Contents/Info.plist" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleName NjordHR" "$APP_DIR/Contents/Info.plist" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName NjordHR" "$APP_DIR/Contents/Info.plist" >/dev/null 2>&1 || true
fi

echo "[NjordHR] App bundle created:"
echo "  $APP_DIR"
if [[ "$EMBED_RUNTIME" == "true" ]]; then
  echo "[NjordHR] Embedded runtime: enabled"
else
  echo "[NjordHR] Embedded runtime: disabled"
fi

