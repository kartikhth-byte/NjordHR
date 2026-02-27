#!/usr/bin/env bash
set -euo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="com.njordhr.local"
PLIST_PATH="$LAUNCH_AGENTS_DIR/${PLIST_LABEL}.plist"
APP_SUPPORT_DIR="$HOME/Library/Application Support/NjordHR"
RUNTIME_DIR="$APP_SUPPORT_DIR/runtime"
APP_PATH_SYSTEM="/Applications/NjordHR.app"
APP_PATH_USER="$HOME/Applications/NjordHR.app"

if [[ -x "$APP_PATH_SYSTEM/Contents/Resources/run_njordhr.sh" ]]; then
  RUN_SCRIPT="$APP_PATH_SYSTEM/Contents/Resources/run_njordhr.sh"
elif [[ -x "$APP_PATH_USER/Contents/Resources/run_njordhr.sh" ]]; then
  RUN_SCRIPT="$APP_PATH_USER/Contents/Resources/run_njordhr.sh"
else
  echo "[NjordHR] Installed app not found."
  echo "[NjordHR] Expected one of:"
  echo "  $APP_PATH_SYSTEM"
  echo "  $APP_PATH_USER"
  echo "[NjordHR] Install the app first, then retry."
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$RUNTIME_DIR" "$APP_SUPPORT_DIR"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUN_SCRIPT</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$APP_SUPPORT_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$RUNTIME_DIR/launchagent.out</string>
  <key>StandardErrorPath</key>
  <string>$RUNTIME_DIR/launchagent.err</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true

echo "[NjordHR] LaunchAgent installed: $PLIST_PATH"
echo "[NjordHR] It will auto-start at login and keep NjordHR services available."
