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
DEFAULT_RUNTIME_ENV="$PAYLOAD_DIR/default_runtime.env"

command -v osacompile >/dev/null 2>&1 || { echo "osacompile not found."; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "rsync not found."; exit 1; }

mkdir -p "$BUILD_DIR"

TMP_SCPT="$(mktemp /tmp/njordhr_launcher.XXXXXX.scpt)"
trap 'rm -f "$TMP_SCPT"' EXIT

cat > "$TMP_SCPT" <<EOF
on run
  set appBundlePath to POSIX path of (path to me)
  set launcherPath to appBundlePath & "Contents/Resources/run_njordhr.sh"
  «event sysoexec» quoted form of launcherPath
end run
EOF

rm -rf "$APP_DIR"
osacompile -o "$APP_DIR" "$TMP_SCPT"

/usr/bin/python3 - "$APP_DIR/Contents/Resources/applet.icns" <<'PY'
from PIL import Image, ImageDraw
import math
import sys

out = sys.argv[1]

# Brand palette
orange = (245, 124, 0, 255)
navy = (7, 33, 84, 255)

size = 1024
img = Image.new("RGBA", (size, size), orange)
draw = ImageDraw.Draw(img)

cx, cy = size // 2, size // 2

# Anchor proportions
stroke = int(size * 0.085)
shaft_top = int(size * 0.18)
shaft_bottom = int(size * 0.70)
crossbar_y = int(size * 0.37)
crossbar_half = int(size * 0.16)
ring_r_outer = int(size * 0.12)
ring_r_inner = int(size * 0.065)
arc_bbox = (
    int(size * 0.23),
    int(size * 0.42),
    int(size * 0.77),
    int(size * 0.94),
)

# Shaft
draw.line((cx, shaft_top, cx, shaft_bottom), fill=navy, width=stroke)

# Crossbar
draw.line((cx - crossbar_half, crossbar_y, cx + crossbar_half, crossbar_y), fill=navy, width=stroke)

# Top ring
draw.ellipse(
    (cx - ring_r_outer, shaft_top - ring_r_outer, cx + ring_r_outer, shaft_top + ring_r_outer),
    fill=navy
)
draw.ellipse(
    (cx - ring_r_inner, shaft_top - ring_r_inner, cx + ring_r_inner, shaft_top + ring_r_inner),
    fill=orange
)

# Bottom arc
draw.arc(arc_bbox, start=205, end=-25, fill=navy, width=stroke)

# Flukes (triangles)
left_tip = (int(size * 0.23), int(size * 0.83))
left_inner = (int(size * 0.39), int(size * 0.75))
left_base = (int(size * 0.33), int(size * 0.89))
draw.polygon([left_tip, left_inner, left_base], fill=navy)

right_tip = (int(size * 0.77), int(size * 0.83))
right_inner = (int(size * 0.61), int(size * 0.75))
right_base = (int(size * 0.67), int(size * 0.89))
draw.polygon([right_tip, right_inner, right_base], fill=navy)

# Crown join at bottom shaft
draw.ellipse((cx - stroke // 2, shaft_bottom - stroke // 2, cx + stroke // 2, shaft_bottom + stroke // 2), fill=navy)

img.save(
    out,
    format="ICNS",
    sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)],
)
PY

mkdir -p "$PAYLOAD_DIR"

echo "[NjordHR] Copying app payload..."
rsync -a \
  --exclude ".git" \
  --exclude ".env" \
  --exclude ".env.*" \
  --exclude "config.ini" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".pycache_tmp" \
  --exclude "build" \
  --exclude "release" \
  --exclude "Verified_Resumes" \
  --exclude "logs" \
  --exclude "logs/runtime" \
  --exclude "*.db" \
  --exclude "*.sqlite" \
  --exclude "*.sqlite3" \
  --exclude "*.csv" \
  --exclude "*.db-journal" \
  --exclude "Backup_*" \
  --exclude "AI_Search_Results" \
  --exclude "NjordHR.bbprojectd" \
  "$PROJECT_DIR/" "$PAYLOAD_DIR/"

# Build-time provisioning for seamless first-run in internal deployments.
# Set these env vars before running build_app_bundle.sh:
#   NJORDHR_DEFAULT_SUPABASE_URL
#   NJORDHR_DEFAULT_SUPABASE_SECRET_KEY
#   NJORDHR_DEFAULT_AUTH_MODE (default: cloud)
#   NJORDHR_DEFAULT_USE_SUPABASE_DB (default: true)
#   NJORDHR_DEFAULT_USE_SUPABASE_READS (default: true)
#   NJORDHR_DEFAULT_USE_DUAL_WRITE (default: false)
#   NJORDHR_DEFAULT_USE_LOCAL_AGENT (default: true)
cat > "$DEFAULT_RUNTIME_ENV" <<EOF
USE_SUPABASE_DB=${NJORDHR_DEFAULT_USE_SUPABASE_DB:-true}
USE_SUPABASE_READS=${NJORDHR_DEFAULT_USE_SUPABASE_READS:-true}
USE_DUAL_WRITE=${NJORDHR_DEFAULT_USE_DUAL_WRITE:-false}
USE_LOCAL_AGENT=${NJORDHR_DEFAULT_USE_LOCAL_AGENT:-true}
NJORDHR_AUTH_MODE=${NJORDHR_DEFAULT_AUTH_MODE:-cloud}
NJORDHR_PASSWORD_HASH_METHOD=${NJORDHR_DEFAULT_PASSWORD_HASH_METHOD:-pbkdf2:sha256:600000}
SUPABASE_URL=${NJORDHR_DEFAULT_SUPABASE_URL:-}
SUPABASE_SECRET_KEY=${NJORDHR_DEFAULT_SUPABASE_SECRET_KEY:-}
EOF

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
APP_SUPPORT_DIR="${HOME}/Library/Application Support/NjordHR"
RUNTIME_DIR="${APP_SUPPORT_DIR}/runtime"
CONFIG_PATH="${APP_SUPPORT_DIR}/config.ini"
DEFAULT_DOWNLOAD_DIR="${HOME}/Documents/NjordHR/Downloads"
DEFAULT_VERIFIED_DIR="${APP_SUPPORT_DIR}/Verified_Resumes"
DEFAULT_LOG_DIR="${APP_SUPPORT_DIR}/logs"

mkdir -p "$APP_SUPPORT_DIR" "$RUNTIME_DIR" "$DEFAULT_DOWNLOAD_DIR" "$DEFAULT_VERIFIED_DIR" "$DEFAULT_LOG_DIR"

if [[ ! -f "$CONFIG_PATH" ]]; then
  if [[ -f "$PROJECT_DIR/config.ini" ]]; then
    cp "$PROJECT_DIR/config.ini" "$CONFIG_PATH"
  elif [[ -f "$PROJECT_DIR/config.example.ini" ]]; then
    cp "$PROJECT_DIR/config.example.ini" "$CONFIG_PATH"
  fi
fi

if [[ -f "$CONFIG_PATH" ]]; then
  /usr/bin/python3 - "$CONFIG_PATH" "$PROJECT_DIR" "$DEFAULT_DOWNLOAD_DIR" "$DEFAULT_VERIFIED_DIR" "$DEFAULT_LOG_DIR" <<'PY'
import configparser
import os
import sys

cfg_path, project_dir, download_dir, verified_dir, log_dir = sys.argv[1:6]
project_dir = os.path.abspath(project_dir)

cfg = configparser.ConfigParser()
cfg.read(cfg_path)

if "Settings" not in cfg:
    cfg["Settings"] = {}
if "Advanced" not in cfg:
    cfg["Advanced"] = {}
if "Credentials" not in cfg:
    cfg["Credentials"] = {}

def _norm(v):
    return os.path.abspath(os.path.expanduser((v or "").strip())) if (v or "").strip() else ""

def _is_bundle_or_relative_path(raw):
    raw = (raw or "").strip()
    if not raw:
        return True
    lowered = raw.lower()
    if "change_me" in lowered or "your_" in lowered or "/absolute/path/" in lowered:
        return True
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        return True
    abs_path = os.path.abspath(expanded)
    if abs_path.startswith(project_dir):
        return True
    if "/Applications/NjordHR.app/" in abs_path:
        return True
    if "/build/macos/NjordHR.app/" in abs_path:
        return True
    return False

current_download = cfg["Settings"].get("default_download_folder", "")
if _is_bundle_or_relative_path(current_download):
    cfg["Settings"]["default_download_folder"] = _norm(download_dir)

current_verified = cfg["Settings"].get("additional_local_folder", "")
if _is_bundle_or_relative_path(current_verified):
    cfg["Settings"]["additional_local_folder"] = _norm(verified_dir)

current_log_dir = cfg["Advanced"].get("log_dir", "")
if _is_bundle_or_relative_path(current_log_dir):
    cfg["Advanced"]["log_dir"] = _norm(log_dir)

with open(cfg_path, "w", encoding="utf-8") as fh:
    cfg.write(fh)
PY
fi

export NJORDHR_CONFIG_PATH="$CONFIG_PATH"
export NJORDHR_RUNTIME_DIR="$RUNTIME_DIR"

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
