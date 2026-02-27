#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(date +%Y.%m.%d.%H%M)}"
RELEASE_DIR="$PROJECT_DIR/release/$VERSION"

mkdir -p "$RELEASE_DIR"
rm -f "$RELEASE_DIR/checksums.txt" "$RELEASE_DIR/manifest.json"

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

artifacts=()
while IFS= read -r artifact; do
  artifacts+=("$artifact")
done < <(find "$RELEASE_DIR" -maxdepth 1 -type f ! -name "checksums.txt" ! -name "manifest.json" -print | sort)
artifact_count="${#artifacts[@]}"
if [[ "$artifact_count" -eq 0 ]]; then
  echo "[NjordHR] No artifacts found in build/ folders. Build installers first."
  exit 1
fi

(
  cd "$RELEASE_DIR"
  for f in "${artifacts[@]}"; do
    bn="$(basename "$f")"
    shasum -a 256 "$bn"
  done > checksums.txt
)

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
    if name in {"checksums.txt", "manifest.json"}:
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
