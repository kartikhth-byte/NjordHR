#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(date +%Y.%m.%d.%H%M)}"
RELEASE_DIR="$PROJECT_DIR/release/$VERSION"

mkdir -p "$RELEASE_DIR"
rm -f "$RELEASE_DIR/checksums.txt" "$RELEASE_DIR/manifest.json" "$RELEASE_DIR/INSTALL.md"

copy_if_exists() {
  local src="$1"
  if [[ -f "$src" ]]; then
    cp "$src" "$RELEASE_DIR/"
    echo "[NjordHR] Added: $(basename "$src")"
  fi
}

# macOS artifacts
if ls "$PROJECT_DIR"/build/macos/NjordHR-*-unsigned.pkg >/dev/null 2>&1; then
  latest_macos_pkg="$(ls -t "$PROJECT_DIR"/build/macos/NjordHR-*-unsigned.pkg | head -n 1)"
  copy_if_exists "$latest_macos_pkg"
fi
copy_if_exists "$PROJECT_DIR/build/macos/NjordHR-unsigned.pkg"

# Windows artifacts
if ls "$PROJECT_DIR"/build/windows/NjordHR-*-setup.exe >/dev/null 2>&1; then
  latest_windows_setup="$(ls -t "$PROJECT_DIR"/build/windows/NjordHR-*-setup.exe | head -n 1)"
  copy_if_exists "$latest_windows_setup"
fi
if ls "$PROJECT_DIR"/build/windows/NjordHR-*-portable.zip >/dev/null 2>&1; then
  latest_windows_zip="$(ls -t "$PROJECT_DIR"/build/windows/NjordHR-*-portable.zip | head -n 1)"
  copy_if_exists "$latest_windows_zip"
fi
if ls "$PROJECT_DIR"/build/electron/NjordHR-Electron-*-win.exe >/dev/null 2>&1; then
  latest_windows_electron_setup="$(ls -t "$PROJECT_DIR"/build/electron/NjordHR-Electron-*-win.exe | head -n 1)"
  copy_if_exists "$latest_windows_electron_setup"
fi

artifacts=()
while IFS= read -r artifact; do
  artifacts+=("$artifact")
done < <(find "$RELEASE_DIR" -maxdepth 1 -type f ! -name "checksums.txt" ! -name "manifest.json" ! -name "INSTALL.md" -print | sort)
artifact_count="${#artifacts[@]}"
if [[ "$artifact_count" -eq 0 ]]; then
  echo "[NjordHR] No artifacts found in build/ folders. Build installers first."
  exit 1
fi

MAC_INSTALLER_NAME=""
WINDOWS_INSTALLER_NAME=""
for artifact in "${artifacts[@]}"; do
  bn="$(basename "$artifact")"
  if [[ -z "$MAC_INSTALLER_NAME" && "$bn" == *.pkg ]]; then
    MAC_INSTALLER_NAME="$bn"
  fi
  if [[ -z "$WINDOWS_INSTALLER_NAME" && "$bn" == *-win.exe ]]; then
    WINDOWS_INSTALLER_NAME="$bn"
  fi
done

(
  cd "$RELEASE_DIR"
  for f in "${artifacts[@]}"; do
    bn="$(basename "$f")"
    shasum -a 256 "$bn"
  done > checksums.txt
)

cat > "$RELEASE_DIR/INSTALL.md" <<EOF
# NjordHR Validation Build Install Notes

This release folder contains unsigned validation artifacts for macOS and Windows.

## macOS

1. Remove any old app and runtime state:
   ```bash
   sudo rm -rf "/Applications/NjordHR.app"
   rm -rf "$HOME/Library/Application Support/NjordHR"
   ```
2. Install the unsigned package:
   ```bash
   sudo installer -pkg "./${MAC_INSTALLER_NAME:-NjordHR-unsigned.pkg}" -target /
   xattr -dr com.apple.quarantine "/Applications/NjordHR.app" || true
   open -a "NjordHR"
   ```
3. Validate:
   ```bash
   cat "/Applications/NjordHR.app/Contents/Resources/app/default_runtime.env"
   cat "$HOME/Library/Application Support/NjordHR/runtime/runtime.env"
   curl -s http://127.0.0.1:5050/runtime/ready
   curl -s http://127.0.0.1:5051/health
   ```

Expected:
- `NJORDHR_AUTH_MODE=cloud`
- `USE_SUPABASE_DB=true`
- `USE_SUPABASE_READS=true`
- backend `/runtime/ready` returns `auth_mode: cloud`
- agent `/health` returns `status: ok`

## Windows

1. Remove any old install and runtime state:
   ```powershell
   Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\NjordHR" -ErrorAction SilentlyContinue
   Remove-Item -Recurse -Force "$env:APPDATA\NjordHR" -ErrorAction SilentlyContinue
   ```
2. Run the installer:
   ```powershell
   Start-Process ".\${WINDOWS_INSTALLER_NAME:-NjordHR-Electron-win.exe}" -Wait
   Start-Process "$env:LOCALAPPDATA\Programs\NjordHR\NjordHR.exe"
   ```
3. Validate:
   ```powershell
   Get-Content "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\default_runtime.env"
   Get-Content "$env:APPDATA\NjordHR\runtime\runtime.env"
   powershell -Command "Invoke-WebRequest http://127.0.0.1:5050/runtime/ready -UseBasicParsing | Select-Object -ExpandProperty Content"
   powershell -Command "Invoke-WebRequest http://127.0.0.1:5051/health -UseBasicParsing | Select-Object -ExpandProperty Content"
   ```

Expected:
- `NJORDHR_AUTH_MODE=cloud`
- `USE_SUPABASE_DB=true`
- `USE_SUPABASE_READS=true`
- backend `/runtime/ready` returns `auth_mode: cloud`
- agent `/health` returns `status: ok`

## Checksums

`checksums.txt` contains SHA-256 checksums for every artifact in this folder.
Verify them after copying artifacts to another machine.
EOF

export VERSION RELEASE_DIR
python3 - <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone

version = os.environ["VERSION"]
release_dir = os.environ["RELEASE_DIR"]

artifacts = []
for name in sorted(os.listdir(release_dir)):
    path = os.path.join(release_dir, name)
    if not os.path.isfile(path):
        continue
    if name in {"checksums.txt", "manifest.json", "INSTALL.md"}:
        continue
    with open(path, "rb") as fh:
        sha256 = hashlib.sha256(fh.read()).hexdigest()
    sig_path = f"{path}.sig"
    signature = ""
    if os.path.isfile(sig_path):
        try:
            with open(sig_path, "r", encoding="utf-8") as sfh:
                signature = sfh.read().strip()
        except Exception:
            signature = ""
    artifacts.append({
        "name": name,
        "size_bytes": os.path.getsize(path),
        "sha256": sha256,
        "signature": signature,
    })

manifest = {
    "version": version,
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "artifact_count": len(artifacts),
    "artifacts": artifacts,
}

with open(os.path.join(release_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
PY

echo "[NjordHR] Release bundle created:"
echo "  $RELEASE_DIR"
echo "[NjordHR] Files:"
ls -1 "$RELEASE_DIR"
