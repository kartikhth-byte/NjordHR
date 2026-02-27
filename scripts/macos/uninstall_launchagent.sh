#!/usr/bin/env bash
set -euo pipefail

PLIST_LABEL="com.njordhr.local"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [[ -f "$PLIST_PATH" ]]; then
  launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "[NjordHR] LaunchAgent removed."
else
  echo "[NjordHR] LaunchAgent not installed."
fi
