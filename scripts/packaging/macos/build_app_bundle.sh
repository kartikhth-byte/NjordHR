#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
APP_NAME="NjordHR"
APP_DIR="$BUILD_DIR/${APP_NAME}.app"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>NjordHR</string>
  <key>CFBundleExecutable</key>
  <string>NjordHRLauncher</string>
  <key>CFBundleIdentifier</key>
  <string>com.njordhr.desktop</string>
  <key>CFBundleName</key>
  <string>NjordHR</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>
EOF

cat > "$APP_DIR/Contents/MacOS/NjordHRLauncher" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$PROJECT_DIR"
exec "\$PROJECT_DIR/scripts/start_njordhr.sh"
EOF

chmod +x "$APP_DIR/Contents/MacOS/NjordHRLauncher"

if [[ -f "$PROJECT_DIR/Truncated_Njord_logo.jpg" ]]; then
  cp "$PROJECT_DIR/Truncated_Njord_logo.jpg" "$APP_DIR/Contents/Resources/"
fi

echo "[NjordHR] App bundle created:"
echo "  $APP_DIR"

