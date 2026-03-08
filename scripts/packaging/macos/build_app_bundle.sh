#!/usr/bin/env bash
set -euo pipefail

# macOS ships Bash 3.2 by default; this script uses constructs that are unstable
# there under heavy process substitution/pipeline workloads.
if [[ "${BASH_VERSINFO[0]:-0}" -lt 4 ]]; then
  echo "[NjordHR] ERROR: Bash 4+ is required for macOS bundle build."
  echo "[NjordHR] Current bash: ${BASH_VERSION:-unknown}"
  echo "[NjordHR] Install Homebrew bash and rerun with:"
  echo "  /opt/homebrew/bin/bash scripts/packaging/macos/build_app_bundle.sh"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build/macos"
APP_NAME="NjordHR"
APP_DIR="$BUILD_DIR/${APP_NAME}.app"
APP_BUNDLE_ID="${NJORDHR_APP_BUNDLE_ID:-com.njordhr.desktop.localapp}"
EMBED_RUNTIME="${NJORDHR_EMBED_RUNTIME:-true}"
BUILD_PYTHON_BIN="${NJORDHR_BUILD_PYTHON_BIN:-}"
PAYLOAD_DIR="$APP_DIR/Contents/Resources/app"
RUNTIME_DIR="$APP_DIR/Contents/Resources/runtime"
RUN_SCRIPT="$APP_DIR/Contents/Resources/run_njordhr.sh"
DEFAULT_RUNTIME_ENV="$PAYLOAD_DIR/default_runtime.env"

command -v osacompile >/dev/null 2>&1 || { echo "osacompile not found."; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "rsync not found."; exit 1; }

mkdir -p "$BUILD_DIR"
RELOCATE_SEEN_FILE="$(mktemp /tmp/njordhr_relocate_seen.XXXXXX)"

supports_copy_venv() {
  local pybin="$1"
  local tmpvenv
  tmpvenv="$(mktemp -d /tmp/njordhr_venvprobe.XXXXXX)"
  if "$pybin" -m venv --copies "$tmpvenv" >/dev/null 2>&1; then
    rm -rf "$tmpvenv"
    return 0
  fi
  rm -rf "$tmpvenv"
  return 1
}

resolve_build_python() {
  if [[ -n "$BUILD_PYTHON_BIN" ]]; then
    if command -v "$BUILD_PYTHON_BIN" >/dev/null 2>&1 && supports_copy_venv "$BUILD_PYTHON_BIN"; then
      echo "$BUILD_PYTHON_BIN"
      return 0
    fi
    echo ""
    return 1
  fi

  local candidates=()
  while IFS= read -r p; do
    [[ -n "$p" ]] && candidates+=("$p")
  done < <(command -v -a python3 2>/dev/null || true)

  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
    [[ -x "$p" ]] && candidates+=("$p")
  done

  local seen=""
  for pybin in "${candidates[@]}"; do
    [[ -z "$pybin" ]] && continue
    if grep -q "|$pybin|" <<<"$seen"; then
      continue
    fi
    seen="${seen}|$pybin|"
    if supports_copy_venv "$pybin"; then
      echo "$pybin"
      return 0
    fi
  done
  echo ""
  return 1
}

bundle_embedded_python_framework() {
  local venv_python="$1"
  local dep_line dep_path version src_framework dst_framework rel_target
  dep_line="$(
    otool -L "$venv_python" 2>/dev/null \
      | tail -n +2 \
      | awk '{print $1}' \
      | grep -E 'Python\.framework/Versions/.*/Python' \
      | head -n 1
  )"
  [[ -z "$dep_line" ]] && return 0
  dep_path="$dep_line"
  if [[ "$dep_path" != /* ]]; then
    return 0
  fi
  if [[ ! -f "$dep_path" ]]; then
    echo "[NjordHR] WARN: Python framework dependency path not found on build host: $dep_path"
    return 0
  fi
  version="${dep_path##*/Versions/}"
  version="${version%%/*}"
  src_framework="${dep_path%%/Versions/*}/"
  src_framework="${src_framework%/}"
  if [[ ! -d "$src_framework" ]]; then
    echo "[NjordHR] WARN: Python framework directory not found: $src_framework"
    return 0
  fi
  dst_framework="$RUNTIME_DIR/Frameworks/Python.framework"
  mkdir -p "$RUNTIME_DIR/Frameworks"
  rm -rf "$dst_framework"
  cp -R "$src_framework" "$dst_framework"
  # Keep only selected framework version to avoid dragging older versions
  # (e.g. 3.11) that may contain absolute host links.
  if [[ -d "$dst_framework/Versions" ]]; then
    find "$dst_framework/Versions" -mindepth 1 -maxdepth 1 -type d ! -name "$version" -exec rm -rf {} +
    ln -sfn "$version" "$dst_framework/Versions/Current"
  fi
  echo "[NjordHR] Bundled Python.framework from: $src_framework"
  echo "[NjordHR] Bundled Python.framework into: $dst_framework"
  if [[ ! -f "$dst_framework/Versions/$version/Python" ]]; then
    echo "[NjordHR] WARN: Bundled framework missing expected binary for version $version"
    return 0
  fi

  # Rewrite python binary linkage to use bundled framework path.
  # runtime/bin executables: use path relative to runtime/bin
  local rel_target_bin="@executable_path/../Frameworks/Python.framework/Versions/$version/Python"
  for pybin in "$RUNTIME_DIR/bin/python3" "$RUNTIME_DIR/bin/python3.11" "$RUNTIME_DIR/bin/python"; do
    if [[ -x "$pybin" ]] && otool -L "$pybin" | awk '{print $1}' | grep -qx "$dep_path"; then
      install_name_tool -change "$dep_path" "$rel_target_bin" "$pybin" || {
        echo "[NjordHR] WARN: Failed to rewrite framework path in $pybin"
      }
    fi
  done

  # Python.app executable inside bundled framework: point at sibling Python dylib.
  local framework_py_app="$dst_framework/Versions/$version/Resources/Python.app/Contents/MacOS/Python"
  local rel_target_framework="@executable_path/../../../../Python"
  if [[ -x "$framework_py_app" ]] && otool -L "$framework_py_app" | awk '{print $1}' | grep -qx "$dep_path"; then
    install_name_tool -change "$dep_path" "$rel_target_framework" "$framework_py_app" || {
      echo "[NjordHR] WARN: Failed to rewrite framework path in $framework_py_app"
    }
  fi

  # Framework bin executables (python3, python3.11, etc.) should link to ../Python.
  local framework_bin_dir="$dst_framework/Versions/$version/bin"
  if [[ -d "$framework_bin_dir" ]]; then
    find "$framework_bin_dir" -maxdepth 1 -type f -perm -111 2>/dev/null \
      | while IFS= read -r fexe; do
          if otool -L "$fexe" 2>/dev/null | awk '{print $1}' | grep -qx "$dep_path"; then
            install_name_tool -change "$dep_path" "@executable_path/../Python" "$fexe" || {
              echo "[NjordHR] WARN: Failed to rewrite framework bin path in $fexe"
            }
          fi
        done
  fi

  # Validate no absolute host dependencies remain in runtime binaries/extensions.
  local bad_refs
  bad_refs="$(
    find "$RUNTIME_DIR" -type f \( -perm -111 -o -name "*.so" -o -name "*.dylib" \) 2>/dev/null \
      | while IFS= read -r exe; do
          if otool -L "$exe" 2>/dev/null | awk '{print $1}' | grep -qE '^((/opt/homebrew|/usr/local)/(opt|Cellar)/|/Library/Frameworks/Python\.framework/)'; then
            echo "$exe"
          fi
        done
  )"
  if [[ -n "${bad_refs:-}" ]]; then
    echo "[NjordHR] ERROR: Embedded runtime still has absolute host references:"
    echo "$bad_refs"
    echo "[NjordHR] Build is not portable; aborting."
    exit 1
  fi
}

rewrite_copied_dylib_deps() {
  local dylib="$1"
  local dep dep_base dep_copy dep_dir
  dep_dir="$(dirname "$dylib")"
  if grep -Fxq "$dylib" "$RELOCATE_SEEN_FILE" 2>/dev/null; then
    return 0
  fi
  echo "$dylib" >> "$RELOCATE_SEEN_FILE"

  if ! install_name_tool -id "@loader_path/$(basename "$dylib")" "$dylib" >/dev/null 2>&1; then
    echo "[NjordHR] WARN: Failed to set install_name id for $dylib"
  fi

  while IFS= read -r dep; do
    [[ -n "$dep" ]] || continue
    [[ -f "$dep" ]] || continue
    dep_base="$(basename "$dep")"
    dep_copy="$dep_dir/$dep_base"
    if [[ ! -f "$dep_copy" ]]; then
      cp -f "$dep" "$dep_copy"
      chmod 755 "$dep_copy" >/dev/null 2>&1 || true
    fi
    if ! install_name_tool -change "$dep" "@loader_path/$dep_base" "$dylib"; then
      echo "[NjordHR] ERROR: Failed to rewrite dylib dependency in $dylib"
      echo "  from: $dep"
      echo "  to:   @loader_path/$dep_base"
      exit 1
    fi
    if otool -L "$dylib" 2>/dev/null | awk '{print $1}' | grep -qx "$dep"; then
      echo "[NjordHR] ERROR: Dylib dependency rewrite did not apply in $dylib"
      echo "  still references: $dep"
      exit 1
    fi
    rewrite_copied_dylib_deps "$dep_copy"
  done < <(
    otool -L "$dylib" 2>/dev/null \
      | tail -n +2 \
      | awk '{print $1}' \
      | grep -E '^((/opt/homebrew|/usr/local)/(opt|Cellar)/.*/lib/.*\.dylib|/Library/Frameworks/Python\.framework/Versions/[^/]+/lib/.*\.dylib)$' || true
  )
}

relocate_external_dylib_deps() {
  local root="$1"
  local bin dep dep_base dep_dir dep_copy
  find "$root" -type f \( -perm -111 -o -name "*.so" -o -name "*.dylib" \) 2>/dev/null \
    | while IFS= read -r bin; do
        while IFS= read -r dep; do
          [[ -n "$dep" ]] || continue
          [[ -f "$dep" ]] || continue
          dep_base="$(basename "$dep")"
          dep_dir="$(dirname "$bin")/.njordhr_deps"
          mkdir -p "$dep_dir"
          dep_copy="$dep_dir/$dep_base"
          if [[ ! -f "$dep_copy" ]]; then
            cp -f "$dep" "$dep_copy"
            chmod 755 "$dep_copy" >/dev/null 2>&1 || true
          fi
          if ! install_name_tool -change "$dep" "@loader_path/.njordhr_deps/$dep_base" "$bin"; then
            echo "[NjordHR] ERROR: Failed to rewrite binary dependency in $bin"
            echo "  from: $dep"
            echo "  to:   @loader_path/.njordhr_deps/$dep_base"
            exit 1
          fi
          if otool -L "$bin" 2>/dev/null | awk '{print $1}' | grep -qx "$dep"; then
            echo "[NjordHR] ERROR: Binary dependency rewrite did not apply in $bin"
            echo "  still references: $dep"
            exit 1
          fi
          rewrite_copied_dylib_deps "$dep_copy"
        done < <(
          otool -L "$bin" 2>/dev/null \
            | tail -n +2 \
            | awk '{print $1}' \
            | grep -E '^((/opt/homebrew|/usr/local)/(opt|Cellar)/.*/lib/.*\.dylib|/Library/Frameworks/Python\.framework/Versions/[^/]+/lib/.*\.dylib)$' || true
        )
      done
}

rewrite_framework_absolute_refs() {
  local runtime_root="$1"
  local version="$2"
  local src_versions_prefix="/Library/Frameworks/Python.framework/Versions/"
  local dst_versions_prefix="$runtime_root/Frameworks/Python.framework/Versions/"
  local bin dep rel target newdep rest
  find "$runtime_root" -type f \( -perm -111 -o -name "*.so" -o -name "*.dylib" \) 2>/dev/null \
    | while IFS= read -r bin; do
        while IFS= read -r dep; do
          [[ -n "$dep" ]] || continue
          [[ "$dep" == "$src_versions_prefix"* ]] || continue
          rest="${dep#$src_versions_prefix}"
          target="$dst_versions_prefix$rest"
          [[ -f "$target" ]] || continue
          rel="$(/usr/bin/python3 - <<PY
import os
print(os.path.relpath("$target", os.path.dirname("$bin")))
PY
)"
          newdep="@loader_path/$rel"
          if ! install_name_tool -change "$dep" "$newdep" "$bin"; then
            echo "[NjordHR] ERROR: Failed to rewrite framework absolute dependency in $bin"
            echo "  from: $dep"
            echo "  to:   $newdep"
            exit 1
          fi
          if otool -L "$bin" 2>/dev/null | awk '{print $1}' | grep -qx "$dep"; then
            echo "[NjordHR] ERROR: Framework absolute dependency rewrite did not apply in $bin"
            echo "  still references: $dep"
            exit 1
          fi
        done < <(otool -L "$bin" 2>/dev/null | tail -n +2 | awk '{print $1}')
      done
}

TMP_SCPT="$(mktemp /tmp/njordhr_launcher.XXXXXX.scpt)"
trap 'rm -f "$TMP_SCPT" "$RELOCATE_SEEN_FILE"' EXIT

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
  BUILD_PYTHON_BIN="$(resolve_build_python || true)"
  if [[ -z "$BUILD_PYTHON_BIN" ]]; then
    echo "[NjordHR] No usable Python found for copy-based venv."
    echo "[NjordHR] Install python.org/Homebrew Python and/or set:"
    echo "  export NJORDHR_BUILD_PYTHON_BIN=/path/to/python3"
    exit 1
  fi
  echo "[NjordHR] Using build python: $BUILD_PYTHON_BIN"
  # Use --copies so runtime/bin/python3 is a real bundled binary, not a symlink
  # to the builder machine's CommandLineTools path.
  "$BUILD_PYTHON_BIN" -m venv --copies "$RUNTIME_DIR"
  "$RUNTIME_DIR/bin/pip" install --upgrade pip setuptools wheel >/dev/null
  "$RUNTIME_DIR/bin/pip" install -r "$PAYLOAD_DIR/requirements.txt" >/dev/null
  bundle_embedded_python_framework "$RUNTIME_DIR/bin/python3"
  relocate_external_dylib_deps "$RUNTIME_DIR"
  for vdir in "$RUNTIME_DIR"/Frameworks/Python.framework/Versions/3.*; do
    [[ -d "$vdir" ]] || continue
    vname="$(basename "$vdir")"
    rewrite_framework_absolute_refs "$RUNTIME_DIR" "$vname"
  done
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

# Prefer framework python when bundled; it is more portable than venv launchers
# across machines when the framework is relocated into the app bundle.
# IMPORTANT: derive version from runtime/bin/python3 so we don't accidentally
# bind to an unrelated Versions/Current symlink (e.g. 3.11 vs 3.13 mismatch).
RUNTIME_PY_BIN="$APP_RES_DIR/runtime/bin/python3"
PY_MM=""
if [[ -x "$RUNTIME_PY_BIN" ]]; then
  PY_MM="$("$RUNTIME_PY_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
fi

FRAMEWORK_HOME=""
if [[ -n "${PY_MM:-}" && ( -x "$APP_RES_DIR/runtime/Frameworks/Python.framework/Versions/${PY_MM}/bin/python3" || -x "$APP_RES_DIR/runtime/Frameworks/Python.framework/Versions/${PY_MM}/bin/python${PY_MM}" ) ]]; then
  FRAMEWORK_HOME="$APP_RES_DIR/runtime/Frameworks/Python.framework/Versions/${PY_MM}"
elif [[ -x "$APP_RES_DIR/runtime/Frameworks/Python.framework/Versions/Current/bin/python3" ]]; then
  FRAMEWORK_HOME="$APP_RES_DIR/runtime/Frameworks/Python.framework/Versions/Current"
fi

if [[ -n "$FRAMEWORK_HOME" && ( -x "$FRAMEWORK_HOME/bin/python3" || ( -n "${PY_MM:-}" && -x "$FRAMEWORK_HOME/bin/python${PY_MM}" ) ) ]]; then
  if [[ -z "${PY_MM:-}" ]]; then
    PY_MM="$(find "$FRAMEWORK_HOME/lib" -maxdepth 1 -type d -name 'python*.*' | xargs -n1 basename | head -n1 | sed 's/^python//')"
  fi
  export PYTHONHOME="$FRAMEWORK_HOME"
  if [[ -n "${PY_MM:-}" ]]; then
    export PYTHONPATH="$APP_RES_DIR/runtime/lib/python${PY_MM}/site-packages:$FRAMEWORK_HOME/lib/python${PY_MM}/site-packages"
  fi
  export PYTHONNOUSERSITE=1
  export PATH="$FRAMEWORK_HOME/bin:$APP_RES_DIR/runtime/bin:$PATH"
  if [[ -n "${PY_MM:-}" && -x "$FRAMEWORK_HOME/bin/python${PY_MM}" ]]; then
    PRIMARY_PYTHON_BIN="$FRAMEWORK_HOME/bin/python${PY_MM}"
  else
    PRIMARY_PYTHON_BIN="$FRAMEWORK_HOME/bin/python3"
  fi
else
  PRIMARY_PYTHON_BIN="$RUNTIME_PY_BIN"
fi

# Prefer embedded runtime for all first-run bootstrap work to avoid requiring
# Xcode Command Line Tools on target machines.
PY_BOOTSTRAP_BIN="$PRIMARY_PYTHON_BIN"
if [[ ! -x "$PY_BOOTSTRAP_BIN" ]]; then
  if [[ "${NJORDHR_ALLOW_SYSTEM_PYTHON_BOOTSTRAP:-false}" == "true" ]]; then
    PY_BOOTSTRAP_BIN="/usr/bin/python3"
  else
    echo "[NjordHR] Embedded runtime missing at $APP_RES_DIR/runtime/bin/python3"
    echo "[NjordHR] Reinstall NjordHR package built with embedded runtime."
    exit 1
  fi
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  if [[ -f "$PROJECT_DIR/config.ini" ]]; then
    cp "$PROJECT_DIR/config.ini" "$CONFIG_PATH"
  elif [[ -f "$PROJECT_DIR/config.example.ini" ]]; then
    cp "$PROJECT_DIR/config.example.ini" "$CONFIG_PATH"
  fi
fi

if [[ -d "$APP_RES_DIR/runtime/Frameworks" ]]; then
  export DYLD_FRAMEWORK_PATH="$APP_RES_DIR/runtime/Frameworks${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
fi

if [[ -f "$CONFIG_PATH" ]]; then
  "$PY_BOOTSTRAP_BIN" - "$CONFIG_PATH" "$PROJECT_DIR" "$DEFAULT_DOWNLOAD_DIR" "$DEFAULT_VERIFIED_DIR" "$DEFAULT_LOG_DIR" <<'PY'
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

if [[ -x "$PRIMARY_PYTHON_BIN" ]]; then
  export NJORDHR_PYTHON_BIN="$PRIMARY_PYTHON_BIN"
elif [[ "${NJORDHR_ALLOW_SYSTEM_PYTHON_BOOTSTRAP:-false}" == "true" ]]; then
  export NJORDHR_PYTHON_BIN="/usr/bin/python3"
else
  echo "[NjordHR] Embedded runtime missing at $PRIMARY_PYTHON_BIN"
  echo "[NjordHR] Reinstall NjordHR package built with embedded runtime."
  exit 1
fi

# Force requests/urllib3 CA bundle to embedded runtime certifi, preventing
# stale host-level SSL_CERT_FILE/REQUESTS_CA_BUNDLE paths from breaking TLS.
if [[ -x "${NJORDHR_PYTHON_BIN:-}" ]]; then
  NJORDHR_CERTIFI_CA="$("$NJORDHR_PYTHON_BIN" - <<'PY'
import certifi
print(certifi.where())
PY
)"
  if [[ -n "${NJORDHR_CERTIFI_CA:-}" && -f "$NJORDHR_CERTIFI_CA" ]]; then
    export SSL_CERT_FILE="$NJORDHR_CERTIFI_CA"
    export REQUESTS_CA_BUNDLE="$NJORDHR_CERTIFI_CA"
  fi
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

rm -f "$RELOCATE_SEEN_FILE" >/dev/null 2>&1 || true
