from flask import Flask, request, jsonify, send_from_directory, Response, send_file, redirect, has_request_context, session
from flask_cors import CORS
import csv
import io
import os
import re
import sys
import uuid
import json
import zipfile
import logging
import threading
import secrets
from datetime import datetime
from queue import Queue, Empty
import requests
from urllib.parse import quote
import sqlite3
import time
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

# Dependency Check & Imports
try:
    import PyPDF2
except ImportError:
    print("\n--- FATAL ERROR --- \nPlease run: pip install PyPDF2\n")
    sys.exit(1)

from scraper_engine import Scraper
from ai_analyzer import Analyzer
from logger_config import setup_logger
from resume_extractor import ResumeExtractor
from app_settings import load_app_settings, FeatureFlags
from repositories.repo_factory import build_candidate_event_repo
from repositories.supabase_candidate_event_repo import resolve_supabase_api_key

# --- App Initialization ---
app = Flask(__name__)
CORS(app) 
app.secret_key = os.getenv("NJORDHR_FLASK_SECRET", "").strip() or secrets.token_hex(32)

# --- Configuration ---
app_settings = load_app_settings()
config = app_settings.config
creds = app_settings.credentials
settings = app_settings.settings
feature_flags = app_settings.feature_flags

# --- Global State ---
scraper_session = None
seajobs_last_activity_at = None
ui_client_heartbeats = {}
ui_client_lock = threading.Lock()
ui_client_seen_once = False
auto_shutdown_started = False
auto_shutdown_in_progress = False
cloud_auth_state_cache = {"ts": 0, "mode": "local", "reason": "not_checked"}

# --- Initialize Extractors ---
resume_extractor = ResumeExtractor()
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _resolve_verified_resumes_dir():
    configured = settings.get('Additional_Local_Folder', fallback='Verified_Resumes').strip()
    if not configured:
        configured = 'Verified_Resumes'
    if os.path.isabs(configured):
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(PROJECT_ROOT, configured))


VERIFIED_RESUMES_DIR = _resolve_verified_resumes_dir()
csv_manager = build_candidate_event_repo(
    flags=feature_flags,
    base_folder=VERIFIED_RESUMES_DIR,
    server_url=app_settings.server_url
)


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _refresh_runtime_managers():
    global feature_flags, csv_manager, VERIFIED_RESUMES_DIR
    feature_flags = FeatureFlags(
        use_supabase_db=_env_bool("USE_SUPABASE_DB", default=False),
        use_dual_write=_env_bool("USE_DUAL_WRITE", default=False),
        use_supabase_reads=_env_bool("USE_SUPABASE_READS", default=False),
        use_local_agent=_env_bool("USE_LOCAL_AGENT", default=False),
        use_cloud_export=_env_bool("USE_CLOUD_EXPORT", default=False),
    )
    _load_runtime_secrets_from_cloud()
    VERIFIED_RESUMES_DIR = _resolve_verified_resumes_dir()
    os.makedirs(VERIFIED_RESUMES_DIR, exist_ok=True)
    csv_manager = build_candidate_event_repo(
        flags=feature_flags,
        base_folder=VERIFIED_RESUMES_DIR,
        server_url=app_settings.server_url
    )
    try:
        Analyzer._instance = None
    except Exception:
        pass


def _advanced_value(name, fallback=""):
    return config.get("Advanced", name, fallback=fallback)


def _credential_value(config_key, env_name, fallback=""):
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    return creds.get(config_key, fallback=fallback)


def _seajob_username():
    return _credential_value("Username", "SEAJOB_USERNAME", "")


def _seajob_password():
    return _credential_value("Password", "SEAJOB_PASSWORD", "")


def _gemini_api_key():
    return _credential_value("Gemini_API_Key", "GEMINI_API_KEY", "")


def _pinecone_api_key():
    return _credential_value("Pinecone_API_Key", "PINECONE_API_KEY", "")


def _supabase_runtime_config_endpoint():
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return "", {}
    return (
        f"{supabase_url}/rest/v1/app_runtime_config",
        {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
    )


def _supabase_runtime_config_get():
    endpoint, headers = _supabase_runtime_config_endpoint()
    if not endpoint:
        return {}
    try:
        resp = requests.get(
            endpoint,
            params={"select": "key,value", "limit": 2000},
            headers=headers,
            timeout=10,
        )
        if resp.status_code >= 400:
            return {}
        rows = resp.json() if resp.text else []
        out = {}
        for row in rows or []:
            key = str(row.get("key", "")).strip()
            if key:
                out[key] = str(row.get("value", "") or "")
        return out
    except Exception:
        return {}


def _supabase_runtime_config_set(pairs):
    endpoint, headers = _supabase_runtime_config_endpoint()
    if not endpoint:
        raise RuntimeError("Supabase runtime config unavailable")
    body = []
    now_iso = datetime.utcnow().isoformat() + "Z"
    for key, value in (pairs or {}).items():
        if key is None:
            continue
        key_s = str(key).strip()
        if not key_s:
            continue
        body.append({
            "key": key_s,
            "value": str(value or ""),
            "updated_at": now_iso,
        })
    if not body:
        return
    resp = requests.post(
        endpoint,
        params={"on_conflict": "key"},
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=body,
        timeout=12,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed writing cloud runtime config ({resp.status_code}): {resp.text}")


def _load_runtime_secrets_from_cloud():
    if not bool(getattr(feature_flags, "use_supabase_db", False)):
        return
    cfg = _supabase_runtime_config_get()
    mapping = {
        "seajob_username": "SEAJOB_USERNAME",
        "seajob_password": "SEAJOB_PASSWORD",
        "gemini_api_key": "GEMINI_API_KEY",
        "pinecone_api_key": "PINECONE_API_KEY",
    }
    for key, env_name in mapping.items():
        val = str(cfg.get(key, "")).strip()
        if val:
            os.environ[env_name] = val


# Best-effort one-time hydration at startup (safe no-op when cloud config is unavailable).
try:
    _load_runtime_secrets_from_cloud()
except Exception:
    pass


def _int_setting(section, key, fallback):
    try:
        return int(config.get(section, key, fallback=str(fallback)))
    except Exception:
        return int(fallback)


def _resolve_runtime_path(raw_path, fallback_name):
    candidate = str(raw_path or "").strip()
    if not candidate:
        candidate = fallback_name
    candidate = os.path.expanduser(candidate)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(PROJECT_ROOT, candidate))


def _release_root_dir():
    raw = os.getenv("NJORDHR_RELEASE_DIR", "").strip()
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return Path(PROJECT_ROOT) / "release"


def _release_public_base_url():
    return os.getenv("NJORDHR_UPDATE_BASE_URL", "").strip().rstrip("/") or f"{app_settings.server_url.rstrip('/')}/releases"


def _iter_release_versions(root: Path):
    if not root.exists() or not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / "manifest.json").exists():
            out.append(child.name)
    return sorted(out, reverse=True)


def _platform_from_filename(name: str):
    n = str(name or "").lower()
    if n.endswith(".pkg"):
        return "macos"
    if n.endswith("-setup.exe") or n.endswith(".msi"):
        return "windows"
    if n.endswith("-portable.zip") or (n.endswith(".zip") and "windows" in n):
        return "windows"
    return "all"


def _ui_idle_autoshutdown_enabled():
    return _env_bool("NJORDHR_AUTO_SHUTDOWN_ON_UI_IDLE", default=False)


def _ui_idle_shutdown_seconds():
    raw = os.getenv("NJORDHR_UI_IDLE_SHUTDOWN_SECONDS", "75")
    try:
        value = int(str(raw).strip())
    except Exception:
        value = 75
    return max(15, value)


def _ui_heartbeat_ttl_seconds():
    return max(15, _ui_idle_shutdown_seconds())


def _record_ui_heartbeat(client_id):
    global ui_client_seen_once
    cid = str(client_id or "").strip()
    if not cid:
        return
    with ui_client_lock:
        ui_client_heartbeats[cid] = time.time()
        ui_client_seen_once = True


def _drop_ui_client(client_id):
    cid = str(client_id or "").strip()
    if not cid:
        return
    with ui_client_lock:
        ui_client_heartbeats.pop(cid, None)


def _active_ui_client_count():
    now = time.time()
    ttl = _ui_heartbeat_ttl_seconds()
    with ui_client_lock:
        stale = [cid for cid, ts in ui_client_heartbeats.items() if (now - ts) > ttl]
        for cid in stale:
            ui_client_heartbeats.pop(cid, None)
        return len(ui_client_heartbeats)


def _start_ui_idle_shutdown_monitor():
    global auto_shutdown_started, auto_shutdown_in_progress
    if auto_shutdown_started or not _ui_idle_autoshutdown_enabled():
        return
    auto_shutdown_started = True

    def _worker():
        idle_seconds = _ui_idle_shutdown_seconds()
        # Wait for UI to initialize/open.
        time.sleep(15)
        while True:
            time.sleep(5)
            if auto_shutdown_in_progress:
                return
            if not ui_client_seen_once:
                continue
            if _active_ui_client_count() > 0:
                continue
            auto_shutdown_in_progress = True
            print(f"[AUTOSHUTDOWN] No active UI clients for {idle_seconds}s. Stopping services.")
            _disconnect_seajobs_best_effort()
            if _use_local_agent():
                try:
                    _agent_request("POST", "/shutdown", timeout=10)
                except Exception:
                    pass
            os._exit(0)

    t = threading.Thread(target=_worker, daemon=True, name="njordhr-ui-idle-shutdown")
    t.start()


def _seajobs_idle_timeout_seconds():
    return max(0, _int_setting("Advanced", "seajobs_idle_timeout_seconds", 300))


def _touch_seajobs_activity():
    global seajobs_last_activity_at
    seajobs_last_activity_at = time.time()


def _clear_seajobs_activity():
    global seajobs_last_activity_at
    seajobs_last_activity_at = None


def _current_seajobs_idle_seconds():
    if seajobs_last_activity_at is None:
        return None
    return max(0, int(time.time() - seajobs_last_activity_at))


def _disconnect_seajobs_best_effort():
    global scraper_session
    try:
        if _use_local_agent():
            _agent_request("POST", "/session/disconnect", timeout=20)
        else:
            if scraper_session:
                scraper_session.quit()
            scraper_session = None
    except Exception:
        return False
    finally:
        _clear_seajobs_activity()
    return True


def _enforce_seajobs_idle_timeout():
    timeout_seconds = _seajobs_idle_timeout_seconds()
    if timeout_seconds <= 0:
        return None
    idle_seconds = _current_seajobs_idle_seconds()
    if idle_seconds is None or idle_seconds < timeout_seconds:
        return None
    _disconnect_seajobs_best_effort()
    timeout_minutes = max(1, timeout_seconds // 60)
    return {
        "timed_out": True,
        "idle_seconds": idle_seconds,
        "timeout_seconds": timeout_seconds,
        "message": f"SeaJobs session disconnected after {timeout_minutes} minute(s) of inactivity."
    }


def _admin_token():
    token = os.getenv("NJORDHR_ADMIN_TOKEN", "").strip()
    if token:
        return token
    return config.get("Advanced", "admin_token", fallback="").strip()


def _is_placeholder_password(value):
    raw = str(value or "").strip()
    if not raw:
        return True
    upper = raw.upper()
    if upper in {"CHANGE_ME", "PASSWORD", "ADMIN", "REPLACE_ME"}:
        return True
    if upper.startswith("CHANGE_ME") or upper.startswith("YOUR_"):
        return True
    if "<" in raw or ">" in raw:
        return True
    if "replace-with-" in raw.lower():
        return True
    return False


def _auth_user_list_local(include_placeholder_passwords=False):
    auth_cfg = config["Auth"] if "Auth" in config else {}
    admin_username = os.getenv("NJORDHR_ADMIN_USERNAME", auth_cfg.get("admin_username", "admin")).strip() or "admin"
    admin_password = os.getenv("NJORDHR_ADMIN_PASSWORD", auth_cfg.get("admin_password", "")).strip() or _admin_token()
    manager_username = os.getenv("NJORDHR_MANAGER_USERNAME", auth_cfg.get("manager_username", "manager")).strip() or "manager"
    manager_password = os.getenv("NJORDHR_MANAGER_PASSWORD", auth_cfg.get("manager_password", "")).strip()
    recruiter_username = os.getenv("NJORDHR_RECRUITER_USERNAME", auth_cfg.get("recruiter_username", "recruiter")).strip() or "recruiter"
    recruiter_password = os.getenv("NJORDHR_RECRUITER_PASSWORD", auth_cfg.get("recruiter_password", "")).strip()

    users = {}
    if admin_password and (include_placeholder_passwords or not _is_placeholder_password(admin_password)):
        users[admin_username] = {"role": "admin", "password": admin_password}
    if manager_password and (include_placeholder_passwords or not _is_placeholder_password(manager_password)):
        users[manager_username] = {"role": "manager", "password": manager_password}
    if recruiter_password and (include_placeholder_passwords or not _is_placeholder_password(recruiter_password)):
        users[recruiter_username] = {"role": "recruiter", "password": recruiter_password}

    if "Users" in config:
        for username, packed in config["Users"].items():
            u = str(username or "").strip()
            if not u:
                continue
            raw = str(packed or "")
            if "|" in raw:
                role, pwd = raw.split("|", 1)
            else:
                role, pwd = "recruiter", raw
            role = str(role or "").strip().lower()
            pwd = str(pwd or "").strip()
            if role not in {"admin", "manager", "recruiter"} or not pwd:
                continue
            if not include_placeholder_passwords and _is_placeholder_password(pwd):
                continue
            users[u] = {"role": role, "password": pwd}
    return users


def _auth_mode_preference():
    raw = os.getenv("NJORDHR_AUTH_MODE", "auto").strip().lower()
    if raw in {"cloud", "local", "auto"}:
        return raw
    return "auto"


def _cloud_auth_required():
    """
    Enforce cloud auth whenever Supabase DB mode is enabled or auth is explicitly forced to cloud.
    This avoids cross-machine user drift from silent fallback to local auth.
    """
    return bool(getattr(feature_flags, "use_supabase_db", False)) or _auth_mode_preference() == "cloud"


def _password_hash_method():
    # Use PBKDF2 by default for compatibility with Python builds lacking hashlib.scrypt.
    return os.getenv("NJORDHR_PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000").strip() or "pbkdf2:sha256:600000"


def _hash_password(password):
    return generate_password_hash(password, method=_password_hash_method())


def _check_password(stored_hash, password):
    try:
        return bool(check_password_hash(stored_hash, password))
    except Exception as exc:
        # Common on older Python runtimes when stored hash uses scrypt.
        msg = str(exc).lower()
        if "scrypt" in msg or "hashlib" in msg:
            print(f"[AUTH] Unsupported password hash method in runtime: {exc}")
            return False
        raise


def _supabase_auth_endpoint():
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return "", {}
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    return f"{supabase_url}/rest/v1/users", headers


def _supabase_users_request(method="GET", params=None, json_body=None, timeout=12):
    endpoint, headers = _supabase_auth_endpoint()
    if not endpoint:
        raise RuntimeError("Supabase auth config missing")
    resp = requests.request(
        method=method,
        url=endpoint,
        headers=headers,
        params=params or {},
        json=json_body,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase users request failed {resp.status_code}: {resp.text}")
    if not resp.text:
        return []
    try:
        return resp.json()
    except Exception:
        return []


def _cloud_auth_state(force_refresh=False):
    now = time.time()
    ttl = 20
    if not force_refresh and (now - cloud_auth_state_cache.get("ts", 0)) < ttl:
        return dict(cloud_auth_state_cache)

    mode = "local"
    reason = "forced_local"
    pref = _auth_mode_preference()
    if pref == "local" and not _cloud_auth_required():
        mode, reason = "local", "forced_local"
    else:
        try:
            if not bool(getattr(feature_flags, "use_supabase_db", False)) and pref != "cloud":
                mode, reason = "local", "supabase_db_disabled"
            else:
                _supabase_users_request(
                    method="GET",
                    params={"select": "id,username,password_hash,role,is_active", "limit": "1"},
                    timeout=8,
                )
                mode, reason = "cloud", "ok"
        except Exception as exc:
            mode = "cloud_error" if _cloud_auth_required() else "local"
            reason = f"cloud_unavailable:{exc}"
    cloud_auth_state_cache.update({"ts": now, "mode": mode, "reason": reason})
    return dict(cloud_auth_state_cache)


def _auth_mode():
    mode = _cloud_auth_state(force_refresh=False).get("mode", "local")
    if mode == "cloud_error":
        return "cloud"
    return mode


def _auth_user_list_cloud(include_placeholder_passwords=False):
    rows = _supabase_users_request(
        method="GET",
        params={"select": "id,username,password_hash,role,is_active,email"},
        timeout=12,
    )
    users = {}
    for row in rows or []:
        username = str(row.get("username", "")).strip()
        role = str(row.get("role", "")).strip().lower()
        password_hash = str(row.get("password_hash", "")).strip()
        is_active = row.get("is_active", True)
        if not username or role not in {"admin", "manager", "recruiter"} or not password_hash or not is_active:
            continue
        if not include_placeholder_passwords and _is_placeholder_password(password_hash):
            continue
        users[username] = {
            "id": row.get("id"),
            "email": row.get("email", ""),
            "role": role,
            "password_hash": password_hash,
        }
    return users


def _auth_user_list(include_placeholder_passwords=False):
    if _auth_mode() == "cloud":
        return _auth_user_list_cloud(include_placeholder_passwords=include_placeholder_passwords)
    return _auth_user_list_local(include_placeholder_passwords=include_placeholder_passwords)


def _auth_verify_user(username, password):
    users = _auth_user_list(include_placeholder_passwords=False)
    record = users.get(username)
    if not record:
        return None
    if _auth_mode() == "cloud":
        stored_hash = str(record.get("password_hash", "")).strip()
        if stored_hash and _check_password(stored_hash, password):
            return {"username": username, "role": record.get("role", "")}
        return None
    if password == str(record.get("password", "")):
        return {"username": username, "role": record.get("role", "")}
    return None


def _auth_upsert_user(username, role, password):
    if _auth_mode() == "cloud":
        password_hash = _hash_password(password)
        email = f"{username}@njordhr.local"
        body = [{
            "username": username,
            "email": email,
            "role": role,
            "password_hash": password_hash,
            "is_active": True,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }]
        _supabase_users_request(
            method="POST",
            params={"on_conflict": "username"},
            json_body=body,
            timeout=12,
        )
        return True

    if "Users" not in config:
        config["Users"] = {}
    _config_set_literal(config, "Users", username, f"{role}|{password}")
    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)
    return True


def _auth_delete_user(username):
    if _auth_mode() == "cloud":
        _supabase_users_request(
            method="DELETE",
            params={"username": f"eq.{username}"},
            timeout=12,
        )
        return True
    if "Users" not in config or username not in config["Users"]:
        return False
    config.remove_option("Users", username)
    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)
    return True


def _bootstrap_status():
    cloud_state = _cloud_auth_state(force_refresh=False)
    state_mode = cloud_state.get("mode")
    using_cloud = state_mode in {"cloud", "cloud_error"}
    users_with_placeholders = {}
    users = {}
    try:
        users_with_placeholders = _auth_user_list(include_placeholder_passwords=True)
        users = _auth_user_list(include_placeholder_passwords=False)
    except Exception as exc:
        if using_cloud:
            return {
                "bootstrap_required": False,
                "bootstrap_completed": False,
                "reason": f"cloud_unavailable:{exc}",
                "valid_user_count": 0,
                "auth_mode": "cloud",
            }
        raise
    if users:
        return {
            "bootstrap_required": False,
            "bootstrap_completed": True,
            "reason": "configured",
            "valid_user_count": len(users),
            "auth_mode": "cloud" if using_cloud else "local",
        }
    if using_cloud:
        return {
            "bootstrap_required": False,
            "bootstrap_completed": False,
            "reason": "cloud_auth_available_no_users",
            "valid_user_count": 0,
            "auth_mode": "cloud",
        }
    if users_with_placeholders:
        return {
            "bootstrap_required": True,
            "bootstrap_completed": False,
            "reason": "placeholder_only",
            "valid_user_count": 0,
            "auth_mode": "local",
        }
    return {
        "bootstrap_required": True,
        "bootstrap_completed": False,
        "reason": "no_users",
        "valid_user_count": 0,
        "auth_mode": "local",
    }


def _is_local_request():
    remote = str(request.remote_addr or "").strip()
    if not remote:
        return True
    return remote in {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def _session_role():
    return str(session.get("role", "")).strip().lower()


def _session_username():
    return str(session.get("username", "")).strip()


def _is_authenticated():
    return _session_role() in {"admin", "manager", "recruiter"} and bool(_session_username())


def _require_role(*allowed_roles):
    if not _is_authenticated():
        return False, "Not authenticated."
    role = _session_role()
    if role not in allowed_roles:
        return False, "Insufficient permissions."
    return True, ""


def _require_admin():
    ok, reason = _require_role("admin")
    if ok:
        return True, ""

    # Legacy token fallback for automation/API compatibility.
    token = _admin_token()
    if not token:
        return False, "Admin token not configured. Set NJORDHR_ADMIN_TOKEN or [Advanced].admin_token."
    request_token = request.headers.get("X-Admin-Token", "").strip()
    if not request_token:
        body = request.json if request.is_json else {}
        request_token = str((body or {}).get("admin_token", "")).strip()
    if request_token != token:
        return False, "Unauthorized admin token."
    return True, ""


def _agent_sync_token():
    return os.getenv("NJORDHR_AGENT_SYNC_TOKEN", "").strip()


def _require_agent_ingest_auth():
    """
    Optional auth for agent ingest endpoints.
    - If NJORDHR_AGENT_SYNC_TOKEN is unset, allow requests (dev mode).
    - If set, require bearer or X-Device-Token match.
    """
    expected = _agent_sync_token()
    if not expected:
        return True, ""

    auth = request.headers.get("Authorization", "").strip()
    bearer = ""
    if auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()
    device_token = request.headers.get("X-Device-Token", "").strip()
    if bearer == expected or device_token == expected:
        return True, ""
    return False, "Unauthorized agent token."


def _mask_secret(value):
    value = str(value or "").strip()
    if not value:
        return {"configured": False, "preview": ""}
    if len(value) <= 8:
        return {"configured": True, "preview": "*" * len(value)}
    return {"configured": True, "preview": f"{value[:4]}...{value[-4:]}"}


def _config_set_literal(parser, section, key, value):
    """Write literal values safely for ConfigParser interpolation mode."""
    parser.set(section, key, str(value).replace('%', '%%'))


def _agent_sync_db_path():
    base = _resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "agent_sync_ingest.sqlite3")


def _ensure_agent_sync_db():
    db_path = _agent_sync_db_path()
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _ingest_seen_idempotency(key):
    if not key:
        return False
    db_path = _ensure_agent_sync_db()
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        row = conn.execute(
            "SELECT 1 FROM ingest_idempotency WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _ingest_store_idempotency(key, endpoint):
    if not key:
        return
    db_path = _ensure_agent_sync_db()
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ingest_idempotency(idempotency_key, endpoint, created_at) VALUES (?, ?, ?)",
            (key, endpoint, datetime.utcnow().isoformat() + "Z"),
        )
        conn.commit()
    finally:
        conn.close()


def _append_agent_sync_jsonl(kind, payload):
    logs_dir = _resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    path = os.path.join(logs_dir, f"agent_{kind}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "kind": kind,
            "received_at": datetime.utcnow().isoformat() + "Z",
            "payload": payload or {},
        }) + "\n")


def _usage_log_path():
    logs_dir = _resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "usage_audit.jsonl")


def _log_usage(action, summary="", extra=None):
    try:
        row = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "username": _session_username() or "anonymous",
            "role": _session_role() or "anonymous",
            "action": str(action or "").strip(),
            "summary": str(summary or "").strip(),
            "endpoint": request.path if has_request_context() else "",
            "method": request.method if has_request_context() else "",
            "remote_addr": request.remote_addr if has_request_context() else "",
            "extra": extra or {},
        }
        with open(_usage_log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _read_usage_logs(limit=200):
    path = _usage_log_path()
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except Exception:
                continue
    if limit > 0:
        rows = rows[-limit:]
    rows.reverse()
    return rows


def _normalize_user_role(value):
    role = str(value or "").strip().lower()
    return role if role in {"admin", "manager", "recruiter"} else ""


def _settings_payload(include_plain_secrets=False):
    payload = {
        "non_secret": {
            "default_download_folder": settings.get("Default_Download_Folder", ""),
            "verified_resumes_folder": settings.get("Additional_Local_Folder", fallback="Verified_Resumes"),
            "seajob_login_url": _advanced_value("seajob_login_url", "http://seajob.net/seajob_login.php"),
            "seajob_dashboard_url": _advanced_value("seajob_dashboard_url", "http://seajob.net/company/dashboard.php"),
            "otp_window_seconds": _advanced_value("otp_window_seconds", "120"),
            "registry_db_path": _advanced_value("registry_db_path", "registry.db"),
            "feedback_db_path": _advanced_value("feedback_db_path", "feedback.db"),
            "log_dir": _advanced_value("log_dir", "logs"),
            "pinecone_environment": config.get("Advanced", "pinecone_environment", fallback=""),
            "pinecone_index_name": config.get("Advanced", "pinecone_index_name", fallback=""),
            "embedding_model_name": config.get("Advanced", "embedding_model_name", fallback=""),
            "reasoning_model_name": config.get("Advanced", "reasoning_model_name", fallback=""),
            "min_similarity_score": config.get("Advanced", "min_similarity_score", fallback="0.25"),
            "supabase_url": os.getenv("SUPABASE_URL", ""),
            "use_supabase_db": bool(feature_flags.use_supabase_db),
            "use_dual_write": bool(getattr(feature_flags, "use_dual_write", False)),
            "use_supabase_reads": bool(getattr(feature_flags, "use_supabase_reads", False)),
            "use_local_agent": bool(feature_flags.use_local_agent),
            "use_cloud_export": bool(feature_flags.use_cloud_export),
        },
        "secrets": {
            "seajob_username": _mask_secret(_seajob_username()),
            "seajob_password": _mask_secret(_seajob_password()),
            "gemini_api_key": _mask_secret(_gemini_api_key()),
            "pinecone_api_key": _mask_secret(_pinecone_api_key()),
            "supabase_secret_key": _mask_secret(resolve_supabase_api_key()),
        }
    }
    if include_plain_secrets:
        payload["secrets_plain"] = {
            "seajob_username": _seajob_username(),
            "seajob_password": _seajob_password(),
            "gemini_api_key": _gemini_api_key(),
            "pinecone_api_key": _pinecone_api_key(),
            "supabase_secret_key": resolve_supabase_api_key(),
        }
    return payload


def _current_repo_backend():
    name = type(csv_manager).__name__.lower()
    if "dualwrite" in name:
        return "dual_write"
    if "supabase" in name:
        return "supabase"
    return "csv"


def _resolve_within_base(base_dir, *parts):
    """Resolve a path and ensure it stays within base_dir."""
    base_abs = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_abs, *parts))
    if os.path.commonpath([base_abs, candidate]) != base_abs:
        raise ValueError("Path escapes base directory")
    return candidate


def _is_safe_name(value):
    """Allow only single path components (no separators/traversal)."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or value in {'.', '..'}:
        return False
    return value == os.path.basename(value)


def _extract_candidate_id_from_filename(filename):
    """Extract numeric candidate ID from {rank}_{candidate_id}.pdf pattern."""
    # Expected format:
    # <rank>_<ship_type>_<candidate_id>_<YYYY-MM-DD>_<HH-MM-SS>.pdf
    match = re.search(r'_(\d+)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.pdf$', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    # Legacy fallback (older names without timestamp suffix).
    match = re.search(r'_(\d+)\.pdf$', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _local_agent_base_url():
    return os.getenv("NJORDHR_AGENT_BASE_URL", "http://127.0.0.1:5051").rstrip("/")


def _use_local_agent():
    return bool(getattr(feature_flags, "use_local_agent", False))


def _agent_request(method, path, *, json_body=None, params=None, stream=False, timeout=30):
    base = _local_agent_base_url()
    url = f"{base}{path}"
    return requests.request(
        method=method,
        url=url,
        json=json_body,
        params=params,
        stream=stream,
        timeout=timeout,
    )


def _agent_health_summary():
    base = _local_agent_base_url()
    try:
        resp = requests.get(f"{base}/health", timeout=3)
        if resp.status_code >= 400:
            return {"configured": True, "reachable": False, "base_url": base, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        return {"configured": True, "reachable": True, "base_url": base, "health": data}
    except Exception as exc:
        return {"configured": True, "reachable": False, "base_url": base, "error": str(exc)}


def _build_runtime_resume_url(rank_applied_for, filename):
    return _build_runtime_resume_url_from_stored(rank_applied_for, filename, "")


def _storage_bucket_name():
    return os.getenv("SUPABASE_RESUME_BUCKET", "resumes").strip() or "resumes"


def _safe_storage_segment(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _supabase_storage_upload(file_bytes, object_path, content_type="application/pdf"):
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return None, "Supabase credentials not configured"

    bucket = _storage_bucket_name()
    endpoint = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    try:
        resp = requests.post(endpoint, headers=headers, data=file_bytes, timeout=30)
        if resp.status_code >= 400:
            return None, f"Upload failed ({resp.status_code}): {resp.text[:300]}"
        return f"storage://{bucket}/{object_path}", ""
    except Exception as exc:
        return None, str(exc)


def _supabase_storage_signed_url(storage_url, expires_in=600):
    if not storage_url or not storage_url.startswith("storage://"):
        return "", "Invalid storage URL"

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return "", "Supabase credentials not configured"

    raw = storage_url[len("storage://"):]
    bucket, _, object_path = raw.partition("/")
    if not bucket or not object_path:
        return "", "Invalid storage path"
    if bucket != _storage_bucket_name():
        return "", "Invalid storage bucket"
    if ".." in object_path:
        return "", "Invalid storage object path"
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", object_path):
        return "", "Invalid storage object path"

    endpoint = f"{supabase_url.rstrip('/')}/storage/v1/object/sign/{bucket}/{object_path}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    body = {"expiresIn": int(expires_in)}
    try:
        resp = requests.post(endpoint, headers=headers, json=body, timeout=20)
        if resp.status_code >= 400:
            return "", f"Sign failed ({resp.status_code}): {resp.text[:300]}"
        payload = resp.json() if resp.text else {}
        signed = payload.get("signedURL") or payload.get("signedUrl") or ""
        if not signed:
            return "", "Signed URL missing in response"
        if signed.startswith("http://") or signed.startswith("https://"):
            return signed, ""
        return f"{supabase_url.rstrip('/')}/storage/v1{signed}", ""
    except Exception as exc:
        return "", str(exc)


def _build_runtime_resume_url_from_stored(rank_applied_for, filename, stored_resume_url):
    rank = str(rank_applied_for or "").strip()
    name = str(filename or "").strip()
    stored = str(stored_resume_url or "").strip()
    # Prefer current request host to avoid stale hardcoded port in historical rows.
    base = request.host_url.rstrip("/") if has_request_context() else app_settings.server_url.rstrip("/")
    if stored.startswith("storage://"):
        return f"{base}/open_resume?storage_url={quote(stored, safe='')}&rank_folder={quote(rank, safe='')}&filename={quote(name, safe='')}"
    if rank and name:
        return f"{base}/open_resume?rank_folder={quote(rank, safe='')}&filename={quote(name, safe='')}"
    return ""


VALID_STATUSES = {
    'New',
    'Contacted',
    'Interested',
    'Not Interested',
    'Mail Sent (handoff complete)',
}

STATUS_TRANSITIONS = {
    'New': {'Contacted'},
    'Contacted': {'Interested', 'Not Interested'},
    'Interested': {'Mail Sent (handoff complete)'},
    'Mail Sent (handoff complete)': set(),
    'Not Interested': set(),
}

ARCHIVE_STATUSES = {
    'Mail Sent (handoff complete)',
    'Not Interested',
}


def _normalize_email(value):
    return str(value or "").strip().lower()


def _validate_status_transition(current_status, next_status):
    current = str(current_status or "New").strip() or "New"
    nxt = str(next_status or "").strip()
    if not nxt:
        return False, "Invalid status value", False
    if nxt == current:
        return True, "", False
    allowed = STATUS_TRANSITIONS.get(current, set())
    if nxt in allowed:
        return True, "", False
    if _session_role() == "admin":
        return True, "", True
    return False, f"Invalid transition from '{current}' to '{nxt}'. Admin role required for overrides.", False


def _ensure_candidate_identifiers_consistent(candidate_id, email, rank_name=""):
    """
    Enforce mandatory ingest identifiers and prevent id/email collision merges.
    """
    cid = str(candidate_id or "").strip()
    normalized_email = _normalize_email(email)
    if not cid or not normalized_email:
        return False, "Missing required identifiers: candidate_id and email", False

    latest = csv_manager.get_latest_status_per_candidate(rank_name=rank_name)
    if latest.empty:
        return True, "", False

    latest = latest.copy()
    latest["Candidate_ID"] = latest["Candidate_ID"].astype(str).str.strip()
    latest["__email_norm"] = latest["Email"].astype(str).str.strip().str.lower()

    id_rows = latest[latest["Candidate_ID"] == cid]
    pair_rows = latest[
        (latest["Candidate_ID"] == cid) &
        (latest["__email_norm"] == normalized_email)
    ]

    if not id_rows.empty:
        existing_email = _normalize_email(id_rows.iloc[0].get("Email", ""))
        if existing_email and existing_email != normalized_email:
            return False, (
                f"Identifier mismatch for candidate_id={cid}: "
                f"existing email '{existing_email}' does not match '{normalized_email}'"
            ), False

    existing = not id_rows.empty or not pair_rows.empty
    return True, "", existing


def _delete_older_candidate_resume_versions(rank_folder, candidate_id, keep_filename):
    """
    Keep only the latest local downloaded resume version for a candidate within the rank folder.
    """
    deleted = []
    rank = str(rank_folder or "").strip()
    cid = str(candidate_id or "").strip()
    keep_name = str(keep_filename or "").strip()
    if not rank or not cid:
        return deleted

    source_base_dir = os.path.abspath(settings['Default_Download_Folder'])
    source_folder = _resolve_within_base(source_base_dir, rank)
    if not os.path.isdir(source_folder):
        return deleted

    for name in os.listdir(source_folder):
        if name == keep_name:
            continue
        if not name.lower().endswith(".pdf") or not _is_safe_name(name):
            continue
        if _extract_candidate_id_from_filename(name) != cid:
            continue
        try:
            stale_path = _resolve_within_base(source_folder, name)
            if os.path.isfile(stale_path):
                os.remove(stale_path)
                deleted.append(name)
        except Exception as exc:
            print(f"[VERIFY] Failed deleting stale resume version {name}: {exc}")
    return deleted

# --- NEW: Serve the Frontend ---
@app.route('/')
def serve_frontend():
    """Serve the frontend HTML file"""
    return send_from_directory('.', 'frontend.html')


@app.route('/app_asset/<path:filename>')
def serve_app_asset(filename):
    """Serve local UI assets (e.g., logo) from project root."""
    allowed_ext = {'.png', '.jpg', '.jpeg', '.svg', '.webp', '.gif', '.ico'}
    ext = os.path.splitext(filename)[1].lower()
    if not _is_safe_name(filename) or ext not in allowed_ext:
        return "Invalid asset request.", 400
    try:
        return send_from_directory('.', filename)
    except FileNotFoundError:
        return "Asset not found.", 404


@app.route('/auth/me', methods=['GET'])
def auth_me():
    if not _is_authenticated():
        return jsonify({"success": True, "authenticated": False, "user": None})
    return jsonify({
        "success": True,
        "authenticated": True,
        "user": {"username": _session_username(), "role": _session_role()},
    })


@app.route('/auth/bootstrap_status', methods=['GET'])
def auth_bootstrap_status():
    status = _bootstrap_status()
    return jsonify({
        "success": True,
        "bootstrap_required": bool(status.get("bootstrap_required")),
        "bootstrap_completed": bool(status.get("bootstrap_completed")),
        "reason": status.get("reason", ""),
        "auth_mode": status.get("auth_mode", _auth_mode()),
    })


@app.route('/auth/bootstrap', methods=['POST'])
def auth_bootstrap():
    global app_settings, config, creds, settings
    status = _bootstrap_status()
    if not status.get("bootstrap_required"):
        return jsonify({"success": False, "message": "Bootstrap already completed."}), 409
    if status.get("auth_mode") == "cloud":
        return jsonify({"success": False, "message": "Cloud auth is enabled. Bootstrap fallback is not required."}), 409
    if not _is_local_request():
        return jsonify({"success": False, "message": "Bootstrap is allowed only from local host."}), 403

    payload = request.json if request.is_json else {}
    admin_username = str((payload or {}).get("admin_username", "")).strip()
    admin_password = str((payload or {}).get("admin_password", "")).strip()
    confirm_password = str((payload or {}).get("confirm_password", "")).strip()

    if not re.match(r"^[A-Za-z0-9._-]{3,64}$", admin_username):
        _log_usage("bootstrap_failed", "Invalid bootstrap username format")
        return jsonify({"success": False, "message": "admin_username must be 3-64 chars: letters, numbers, dot, underscore, hyphen"}), 400
    if not admin_password or len(admin_password) < 8:
        _log_usage("bootstrap_failed", "Bootstrap password too short")
        return jsonify({"success": False, "message": "admin_password must be at least 8 characters"}), 400
    if admin_password != confirm_password:
        _log_usage("bootstrap_failed", "Bootstrap password confirmation mismatch")
        return jsonify({"success": False, "message": "Password confirmation does not match"}), 400
    if _is_placeholder_password(admin_password):
        _log_usage("bootstrap_failed", "Bootstrap password rejected as placeholder")
        return jsonify({"success": False, "message": "Choose a real password, not a placeholder value"}), 400

    _auth_upsert_user(admin_username, "admin", admin_password)

    if "Auth" not in config:
        config["Auth"] = {}
    _config_set_literal(config, "Auth", "admin_username", admin_username)
    _config_set_literal(config, "Auth", "admin_password", "")
    _config_set_literal(config, "Auth", "manager_password", "")
    _config_set_literal(config, "Auth", "recruiter_password", "")

    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)

    app_settings = load_app_settings()
    config = app_settings.config
    creds = app_settings.credentials
    settings = app_settings.settings
    try:
        _refresh_runtime_managers()
    except RuntimeError as exc:
        # Bootstrap auth is still valid; return actionable runtime error instead of hard 500.
        return jsonify({"success": False, "message": str(exc)}), 400

    session["username"] = admin_username
    session["role"] = "admin"
    session.permanent = False
    _log_usage("bootstrap_success", f"Bootstrap admin created: username={admin_username}")
    return jsonify({
        "success": True,
        "message": "Admin account created.",
        "user": {"username": admin_username, "role": "admin"},
    })


@app.route('/auth/login', methods=['POST'])
def auth_login():
    status = _bootstrap_status()
    if status.get("bootstrap_required"):
        return jsonify({"success": False, "message": "Bootstrap setup required. Create admin account first."}), 403
    if status.get("auth_mode") == "cloud" and status.get("reason") == "cloud_auth_available_no_users":
        return jsonify({"success": False, "message": "Cloud auth is enabled but no users are configured yet. Create users from an existing admin account or seed the first admin in Supabase."}), 403
    payload = request.json if request.is_json else {}
    username = str((payload or {}).get("username", "")).strip()
    password = str((payload or {}).get("password", "")).strip()
    try:
        record = _auth_verify_user(username, password)
    except Exception as exc:
        if status.get("auth_mode") == "cloud":
            return jsonify({"success": False, "message": f"Cloud auth unavailable: {exc}"}), 503
        raise
    if not record:
        _log_usage("login_failed", f"Login failed for username={username}")
        return jsonify({"success": False, "message": "Invalid username or password."}), 401
    session["username"] = username
    session["role"] = str(record.get("role", "")).strip().lower()
    session.permanent = False
    _log_usage("login", f"User logged in as {session['role']}")
    return jsonify({
        "success": True,
        "user": {"username": username, "role": session["role"]},
    })


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    _disconnect_seajobs_best_effort()
    _log_usage("logout", "User logged out")
    session.clear()
    return jsonify({"success": True})


@app.route('/client/heartbeat', methods=['POST'])
def client_heartbeat():
    payload = request.json if request.is_json else {}
    client_id = str((payload or {}).get("client_id", "")).strip() or request.headers.get("X-Client-Id", "").strip()
    if not client_id:
        return jsonify({"success": False, "message": "client_id is required"}), 400
    _record_ui_heartbeat(client_id)
    return jsonify({"success": True})


@app.route('/client/disconnect', methods=['POST'])
def client_disconnect():
    payload = request.json if request.is_json else {}
    client_id = str((payload or {}).get("client_id", "")).strip() or request.headers.get("X-Client-Id", "").strip()
    if not client_id:
        return jsonify({"success": False, "message": "client_id is required"}), 400
    _drop_ui_client(client_id)
    return jsonify({"success": True})

# --- API Endpoints ---
@app.route('/start_session', methods=['POST'])
def start_session():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    global scraper_session
    data = request.json
    mobile_number = data.get('mobileNumber')
    _log_usage("session_start", f"Start session requested for mobile={mobile_number}")
    if _use_local_agent():
        try:
            resp = _agent_request("POST", "/session/start", json_body={"mobile_number": mobile_number}, timeout=60)
            payload = resp.json()
            if payload.get("success"):
                _touch_seajobs_activity()
            return jsonify(payload), resp.status_code
        except Exception as exc:
            return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502

    if scraper_session: scraper_session.quit()
    _clear_seajobs_activity()
    scraper_session = Scraper(
        settings['Default_Download_Folder'],
        otp_window_seconds=_int_setting("Advanced", "otp_window_seconds", 120),
        login_url=_advanced_value("seajob_login_url", "http://seajob.net/seajob_login.php"),
        dashboard_url=_advanced_value("seajob_dashboard_url", "http://seajob.net/company/dashboard.php"),
    )
    result = scraper_session.start_session(_seajob_username(), _seajob_password(), mobile_number)
    if result.get("success"):
        _touch_seajobs_activity()
    return jsonify(result)

@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    global scraper_session
    data = request.json
    otp = data.get('otp')
    _log_usage("otp_verify", "OTP verification requested")
    timeout_info = _enforce_seajobs_idle_timeout()
    if timeout_info:
        return jsonify({
            "success": False,
            "message": timeout_info["message"],
            "session_health": {
                "active": False,
                "valid": False,
                "otp_pending": False,
                "otp_expired": True,
                "reason": "Idle timeout reached"
            }
        })
    if _use_local_agent():
        try:
            resp = _agent_request("POST", "/session/verify-otp", json_body={"otp": otp}, timeout=60)
            login_result = resp.json()
            if login_result.get("success"):
                _touch_seajobs_activity()
                try:
                    ranks_str = config.get('Ranks', 'rank_options', fallback='').strip()
                    ship_types_str = config.get('ShipTypes', 'ship_type_options', fallback='').strip()
                    login_result["ranks"] = [r.strip() for r in ranks_str.split('\n') if r.strip()]
                    login_result["ship_types"] = [s.strip() for s in ship_types_str.split('\n') if s.strip()]
                except Exception as e:
                    return jsonify({"success": False, "message": f"Error in config.ini: {e}"}), 500
            return jsonify(login_result), resp.status_code
        except Exception as exc:
            return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502

    if not scraper_session:
        return jsonify({"success": False, "message": "Session not started."})
    
    login_result = scraper_session.verify_otp(otp)
    if login_result["success"]:
        _touch_seajobs_activity()
        try:
            ranks_str = config.get('Ranks', 'rank_options', fallback='').strip()
            ship_types_str = config.get('ShipTypes', 'ship_type_options', fallback='').strip()
            login_result["ranks"] = [r.strip() for r in ranks_str.split('\n') if r.strip()]
            login_result["ship_types"] = [s.strip() for s in ship_types_str.split('\n') if s.strip()]
        except Exception as e:
            return jsonify({"success": False, "message": f"Error in config.ini: {e}"})
    return jsonify(login_result)

@app.route('/start_download', methods=['POST'])
def start_download():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    global scraper_session
    data = request.json
    timeout_info = _enforce_seajobs_idle_timeout()
    if timeout_info:
        return jsonify({"success": False, "message": timeout_info["message"]}), 400
    if _use_local_agent():
        try:
            resp = _agent_request(
                "POST",
                "/jobs/download",
                json_body={
                    "rank": data.get("rank", ""),
                    "ship_type": data.get("shipType", ""),
                    "force_redownload": bool(data.get("forceRedownload", False)),
                },
                timeout=60
            )
            payload = resp.json()
            if payload.get("success"):
                _touch_seajobs_activity()
                payload.setdefault("message", "Download job queued in local agent.")
            return jsonify(payload), resp.status_code
        except Exception as exc:
            return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502

    if not scraper_session or not scraper_session.driver:
        return jsonify({"success": False, "message": "Website session is not active or has expired."})

    if hasattr(scraper_session, 'get_session_health'):
        health = scraper_session.get_session_health()
        if not health.get('valid'):
            return jsonify({
                "success": False,
                "message": f"Website session invalid: {health.get('reason', 'Unknown')}",
                "session_health": health
            })

    session_id = str(uuid.uuid4())
    logger, log_filepath = setup_logger(
        session_id,
        logs_dir=_resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
    )
    
    result = scraper_session.download_resumes(
        data['rank'], 
        data['shipType'], 
        data['forceRedownload'], 
        logger
    )
    _log_usage("download_start", f"Download started for rank={data.get('rank', '')}, ship_type={data.get('shipType', '')}")
    
    result['log_file'] = log_filepath
    return jsonify(result)


@app.route('/download_stream', methods=['GET'])
def download_stream():
    """Stream download progress using Server-Sent Events."""
    ok, reason = _require_role("admin", "manager")
    if not ok:
        def denied():
            yield f"data: {json.dumps({'type': 'error', 'message': reason})}\n\n"
        return Response(denied(), mimetype='text/event-stream')
    global scraper_session

    rank = request.args.get('rank', '').strip()
    ship_type = request.args.get('shipType', '').strip()
    force_redownload_raw = request.args.get('forceRedownload', 'false').strip().lower()
    force_redownload = force_redownload_raw in {'1', 'true', 'yes', 'on'}
    timeout_info = _enforce_seajobs_idle_timeout()
    if timeout_info:
        def idle_timed_out():
            yield f"data: {json.dumps({'type': 'error', 'message': timeout_info['message']})}\n\n"
        return Response(idle_timed_out(), mimetype='text/event-stream')

    if _use_local_agent():
        def generate_agent():
            if not rank or not ship_type:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Rank and Ship Type are required.'})}\n\n"
                return

            try:
                create_resp = _agent_request(
                    "POST",
                    "/jobs/download",
                    json_body={
                        "rank": rank,
                        "ship_type": ship_type,
                        "force_redownload": force_redownload,
                    },
                    timeout=60
                )
                create_payload = create_resp.json()
                if not create_payload.get("success"):
                    msg = create_payload.get("message", "Failed to start local agent job")
                    yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                    return
                _touch_seajobs_activity()
                job_id = str(create_payload.get("job_id", "")).strip()
                if not job_id:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Local agent did not return job_id'})}\n\n"
                    return
                yield f"data: {json.dumps({'type': 'started', 'message': 'Download stream started.', 'job_id': job_id})}\n\n"

                with _agent_request("GET", f"/jobs/{job_id}/stream", stream=True, timeout=600) as stream_resp:
                    if stream_resp.status_code >= 400:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Agent stream failed ({stream_resp.status_code})'})}\n\n"
                        return
                    for raw in stream_resp.iter_lines(decode_unicode=True):
                        if raw is None:
                            continue
                        line = raw.strip()
                        if not line:
                            continue
                        if line.startswith(":"):
                            yield ": keepalive\n\n"
                            continue
                        if not line.startswith("data:"):
                            continue
                        payload_text = line[5:].strip()
                        try:
                            event = json.loads(payload_text)
                        except Exception:
                            continue
                        ev_type = event.get("type")
                        if ev_type == "log":
                            yield f"data: {json.dumps({'type': 'log', 'line': event.get('message', '')})}\n\n"
                        elif ev_type == "complete":
                            result = (event.get("data") or {}).get("result", {}) or {}
                            if result.get("success"):
                                yield f"data: {json.dumps({'type': 'complete', **result})}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': 'error', 'message': result.get('message', 'Download failed')})}\n\n"
                            return
                        elif ev_type == "error":
                            yield f"data: {json.dumps({'type': 'error', 'message': event.get('message', 'Download failed')})}\n\n"
                            return
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Local agent unavailable: {exc}'})}\n\n"

        return Response(generate_agent(), mimetype='text/event-stream')

    def generate():
        local_scraper = scraper_session

        if not local_scraper or not local_scraper.driver:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Website session is not active or has expired.'})}\n\n"
            return
        if not rank or not ship_type:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Rank and Ship Type are required.'})}\n\n"
            return

        if hasattr(local_scraper, 'get_session_health'):
            health = local_scraper.get_session_health()
            if not health.get('valid'):
                invalid_reason = health.get('reason', 'Unknown')
                payload = {
                    "type": "error",
                    "message": f"Website session invalid: {invalid_reason}",
                    "session_health": health
                }
                yield f"data: {json.dumps(payload)}\n\n"
                return

        session_id = str(uuid.uuid4())
        logger, log_filepath = setup_logger(
            session_id,
            logs_dir=_resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
        )
        yield f"data: {json.dumps({'type': 'started', 'log_file': log_filepath, 'message': 'Download stream started.'})}\n\n"

        stream_queue = Queue()
        result_holder = {"result": None}

        class QueueLogHandler(logging.Handler):
            def emit(self, record):
                try:
                    message = self.format(record)
                except Exception:
                    message = record.getMessage()
                stream_queue.put({"type": "log", "line": message})

        queue_handler = QueueLogHandler()
        queue_handler.setLevel(logging.INFO)
        queue_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(queue_handler)

        def worker():
            try:
                result_holder["result"] = local_scraper.download_resumes(
                    rank,
                    ship_type,
                    force_redownload,
                    logger
                )
            except Exception as exc:
                result_holder["result"] = {"success": False, "message": f"Download failed: {str(exc)}", "log": []}
            finally:
                stream_queue.put({"type": "_done"})

        download_thread = threading.Thread(target=worker, daemon=True)
        download_thread.start()

        try:
            while True:
                try:
                    event = stream_queue.get(timeout=1.0)
                except Empty:
                    if not download_thread.is_alive():
                        break
                    yield ": keepalive\n\n"
                    continue

                if event.get("type") == "_done":
                    break

                yield f"data: {json.dumps(event)}\n\n"
        finally:
            logger.removeHandler(queue_handler)
            queue_handler.close()

        result = result_holder.get("result") or {"success": False, "message": "Download ended unexpectedly.", "log": []}
        payload = {
            "type": "complete",
            "success": bool(result.get("success")),
            "message": result.get("message", "Download finished."),
            "log_file": log_filepath
        }
        yield f"data: {json.dumps(payload)}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/disconnect_session', methods=['POST'])
def disconnect_session():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    global scraper_session
    _log_usage("session_disconnect", "Disconnect session requested")
    success = _disconnect_seajobs_best_effort()
    if success:
        return jsonify({"success": True, "message": "Session disconnected successfully."})
    return jsonify({"success": False, "message": "Session disconnect failed."}), 502


@app.route('/session_health', methods=['GET'])
def session_health():
    """Return current scraper session health for OTP/session timeout handling."""
    global scraper_session
    timeout_info = _enforce_seajobs_idle_timeout()
    if timeout_info:
        return jsonify({
            "success": True,
            "connected": False,
            "idle_timeout_seconds": _seajobs_idle_timeout_seconds(),
            "idle_seconds": timeout_info["idle_seconds"],
            "health": {
                "active": False,
                "valid": False,
                "otp_pending": False,
                "otp_expired": True,
                "reason": "Idle timeout reached"
            },
            "message": timeout_info["message"]
        })

    if _use_local_agent():
        try:
            resp = _agent_request("GET", "/session/health", timeout=15)
            payload = resp.json()
            if isinstance(payload, dict):
                payload.setdefault("connected", bool((payload.get("health") or {}).get("active")))
                payload.setdefault("idle_timeout_seconds", _seajobs_idle_timeout_seconds())
                payload.setdefault("idle_seconds", _current_seajobs_idle_seconds() or 0)
            return jsonify(payload), resp.status_code
        except Exception as exc:
            return jsonify({
                "success": False,
                "connected": False,
                "health": {
                    "active": False,
                    "valid": False,
                    "otp_pending": False,
                    "otp_expired": False,
                    "reason": f"Local agent unavailable: {exc}"
                }
            }), 502

    if not scraper_session:
        return jsonify({
            "success": True,
            "connected": False,
            "idle_timeout_seconds": _seajobs_idle_timeout_seconds(),
            "idle_seconds": _current_seajobs_idle_seconds() or 0,
            "health": {
                "active": False,
                "valid": False,
                "otp_pending": False,
                "otp_expired": False,
                "reason": "No active scraper session"
            }
        })

    if hasattr(scraper_session, 'get_session_health'):
        health = scraper_session.get_session_health()
        return jsonify({
            "success": True,
            "connected": bool(scraper_session and scraper_session.driver),
            "idle_timeout_seconds": _seajobs_idle_timeout_seconds(),
            "idle_seconds": _current_seajobs_idle_seconds() or 0,
            "health": health
        })

    return jsonify({
        "success": True,
        "connected": bool(scraper_session and scraper_session.driver),
        "idle_timeout_seconds": _seajobs_idle_timeout_seconds(),
        "idle_seconds": _current_seajobs_idle_seconds() or 0,
        "health": {
            "active": bool(scraper_session and scraper_session.driver),
            "valid": bool(scraper_session and scraper_session.driver),
            "otp_pending": False,
            "otp_expired": False,
            "reason": "Legacy scraper without health checks"
        }
    })


@app.route('/config/runtime', methods=['GET'])
def runtime_config():
    """Expose safe runtime mode information for diagnostics/UI."""
    if not _is_authenticated():
        return jsonify({
            "success": True,
            "feature_flags": {
                "use_local_agent": bool(feature_flags.use_local_agent),
            },
            "ui_auto_shutdown": {
                "enabled": _ui_idle_autoshutdown_enabled(),
                "idle_seconds": _ui_idle_shutdown_seconds(),
                "active_clients": _active_ui_client_count(),
            },
        })

    key_source = "none"
    if os.getenv("SUPABASE_SECRET_KEY", "").strip():
        key_source = "secret"
    elif os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        key_source = "legacy_service_role"
    supabase_key = resolve_supabase_api_key()
    key_hint = f"{supabase_key[:12]}..." if supabase_key else ""

    return jsonify({
        "success": True,
        "feature_flags": {
            "use_supabase_db": bool(feature_flags.use_supabase_db),
            "use_dual_write": bool(getattr(feature_flags, "use_dual_write", False)),
            "use_supabase_reads": bool(getattr(feature_flags, "use_supabase_reads", False)),
            "use_local_agent": bool(feature_flags.use_local_agent),
            "use_cloud_export": bool(feature_flags.use_cloud_export),
        },
        "persistence_backend": _current_repo_backend(),
        "server_url": app_settings.server_url,
        "ui_auto_shutdown": {
            "enabled": _ui_idle_autoshutdown_enabled(),
            "idle_seconds": _ui_idle_shutdown_seconds(),
            "active_clients": _active_ui_client_count(),
        },
        "verified_resumes_dir": VERIFIED_RESUMES_DIR,
        "supabase_auth": {
            "key_source": key_source,
            "key_hint": key_hint,
            "url_configured": bool(os.getenv("SUPABASE_URL", "").strip()),
        },
        "agent_ingest_auth": {
            "required": bool(_agent_sync_token()),
        },
        "admin_settings_enabled": bool(_admin_token()),
        "auth_backend": _cloud_auth_state(force_refresh=False),
        "local_agent": _agent_health_summary() if _use_local_agent() else {
            "configured": False,
            "reachable": False,
            "base_url": _local_agent_base_url(),
        },
    })


@app.route('/setup/manifest', methods=['GET'])
def setup_manifest():
    """Installer/setup metadata for the guided Setup tab."""
    base_url = app_settings.server_url.rstrip('/')
    return jsonify({
        "success": True,
        "recommended_mode": "cloud_local_agent",
        "installers": {
            "macos": {
                "full": os.getenv("NJORDHR_MACOS_FULL_INSTALLER_URL", ""),
                "agent_only": os.getenv("NJORDHR_MACOS_AGENT_INSTALLER_URL", ""),
                "fallback_docs": f"{base_url}",
            },
            "windows": {
                "full": os.getenv("NJORDHR_WINDOWS_FULL_INSTALLER_URL", ""),
                "agent_only": os.getenv("NJORDHR_WINDOWS_AGENT_INSTALLER_URL", ""),
                "fallback_docs": f"{base_url}",
            }
        },
        "commands": {
            "macos_full": "./scripts/packaging/macos/build_pkg.sh",
            "macos_install_app": "./scripts/packaging/macos/install_app.sh",
            "windows_full": "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\packaging\\windows\\build_inno_installer.ps1",
            "windows_install_shortcuts": "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\windows\\install_shortcuts.ps1",
            "macos_local_start": "./scripts/start_njordhr.sh",
            "windows_local_start": "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\windows\\start_njordhr.ps1",
        },
        "checks": {
            "backend_runtime_url": f"{base_url}/config/runtime",
            "agent_health_url": os.getenv("NJORDHR_AGENT_BASE_URL", "http://127.0.0.1:5051").rstrip('/') + "/health",
        }
    })


@app.route('/updates/manifest', methods=['GET'])
def updates_manifest():
    """
    Update manifest service (M5-T4).
    Query params:
      - channel (currently accepted, default: stable)
      - platform: macos|windows|all (default: all)
      - version: specific version folder (optional)
    """
    channel = str(request.args.get("channel", "stable")).strip().lower() or "stable"
    platform = str(request.args.get("platform", "all")).strip().lower() or "all"
    requested_version = str(request.args.get("version", "")).strip()

    if platform not in {"all", "macos", "windows"}:
        return jsonify({"success": False, "message": "Invalid platform. Use all|macos|windows."}), 400
    if channel not in {"stable"}:
        return jsonify({"success": False, "message": "Unsupported channel."}), 400

    root = _release_root_dir()
    versions = _iter_release_versions(root)
    if not versions:
        return jsonify({"success": False, "message": "No release manifests available.", "channel": channel}), 404

    version = requested_version or versions[0]
    if version not in versions:
        return jsonify({"success": False, "message": f"Version not found: {version}"}), 404

    manifest_path = root / version / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"success": False, "message": f"Invalid manifest.json for {version}: {exc}"}), 500

    base_url = _release_public_base_url()
    raw_artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    normalized_artifacts = []
    for item in raw_artifacts:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or not _is_safe_name(name):
            continue
        item_platform = _platform_from_filename(name)
        if platform != "all" and item_platform not in {platform, "all"}:
            continue
        sig_name = f"{name}.sig"
        sig_path = root / version / sig_name
        normalized_artifacts.append({
            "name": name,
            "platform": item_platform,
            "size_bytes": int(item.get("size_bytes", 0) or 0),
            "sha256": str(item.get("sha256", "")).strip(),
            "url": f"{base_url}/{quote(version, safe='')}/{quote(name, safe='')}",
            "signature_url": f"{base_url}/{quote(version, safe='')}/{quote(sig_name, safe='')}" if sig_path.exists() else "",
            "signature": str(item.get("signature", "")).strip(),
        })

    return jsonify({
        "success": True,
        "channel": channel,
        "version": version,
        "created_at_utc": manifest.get("created_at_utc", ""),
        "artifact_count": len(normalized_artifacts),
        "artifacts": normalized_artifacts,
    })


@app.route('/releases/<version>/<path:filename>', methods=['GET'])
def download_release_artifact(version, filename):
    """Serve release artifacts referenced by /updates/manifest."""
    version = str(version or "").strip()
    filename = str(filename or "").strip()
    if not _is_safe_name(version) or not _is_safe_name(filename):
        return jsonify({"success": False, "message": "Invalid artifact path."}), 400

    root = _release_root_dir()
    target_dir = root / version
    if not target_dir.exists() or not target_dir.is_dir():
        return jsonify({"success": False, "message": "Release version not found."}), 404

    artifact_path = target_dir / filename
    if not artifact_path.exists() or not artifact_path.is_file():
        return jsonify({"success": False, "message": "Artifact not found."}), 404

    return send_from_directory(str(target_dir), filename, as_attachment=True)


@app.route('/api/agent/job-state', methods=['POST'])
def ingest_agent_job_state():
    ok, reason = _require_agent_ingest_auth()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    idem_key = request.headers.get("X-Idempotency-Key", "").strip()
    if idem_key and _ingest_seen_idempotency(idem_key):
        return jsonify({"success": True, "duplicate": True})

    payload = request.json if request.is_json else {}
    _append_agent_sync_jsonl("job_state", payload or {})
    _ingest_store_idempotency(idem_key, "/api/agent/job-state")
    return jsonify({"success": True})


@app.route('/api/agent/job-log', methods=['POST'])
def ingest_agent_job_log():
    ok, reason = _require_agent_ingest_auth()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    idem_key = request.headers.get("X-Idempotency-Key", "").strip()
    if idem_key and _ingest_seen_idempotency(idem_key):
        return jsonify({"success": True, "duplicate": True})

    payload = request.json if request.is_json else {}
    _append_agent_sync_jsonl("job_log", payload or {})
    _ingest_store_idempotency(idem_key, "/api/agent/job-log")
    return jsonify({"success": True})


@app.route('/api/events/candidate', methods=['POST'])
def ingest_agent_candidate_event():
    ok, reason = _require_agent_ingest_auth()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    idem_key = request.headers.get("X-Idempotency-Key", "").strip()
    if idem_key and _ingest_seen_idempotency(idem_key):
        return jsonify({"success": True, "duplicate": True})

    payload = request.json if request.is_json else {}
    payload = payload or {}

    event_type = str(payload.get("event_type", "")).strip() or "unknown"
    filename = str(payload.get("filename", "")).strip()
    rank = str(payload.get("rank_applied_for", "")).strip()
    extracted_data = {
        "name": str(payload.get("name", "")).strip(),
        "present_rank": str(payload.get("present_rank", "")).strip(),
        "email": _normalize_email(payload.get("email", "")),
        "country": str(payload.get("country", "")).strip(),
        "mobile_no": str(payload.get("mobile_no", "")).strip(),
    }

    candidate_id = str(payload.get("candidate_external_id", "")).strip()
    if not candidate_id and filename:
        m = re.search(r"_(\d+)(?:_|\.)", filename)
        if m:
            candidate_id = m.group(1)

    resume_storage_path = str(payload.get("resume_storage_path", "")).strip()
    explicit_resume_url = resume_storage_path if resume_storage_path.startswith("storage://") else ""

    # Keep operational/download-only agent events out of dashboard data.
    dashboard_event_types = {"initial_verification", "status_change", "note_added", "resume_updated"}
    if event_type in {"initial_verification", "resume_updated"} and (
        not candidate_id or not extracted_data["email"]
    ):
        return jsonify({
            "success": False,
            "message": "candidate_external_id and email are required for resume ingest events"
        }), 400

    if event_type in dashboard_event_types and candidate_id and filename:
        csv_manager.log_event(
            candidate_id=candidate_id,
            filename=filename,
            event_type=event_type,
            status=str(payload.get("status", "New")),
            admin_override=bool(payload.get("admin_override", False)),
            notes=str(payload.get("notes", "")),
            rank_applied_for=rank,
            search_ship_type=str(payload.get("search_ship_type", "")),
            ai_prompt=str(payload.get("ai_search_prompt", "")),
            ai_reason=str(payload.get("ai_match_reason", "")),
            extracted_data=extracted_data,
            resume_url=explicit_resume_url,
        )
    else:
        _append_agent_sync_jsonl("candidate_event", payload)

    _ingest_store_idempotency(idem_key, "/api/events/candidate")
    return jsonify({"success": True})


@app.route('/api/agent/resume-upload', methods=['POST'])
def ingest_agent_resume_upload():
    ok, reason = _require_agent_ingest_auth()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    idem_key = request.headers.get("X-Idempotency-Key", "").strip()
    if idem_key and _ingest_seen_idempotency(idem_key):
        return jsonify({"success": True, "duplicate": True, "upload_status": "duplicate"})

    # Upload PDF into private Supabase Storage bucket and return canonical storage URL.
    metadata_raw = request.form.get("metadata", "{}")
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except Exception:
        metadata = {}
    file_obj = request.files.get("file")
    filename = file_obj.filename if file_obj else ""
    candidate_id = _safe_storage_segment(metadata.get("candidate_external_id") or "unknown")
    rank = _safe_storage_segment(metadata.get("rank_applied_for") or "unknown")
    safe_name = _safe_storage_segment(filename or f"{candidate_id}.pdf")
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"
    object_path = f"{rank}/{candidate_id}/{safe_name}"

    if not file_obj:
        return jsonify({"success": False, "message": "Missing file"}), 400

    content = file_obj.read()
    if not content:
        return jsonify({"success": False, "message": "Empty file"}), 400

    storage_url, error = _supabase_storage_upload(content, object_path)
    _append_agent_sync_jsonl("resume_upload", {
        "filename": filename,
        "metadata": metadata,
        "storage_url": storage_url,
        "error": error,
    })

    _ingest_store_idempotency(idem_key, "/api/agent/resume-upload")
    if not storage_url:
        return jsonify({
            "success": False,
            "upload_status": "failed",
            "message": error or "Upload failed",
            "resume_storage_path": "",
        }), 500
    return jsonify({
        "success": True,
        "upload_status": "uploaded",
        "resume_storage_path": storage_url,
    })


@app.route('/admin/settings', methods=['GET'])
def get_admin_settings():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401
    include_plain = str(request.args.get("include_secrets", "false")).strip().lower() in {"1", "true", "yes", "on"}
    return jsonify({"success": True, "settings": _settings_payload(include_plain_secrets=include_plain)})


@app.route('/admin/settings/test_supabase', methods=['POST'])
def test_admin_supabase():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    data = request.json or {}
    supabase_url = str(data.get("supabase_url", "") or os.getenv("SUPABASE_URL", "")).strip()
    supabase_secret_key = str(data.get("supabase_secret_key", "") or resolve_supabase_api_key()).strip()
    if not supabase_url or not supabase_secret_key:
        return jsonify({"success": False, "message": "supabase_url and supabase_secret_key are required"}), 400

    try:
        import requests
        resp = requests.get(
            f"{supabase_url.rstrip('/')}/rest/v1/candidate_events",
            params={"select": "id", "limit": 1},
            headers={
                "apikey": supabase_secret_key,
                "Authorization": f"Bearer {supabase_secret_key}",
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            return jsonify({
                "success": False,
                "message": f"Supabase test failed ({resp.status_code})",
                "details": resp.text[:500],
            }), 400
        return jsonify({"success": True, "message": "Supabase connection successful"})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Supabase test error: {exc}"}), 500


@app.route('/admin/settings', methods=['POST'])
def save_admin_settings():
    global app_settings, config, creds, settings
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    data = request.json or {}
    payload = data.get("settings", {}) if isinstance(data.get("settings", {}), dict) else data

    if "Credentials" not in config:
        config["Credentials"] = {}
    if "Settings" not in config:
        config["Settings"] = {}
    if "Advanced" not in config:
        config["Advanced"] = {}

    def _set_if_present(section, key, payload_key):
        if payload_key in payload:
            value = str(payload.get(payload_key, "")).strip()
            if value:
                _config_set_literal(config, section, key, value)

    # Sensitive credentials are kept in runtime env / cloud runtime config, not persisted in local config.ini.
    sensitive_pairs = {}
    if "seajob_username" in payload:
        val = str(payload.get("seajob_username", "")).strip()
        if val:
            os.environ["SEAJOB_USERNAME"] = val
            sensitive_pairs["seajob_username"] = val
    if "seajob_password" in payload:
        val = str(payload.get("seajob_password", "")).strip()
        if val:
            os.environ["SEAJOB_PASSWORD"] = val
            sensitive_pairs["seajob_password"] = val
    if "gemini_api_key" in payload:
        val = str(payload.get("gemini_api_key", "")).strip()
        if val:
            os.environ["GEMINI_API_KEY"] = val
            sensitive_pairs["gemini_api_key"] = val
    if "pinecone_api_key" in payload:
        val = str(payload.get("pinecone_api_key", "")).strip()
        if val:
            os.environ["PINECONE_API_KEY"] = val
            sensitive_pairs["pinecone_api_key"] = val
    _set_if_present("Settings", "Default_Download_Folder", "default_download_folder")
    _set_if_present("Settings", "Additional_Local_Folder", "verified_resumes_folder")
    _set_if_present("Advanced", "seajob_login_url", "seajob_login_url")
    _set_if_present("Advanced", "seajob_dashboard_url", "seajob_dashboard_url")
    _set_if_present("Advanced", "registry_db_path", "registry_db_path")
    _set_if_present("Advanced", "feedback_db_path", "feedback_db_path")
    _set_if_present("Advanced", "log_dir", "log_dir")
    _set_if_present("Advanced", "pinecone_environment", "pinecone_environment")
    _set_if_present("Advanced", "pinecone_index_name", "pinecone_index_name")
    _set_if_present("Advanced", "embedding_model_name", "embedding_model_name")
    _set_if_present("Advanced", "reasoning_model_name", "reasoning_model_name")

    if "min_similarity_score" in payload:
        try:
            score = float(payload.get("min_similarity_score"))
        except Exception:
            return jsonify({"success": False, "message": "min_similarity_score must be a valid number"}), 400
        _config_set_literal(config, "Advanced", "min_similarity_score", str(score))

    if "otp_window_seconds" in payload:
        try:
            otp_seconds = int(str(payload.get("otp_window_seconds", "")).strip())
            if otp_seconds < 30 or otp_seconds > 900:
                raise ValueError("out of range")
        except Exception:
            return jsonify({"success": False, "message": "otp_window_seconds must be an integer between 30 and 900"}), 400
        _config_set_literal(config, "Advanced", "otp_window_seconds", str(otp_seconds))

    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)

    if "supabase_url" in payload:
        os.environ["SUPABASE_URL"] = str(payload.get("supabase_url", "")).strip()
    if "supabase_secret_key" in payload:
        sup_key = str(payload.get("supabase_secret_key", "")).strip()
        if sup_key:
            os.environ["SUPABASE_SECRET_KEY"] = sup_key
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            sensitive_pairs["supabase_secret_key"] = sup_key

    # Persist sensitive runtime config centrally in Supabase when available.
    if sensitive_pairs and bool(getattr(feature_flags, "use_supabase_db", False)):
        try:
            _supabase_runtime_config_set(sensitive_pairs)
        except Exception as exc:
            return jsonify({"success": False, "message": f"Failed to save cloud secrets: {exc}"}), 400

    env_flags = [
        "use_supabase_db",
        "use_dual_write",
        "use_supabase_reads",
        "use_local_agent",
        "use_cloud_export",
    ]
    prev_env = {name.upper(): os.environ.get(name.upper()) for name in env_flags}
    for flag_name in env_flags:
        if flag_name in payload:
            env_name = flag_name.upper()
            os.environ[env_name] = "true" if bool(payload.get(flag_name)) else "false"

    try:
        app_settings = load_app_settings()
        config = app_settings.config
        creds = app_settings.credentials
        settings = app_settings.settings
        _refresh_runtime_managers()
    except RuntimeError as exc:
        for env_name, prev_value in prev_env.items():
            if prev_value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = prev_value
        app_settings = load_app_settings()
        config = app_settings.config
        creds = app_settings.credentials
        settings = app_settings.settings
        return jsonify({"success": False, "message": str(exc)}), 400

    return jsonify({
        "success": True,
        "message": "Admin settings saved and applied.",
        "runtime": {
            "persistence_backend": _current_repo_backend(),
            "feature_flags": {
                "use_supabase_db": bool(feature_flags.use_supabase_db),
                "use_dual_write": bool(getattr(feature_flags, "use_dual_write", False)),
                "use_supabase_reads": bool(getattr(feature_flags, "use_supabase_reads", False)),
                "use_local_agent": bool(feature_flags.use_local_agent),
                "use_cloud_export": bool(feature_flags.use_cloud_export),
            }
        }
    })


@app.route('/admin/fs/list', methods=['GET'])
def admin_list_directories():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    requested_path = request.args.get("path", "").strip()
    base_path = os.path.expanduser("~")
    target_path = requested_path or base_path
    target_path = os.path.abspath(os.path.expanduser(target_path))

    if not os.path.isdir(target_path):
        return jsonify({"success": False, "message": "Directory not found"}), 400

    entries = []
    try:
        with os.scandir(target_path) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name
                if name.startswith('.'):
                    continue
                entries.append({
                    "name": name,
                    "path": os.path.abspath(entry.path),
                })
    except Exception as exc:
        return jsonify({"success": False, "message": f"Failed to list directories: {exc}"}), 500

    entries.sort(key=lambda item: item["name"].lower())
    parent_path = os.path.dirname(target_path)
    if parent_path == target_path:
        parent_path = ""

    return jsonify({
        "success": True,
        "current_path": target_path,
        "parent_path": parent_path,
        "entries": entries,
    })


@app.route('/admin/settings/change_password', methods=['POST'])
def change_admin_password():
    global app_settings, config
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    data = request.json or {}
    new_password = str(data.get("new_admin_password", "")).strip()
    confirm_password = str(data.get("confirm_admin_password", "")).strip()

    if not new_password:
        return jsonify({"success": False, "message": "New settings password is required"}), 400
    if len(new_password) < 8:
        return jsonify({"success": False, "message": "Settings password must be at least 8 characters"}), 400
    if new_password != confirm_password:
        return jsonify({"success": False, "message": "Password confirmation does not match"}), 400

    if "Advanced" not in config:
        config["Advanced"] = {}
    _config_set_literal(config, "Advanced", "admin_token", new_password)

    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)

    os.environ["NJORDHR_ADMIN_TOKEN"] = new_password
    app_settings = load_app_settings()
    config = app_settings.config

    return jsonify({"success": True, "message": "Settings password updated successfully."})


@app.route('/admin/users', methods=['GET'])
def admin_list_users():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401
    try:
        users = _auth_user_list()
    except Exception as exc:
        return jsonify({"success": False, "message": f"Failed to load users: {exc}"}), 500
    data = []
    for username, record in sorted(users.items(), key=lambda item: item[0].lower()):
        if _auth_mode() == "cloud":
            masked = "********"
        else:
            masked = "*" * max(8, len(str(record.get("password", ""))))
        data.append({
            "username": username,
            "role": str(record.get("role", "")).strip().lower(),
            "password_masked": masked,
        })
    return jsonify({"success": True, "users": data, "auth_mode": _auth_mode()})


@app.route('/admin/users', methods=['POST'])
def admin_upsert_user():
    global app_settings, config
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401

    payload = request.json if request.is_json else {}
    username = str((payload or {}).get("username", "")).strip()
    password = str((payload or {}).get("password", "")).strip()
    role = _normalize_user_role((payload or {}).get("role", ""))
    if not username:
        return jsonify({"success": False, "message": "username is required"}), 400
    if not re.match(r"^[A-Za-z0-9._-]{3,64}$", username):
        return jsonify({"success": False, "message": "username must be 3-64 chars: letters, numbers, dot, underscore, hyphen"}), 400
    if not password or len(password) < 8:
        return jsonify({"success": False, "message": "password must be at least 8 characters"}), 400
    if not role:
        return jsonify({"success": False, "message": "role must be one of: admin, manager, recruiter"}), 400

    try:
        _auth_upsert_user(username, role, password)
    except Exception as exc:
        return jsonify({"success": False, "message": f"Failed to save user: {exc}"}), 500
    if _auth_mode() == "local":
        app_settings = load_app_settings()
        config = app_settings.config
    _log_usage("user_upsert", f"User saved: username={username}, role={role}")
    return jsonify({"success": True, "message": "User saved successfully."})


@app.route('/admin/users/<username>', methods=['DELETE'])
def admin_delete_user(username):
    global app_settings, config
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401
    username = str(username or "").strip()
    if not username:
        return jsonify({"success": False, "message": "username is required"}), 400
    try:
        users = _auth_user_list()
    except Exception as exc:
        return jsonify({"success": False, "message": f"Failed to load users: {exc}"}), 500
    if username not in users:
        return jsonify({"success": False, "message": "User not found"}), 404
    try:
        _auth_delete_user(username)
    except Exception as exc:
        return jsonify({"success": False, "message": f"Failed to delete user: {exc}"}), 500
    if _auth_mode() == "local":
        app_settings = load_app_settings()
        config = app_settings.config
    _log_usage("user_delete", f"User deleted: username={username}")
    return jsonify({"success": True, "message": "User deleted successfully."})


@app.route('/admin/usage_logs', methods=['GET'])
def admin_usage_logs():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401
    try:
        limit = int(str(request.args.get("limit", "200")).strip())
    except Exception:
        limit = 200
    limit = max(1, min(limit, 1000))
    rows = _read_usage_logs(limit=limit)
    return jsonify({"success": True, "rows": rows, "count": len(rows)})

@app.route('/get_rank_folders', methods=['GET'])
def get_rank_folders():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    base_folder = settings['Default_Download_Folder']
    if not os.path.isdir(base_folder):
        return jsonify({"success": False, "folders": [], "message": "Download folder not found."})
    
    try:
        subfolders = [d for d in os.listdir(base_folder) if os.path.isdir(os.path.join(base_folder, d))]
        return jsonify({"success": True, "folders": sorted(subfolders)})
    except Exception as e:
        return jsonify({"success": False, "folders": [], "message": str(e)})

@app.route('/analyze_stream', methods=['GET'])
def analyze_stream():
    """Stream analysis progress using Server-Sent Events"""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        def denied():
            yield f"data: {json.dumps({'type': 'error', 'message': reason})}\n\n"
        return Response(denied(), mimetype='text/event-stream')
    prompt = request.args.get('prompt')
    rank_folder = request.args.get('rank_folder')
    _log_usage("analyze_stream", f"AI search started for rank_folder={rank_folder}")

    def generate():
        try:
            if not prompt or not rank_folder:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Missing required data'})}\n\n"
                return
            
            target_folder = os.path.join(settings['Default_Download_Folder'], rank_folder)
            if not os.path.isdir(target_folder):
                yield f"data: {json.dumps({'type': 'error', 'message': 'Rank folder not found: ' + rank_folder})}\n\n"
                return
            
            # Create analyzer and run streaming analysis
            analyzer = Analyzer(_gemini_api_key())
            
            # Stream progress events
            for progress_event in analyzer.run_analysis_stream(rank_folder, prompt):
                yield f"data: {json.dumps(progress_event)}\n\n"
            
        except Exception as e:
            print(f"[BACKEND ERROR] {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/analyze', methods=['POST'])
def analyze():
    """Non-streaming endpoint for backward compatibility"""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json
        prompt = data.get('prompt')
        rank_folder = data.get('rank_folder')

        if not prompt or not rank_folder:
            return jsonify({"success": False, "message": "AI prompt and a rank folder selection are required."}), 400
        
        target_folder = os.path.join(settings['Default_Download_Folder'], rank_folder)
        if not os.path.isdir(target_folder):
            return jsonify({"success": False, "message": f"Rank folder not found: {rank_folder}"}), 400
        
        print(f"[BACKEND] Starting analysis for rank folder: {rank_folder}")
        print(f"[BACKEND] Prompt: {prompt}")
        
        analyzer = Analyzer(_gemini_api_key())
        result = analyzer.run_analysis(rank_folder, prompt)
        _log_usage("analyze", f"AI search completed for rank_folder={rank_folder}", {
            "success": bool(result.get("success")),
            "verified_matches": len(result.get("verified_matches", [])),
            "uncertain_matches": len(result.get("uncertain_matches", [])),
        })
        
        print(f"[BACKEND] Analysis complete. Success: {result.get('success')}")
        return jsonify(result)
    
    except Exception as e:
        print(f"[BACKEND ERROR] {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    """Store user feedback for learning"""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json
        
        analyzer = Analyzer(_gemini_api_key())
        analyzer.store_feedback(
            filename=data.get('filename'),
            query=data.get('query'),
            llm_decision=data.get('llm_decision'),
            llm_reason=data.get('llm_reason'),
            llm_confidence=data.get('llm_confidence'),
            user_decision=data.get('user_decision'),
            user_notes=data.get('user_notes', '')
        )
        
        return jsonify({"success": True, "message": "Feedback recorded successfully"})
    
    except Exception as e:
        print(f"[ERROR] Feedback submission failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def _serve_local_resume(rank_folder, filename):
    try:
        base_dir = os.path.abspath(settings['Default_Download_Folder'])
        full_path = _resolve_within_base(base_dir, rank_folder, filename)

        if not os.path.isfile(full_path):
            # Fallback for historical rows where rank folder changed over time.
            requested_name = os.path.basename(filename)
            fallback_path = None
            for root, _, files in os.walk(base_dir):
                if requested_name in files:
                    fallback_path = os.path.join(root, requested_name)
                    break
            if not fallback_path:
                print(f"[ERROR] File not found: {full_path}")
                return "File not found", 404
            full_path = fallback_path

        directory = os.path.dirname(full_path)
        safe_filename = os.path.basename(full_path)

        if not safe_filename:
            return "Access denied.", 403

        print(f"[PDF] Serving file: {full_path}")
        return send_from_directory(directory, safe_filename, as_attachment=False)

    except ValueError:
        print("[SECURITY] Access denied. Invalid path request.")
        return "Access denied.", 403
    except FileNotFoundError:
        return "File not found", 404
    except Exception as e:
        print(f"[ERROR] Exception in get_resume: {e}")
        import traceback
        traceback.print_exc()
        return str(e), 500


def _cloud_data_required():
    """
    When Supabase DB mode is enabled, require cloud-backed resume access and avoid local file fallback.
    """
    return bool(getattr(feature_flags, "use_supabase_db", False))


@app.route('/open_resume', methods=['GET'])
def open_resume():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return reason, 403
    storage_url = str(request.args.get('storage_url', '')).strip()
    rank_folder = str(request.args.get('rank_folder', '')).strip()
    filename = str(request.args.get('filename', '')).strip()

    if storage_url.startswith("storage://"):
        signed, err = _supabase_storage_signed_url(storage_url, expires_in=900)
        if signed:
            return redirect(signed, code=302)
        if _cloud_data_required():
            return jsonify({"success": False, "message": "Resume URL unavailable from cloud storage"}), 502
        print(f"[RESUME] Signed URL fallback to local file: {err}")

    if _cloud_data_required():
        return jsonify({"success": False, "message": "Cloud storage URL is required for this resume."}), 400

    if not rank_folder or not filename:
        return "Missing resume path", 400
    return _serve_local_resume(rank_folder, filename)


@app.route('/get_resume/<path:rank_folder>/<path:filename>')
def get_resume(rank_folder, filename):
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return reason, 403
    if _cloud_data_required():
        return jsonify({"success": False, "message": "Direct local resume path is disabled in cloud mode."}), 410
    return _serve_local_resume(rank_folder, filename)

@app.route('/verify_resumes', methods=['POST'])
def verify_resumes():
    """Verify resumes by logging initial verification events to master CSV."""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    data = request.json
    rank_folder = data.get('rank_folder')
    filenames = data.get('filenames')
    
    # Get AI match data for each file (if available from frontend)
    match_data = data.get('match_data', {})  # {filename: {reason: "...", confidence: 0.9}}
    ai_prompt = data.get('ai_prompt', '')
    search_ship_type = data.get('search_ship_type', '')

    if not rank_folder or not filenames:
        return jsonify({"success": False, "message": "Missing required data."}), 400
    if not _is_safe_name(rank_folder):
        return jsonify({"success": False, "message": "Invalid rank folder."}), 400
    if not isinstance(filenames, list):
        return jsonify({"success": False, "message": "Invalid filenames payload."}), 400
    for filename in filenames:
        if not _is_safe_name(filename) or not filename.lower().endswith('.pdf'):
            return jsonify({"success": False, "message": f"Invalid filename: {filename}"}), 400

    source_base_dir = os.path.abspath(settings['Default_Download_Folder'])
    source_folder = _resolve_within_base(source_base_dir, rank_folder)

    try:
        processed_files = 0
        csv_exports = 0
        stale_versions_deleted = 0
        extraction_errors = []
        
        for filename in filenames:
            source_path = _resolve_within_base(source_folder, filename)

            if os.path.isfile(source_path):
                candidate_id = _extract_candidate_id_from_filename(filename)
                if not candidate_id:
                    extraction_errors.append(f"{filename}: Could not extract candidate ID from filename")
                    continue

                ai_match_reason = match_data.get(filename, {}).get('reason', 'Manually verified')
                
                print(f"[VERIFY] Extracting data from {filename}...")
                resume_data = resume_extractor.extract_resume_data(
                    source_path,
                    candidate_id=candidate_id,
                    match_reason=ai_match_reason
                )
                extracted_email = _normalize_email(resume_data.get("email", ""))
                if not extracted_email:
                    extraction_errors.append(f"{filename}: Missing required email in extracted resume data")
                    continue

                identifiers_ok, identifier_error, already_exists = _ensure_candidate_identifiers_consistent(
                    candidate_id=candidate_id,
                    email=extracted_email,
                    rank_name=rank_folder,
                )
                if not identifiers_ok:
                    extraction_errors.append(f"{filename}: {identifier_error}")
                    continue

                event_type = 'resume_updated' if already_exists else 'initial_verification'
                storage_url = ""
                try:
                    rank_seg = _safe_storage_segment(rank_folder)
                    candidate_seg = _safe_storage_segment(candidate_id)
                    file_seg = _safe_storage_segment(filename)
                    object_path = f"{rank_seg}/{candidate_seg}/{file_seg}"
                    with open(source_path, "rb") as fh:
                        file_bytes = fh.read()
                    storage_url, upload_err = _supabase_storage_upload(file_bytes, object_path)
                    if not storage_url and upload_err:
                        extraction_errors.append(f"{filename}: Cloud upload failed ({upload_err})")
                except Exception as upload_exc:
                    extraction_errors.append(f"{filename}: Cloud upload exception ({upload_exc})")

                csv_ok = csv_manager.log_event(
                    candidate_id=candidate_id,
                    filename=filename,
                    event_type=event_type,
                    status='New',
                    notes='',
                    rank_applied_for=rank_folder,
                    search_ship_type=search_ship_type,
                    ai_prompt=ai_prompt,
                    ai_reason=ai_match_reason,
                    extracted_data=resume_data,
                    resume_url=storage_url,
                )

                if csv_ok:
                    csv_exports += 1
                    print(f"[VERIFY] Event logged for {filename}")
                    if event_type == 'resume_updated':
                        deleted_files = _delete_older_candidate_resume_versions(
                            rank_folder=rank_folder,
                            candidate_id=candidate_id,
                            keep_filename=filename,
                        )
                        stale_versions_deleted += len(deleted_files)
                else:
                    extraction_errors.append(f"{filename}: CSV event logging failed")

                if resume_data.get('extraction_status') != 'Success':
                    extraction_errors.append(f"{filename}: Data extraction failed")

                processed_files += 1
            else:
                extraction_errors.append(f"{filename}: Source file not found")
        
        # Prepare response message
        message = f"Successfully processed {processed_files} file(s). "
        message += f"Logged {csv_exports} event(s) to master CSV."
        if stale_versions_deleted:
            message += f" Deleted {stale_versions_deleted} older local resume version(s)."
        
        if extraction_errors:
            message += f" Warnings: {len(extraction_errors)} file(s) had extraction issues."
        
        # Get CSV stats for response
        csv_stats = csv_manager.get_csv_stats()
        
        success = csv_exports > 0
        _log_usage("verify_resumes", f"Verified resumes for rank_folder={rank_folder}", {
            "processed": processed_files,
            "csv_exports": csv_exports,
            "stale_versions_deleted": stale_versions_deleted,
            "warnings": len(extraction_errors),
        })
        return jsonify({
            "success": success,
            "message": message,
            "processed": processed_files,
            "csv_exports": csv_exports,
            "stale_versions_deleted": stale_versions_deleted,
            "errors": extraction_errors,
            "csv_stats": csv_stats,
            "persistence_backend": _current_repo_backend(),
        }), (200 if success else 500)

    except Exception as e:
        print(f"[ERROR] Verify resumes failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_dashboard_data', methods=['GET'])
def get_dashboard_data():
    """Fetch CSV data for dashboard display"""
    try:
        ok, reason = _require_role("admin", "manager", "recruiter")
        if not ok:
            return jsonify({"success": False, "message": reason}), 403
        view_type = request.args.get('view', 'master')  # master, rank, archive, archive_rank
        rank_name = request.args.get('rank_name', '')

        if view_type not in ('master', 'rank', 'archive', 'archive_rank'):
            return jsonify({"success": False, "message": "Invalid view type or missing rank_name"}), 400
        if view_type in ('rank', 'archive_rank') and not rank_name:
            return jsonify({
                "success": True,
                "view": view_type,
                "rank_name": "",
                "total_count": 0,
                "data": [],
                "message": "No rank selected"
            })

        rows = csv_manager.get_latest_status_per_candidate(rank_name if view_type in ('rank', 'archive_rank') else '')

        # Split dashboard into active pipeline (default) vs archived candidates.
        if not rows.empty:
            status_series = rows['Status'].astype(str).str.strip()
            if view_type in ('archive', 'archive_rank'):
                rows = rows[status_series.isin(ARCHIVE_STATUSES)]
            else:
                rows = rows[~status_series.isin(ARCHIVE_STATUSES)]

        if rows.empty:
            return jsonify({
                "success": True,
                "view": view_type,
                "total_count": 0,
                "data": [],
                "message": "No data available yet"
            })
        data = []
        for _, row in rows.iterrows():
            runtime_resume_url = _build_runtime_resume_url_from_stored(
                row.get('Rank_Applied_For', ''),
                row.get('Filename', ''),
                row.get('Resume_URL', '')
            ) or row.get('Resume_URL', '')
            data.append({
                "candidate_id": row.get('Candidate_ID', ''),
                "filename": row.get('Filename', ''),
                "resume_url": runtime_resume_url,
                "date_added": row.get('Date_Added', ''),
                "event_type": row.get('Event_Type', ''),
                "status": row.get('Status', ''),
                "notes": row.get('Notes', ''),
                "rank_applied_for": row.get('Rank_Applied_For', ''),
                "search_ship_type": row.get('Search_Ship_Type', ''),
                "name": row.get('Name', ''),
                "present_rank": row.get('Present_Rank', ''),
                "email": row.get('Email', ''),
                "country": row.get('Country', ''),
                "mobile_no": row.get('Mobile_No', ''),
                "ai_match_reason": row.get('AI_Match_Reason', '')
            })
        
        return jsonify({
            "success": True,
            "view": view_type,
            "rank_name": rank_name if view_type == 'rank' else None,
            "total_count": len(data),
            "data": data
        })
    
    except Exception as e:
        print(f"[ERROR] Dashboard data fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/get_available_ranks', methods=['GET'])
def get_available_ranks():
    """Get list of ranks from master CSV latest-candidate view."""
    try:
        ok, reason = _require_role("admin", "manager", "recruiter")
        if not ok:
            return jsonify({"success": False, "message": reason}), 403
        scope = str(request.args.get('scope', 'active')).strip().lower()  # active|archive|all
        latest_rows = csv_manager.get_latest_status_per_candidate()
        if latest_rows.empty:
            return jsonify({"success": True, "ranks": []})

        if scope == 'archive':
            latest_rows = latest_rows[latest_rows['Status'].astype(str).str.strip().isin(ARCHIVE_STATUSES)]
        elif scope == 'active':
            latest_rows = latest_rows[~latest_rows['Status'].astype(str).str.strip().isin(ARCHIVE_STATUSES)]
        elif scope != 'all':
            return jsonify({"success": False, "message": "Invalid scope"}), 400

        if latest_rows.empty:
            return jsonify({"success": True, "ranks": []})

        rank_counts = (
            latest_rows['Rank_Applied_For']
            .astype(str)
            .str.strip()
        )
        rank_counts = rank_counts[rank_counts != '']
        rank_counts = (
            rank_counts
            .value_counts()
            .reset_index()
        )
        rank_counts.columns = ['Rank_Applied_For', 'count']
        ranks = []
        for _, row in rank_counts.iterrows():
            rank_name = row.get('Rank_Applied_For', '')
            if rank_name:
                ranks.append({
                    "rank": rank_name,
                    "display_name": rank_name.replace('_', ' '),
                    "count": int(row.get('count', 0))
                })

        ranks.sort(key=lambda x: x['rank'])
        
        return jsonify({"success": True, "ranks": ranks})
    
    except Exception as e:
        print(f"[ERROR] Get available ranks failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/get_candidate_history/<candidate_id>', methods=['GET'])
def get_candidate_history(candidate_id):
    """Return full event log for one candidate."""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        history = csv_manager.get_candidate_history(candidate_id)
        return jsonify({
            "success": True,
            "candidate_id": candidate_id,
            "count": len(history),
            "history": history
        })
    except Exception as e:
        print(f"[ERROR] Candidate history fetch failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/update_status', methods=['POST'])
def update_status():
    """Append a status_change event for a candidate."""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json or {}
        candidate_id = str(data.get('candidate_id', '')).strip()
        status = str(data.get('status', '')).strip()

        if not candidate_id or not candidate_id.isdigit():
            return jsonify({"success": False, "message": "Invalid candidate_id"}), 400
        if status not in VALID_STATUSES:
            return jsonify({"success": False, "message": "Invalid status value"}), 400

        history = csv_manager.get_candidate_history(candidate_id)
        if not history:
            return jsonify({"success": False, "message": "Candidate not found"}), 404
        latest = history[-1]
        transition_ok, transition_error, admin_override = _validate_status_transition(
            current_status=latest.get("Status", "New"),
            next_status=status,
        )
        if not transition_ok:
            return jsonify({"success": False, "message": transition_error}), 403

        ok = csv_manager.log_status_change(candidate_id, status, admin_override=admin_override)
        if not ok:
            return jsonify({"success": False, "message": "Candidate not found"}), 404

        _log_usage("status_update", f"Status updated for candidate_id={candidate_id} to {status}")
        return jsonify({"success": True, "message": "Status updated"})
    except Exception as e:
        print(f"[ERROR] Status update failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/add_notes', methods=['POST'])
def add_notes():
    """Append a note_added event for a candidate."""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json or {}
        candidate_id = str(data.get('candidate_id', '')).strip()
        notes = str(data.get('notes', '')).strip()

        if not candidate_id or not candidate_id.isdigit():
            return jsonify({"success": False, "message": "Invalid candidate_id"}), 400
        if not notes:
            return jsonify({"success": False, "message": "Notes cannot be empty"}), 400

        ok = csv_manager.log_note_added(candidate_id, notes)
        if not ok:
            return jsonify({"success": False, "message": "Candidate not found"}), 404

        _log_usage("note_add", f"Note added for candidate_id={candidate_id}", {"length": len(notes)})
        return jsonify({"success": True, "message": "Notes added"})
    except Exception as e:
        print(f"[ERROR] Add notes failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/export_resumes', methods=['POST'])
def export_resumes():
    """Export selected candidates as ZIP (PDFs + CSV snapshot)."""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json or {}
        candidate_ids = data.get('candidate_ids', [])
        if not isinstance(candidate_ids, list) or not candidate_ids:
            return jsonify({"success": False, "message": "candidate_ids is required"}), 400

        clean_ids = [str(c).strip() for c in candidate_ids if str(c).strip().isdigit()]
        if not clean_ids:
            return jsonify({"success": False, "message": "No valid candidate IDs provided"}), 400

        latest_rows = csv_manager.get_latest_status_per_candidate()
        if latest_rows.empty:
            return jsonify({"success": False, "message": "No dashboard data found"}), 404

        selected = latest_rows[latest_rows['Candidate_ID'].astype(str).isin(clean_ids)]
        if selected.empty:
            return jsonify({"success": False, "message": "Selected candidates not found"}), 404

        zip_buffer = io.BytesIO()
        download_root = os.path.abspath(settings['Default_Download_Folder'])
        missing_files = []
        added_files = 0

        with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
            csv_rows = selected.to_dict(orient='records')
            csv_buffer = io.StringIO()
            writer = csv.DictWriter(csv_buffer, fieldnames=list(selected.columns))
            writer.writeheader()
            writer.writerows(csv_rows)
            archive.writestr("selected_candidates.csv", csv_buffer.getvalue())

            for row in csv_rows:
                rank_folder = str(row.get('Rank_Applied_For', '')).strip()
                filename = str(row.get('Filename', '')).strip()

                if not _is_safe_name(rank_folder) or not _is_safe_name(filename):
                    missing_files.append(filename or "invalid_name")
                    continue

                pdf_path = _resolve_within_base(download_root, rank_folder, filename)
                if not os.path.isfile(pdf_path):
                    missing_files.append(filename)
                    continue

                arcname = os.path.join("resumes", rank_folder, filename)
                archive.write(pdf_path, arcname=arcname)
                added_files += 1

        zip_buffer.seek(0)
        timestamp = uuid.uuid4().hex[:8]
        download_name = f"njord_export_{timestamp}.zip"

        response = send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=download_name
        )
        response.headers['X-Exported-Count'] = str(len(selected))
        response.headers['X-Included-Files'] = str(added_files)
        response.headers['X-Missing-Files'] = str(len(missing_files))
        missing_preview = missing_files[:20]
        response.headers['X-Missing-Files-Preview'] = json.dumps(missing_preview)
        response.headers['X-Missing-Files-Truncated'] = str(max(0, len(missing_files) - len(missing_preview)))
        _log_usage("export_resumes", "Resume export completed", {
            "selected": len(selected),
            "included_files": added_files,
            "missing_files": len(missing_files),
        })
        return response

    except Exception as e:
        print(f"[ERROR] Export resumes failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == '__main__':
    _start_ui_idle_shutdown_monitor()
    os.makedirs(settings['Default_Download_Folder'], exist_ok=True)
    os.makedirs(_resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs"), exist_ok=True)
    server_port = int(os.getenv("NJORDHR_PORT", "5000"))
    server_url = f"http://127.0.0.1:{server_port}"

    print("\n" + "="*70)
    print("NjordHR Backend Server - With Dashboard")
    print("="*70)
    print("\nOpen your browser and go to:")
    print(f"   {server_url}")
    print("\n" + "="*70 + "\n")
    
    app.run(host='127.0.0.1', port=server_port, debug=False, threaded=True)
