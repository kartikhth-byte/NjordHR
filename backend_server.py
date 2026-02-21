from flask import Flask, request, jsonify, send_from_directory, Response, send_file
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
from queue import Queue, Empty

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

# --- Configuration ---
app_settings = load_app_settings()
config = app_settings.config
creds = app_settings.credentials
settings = app_settings.settings
feature_flags = app_settings.feature_flags

# --- Global State ---
scraper_session = None

# --- Initialize Extractors ---
resume_extractor = ResumeExtractor()
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VERIFIED_RESUMES_DIR = os.path.join(PROJECT_ROOT, 'Verified_Resumes')
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
    global feature_flags, csv_manager
    feature_flags = FeatureFlags(
        use_supabase_db=_env_bool("USE_SUPABASE_DB", default=False),
        use_dual_write=_env_bool("USE_DUAL_WRITE", default=False),
        use_supabase_reads=_env_bool("USE_SUPABASE_READS", default=False),
        use_local_agent=_env_bool("USE_LOCAL_AGENT", default=False),
        use_cloud_export=_env_bool("USE_CLOUD_EXPORT", default=False),
    )
    csv_manager = build_candidate_event_repo(
        flags=feature_flags,
        base_folder=VERIFIED_RESUMES_DIR,
        server_url=app_settings.server_url
    )
    try:
        Analyzer._instance = None
    except Exception:
        pass


def _admin_token():
    token = os.getenv("NJORDHR_ADMIN_TOKEN", "").strip()
    if token:
        return token
    return config.get("Advanced", "admin_token", fallback="").strip()


def _require_admin():
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


def _mask_secret(value):
    value = str(value or "").strip()
    if not value:
        return {"configured": False, "preview": ""}
    if len(value) <= 8:
        return {"configured": True, "preview": "*" * len(value)}
    return {"configured": True, "preview": f"{value[:4]}...{value[-4:]}"}


def _settings_payload():
    return {
        "non_secret": {
            "default_download_folder": settings.get("Default_Download_Folder", ""),
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
            "seajob_username": _mask_secret(creds.get("Username", "")),
            "seajob_password": _mask_secret(creds.get("Password", "")),
            "gemini_api_key": _mask_secret(creds.get("Gemini_API_Key", "")),
            "pinecone_api_key": _mask_secret(creds.get("Pinecone_API_Key", "")),
            "supabase_secret_key": _mask_secret(resolve_supabase_api_key()),
        }
    }


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


VALID_STATUSES = {
    'New',
    'Contacted',
    'Interested',
    'Not Interested',
    'Interview Scheduled',
    'Offer Made',
    'Hired',
    'Rejected'
}

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

# --- API Endpoints ---
@app.route('/start_session', methods=['POST'])
def start_session():
    global scraper_session
    data = request.json
    mobile_number = data.get('mobileNumber')
    if scraper_session: scraper_session.quit()
    scraper_session = Scraper(settings['Default_Download_Folder'])
    result = scraper_session.start_session(creds['Username'], creds['Password'], mobile_number)
    return jsonify(result)

@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    global scraper_session
    data = request.json
    otp = data.get('otp')
    if not scraper_session:
        return jsonify({"success": False, "message": "Session not started."})
    
    login_result = scraper_session.verify_otp(otp)
    if login_result["success"]:
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
    global scraper_session
    data = request.json
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
    logger, log_filepath = setup_logger(session_id)
    
    result = scraper_session.download_resumes(
        data['rank'], 
        data['shipType'], 
        data['forceRedownload'], 
        logger
    )
    
    result['log_file'] = log_filepath
    return jsonify(result)


@app.route('/download_stream', methods=['GET'])
def download_stream():
    """Stream download progress using Server-Sent Events."""
    global scraper_session

    rank = request.args.get('rank', '').strip()
    ship_type = request.args.get('shipType', '').strip()
    force_redownload_raw = request.args.get('forceRedownload', 'false').strip().lower()
    force_redownload = force_redownload_raw in {'1', 'true', 'yes', 'on'}

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
        logger, log_filepath = setup_logger(session_id)
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
    global scraper_session
    if scraper_session:
        scraper_session.quit()
        scraper_session = None
    return jsonify({"success": True, "message": "Session disconnected successfully."})


@app.route('/session_health', methods=['GET'])
def session_health():
    """Return current scraper session health for OTP/session timeout handling."""
    global scraper_session
    if not scraper_session:
        return jsonify({
            "success": True,
            "connected": False,
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
            "health": health
        })

    return jsonify({
        "success": True,
        "connected": bool(scraper_session and scraper_session.driver),
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
        "verified_resumes_dir": VERIFIED_RESUMES_DIR,
        "supabase_auth": {
            "key_source": key_source,
            "key_hint": key_hint,
            "url_configured": bool(os.getenv("SUPABASE_URL", "").strip()),
        },
        "admin_settings_enabled": bool(_admin_token()),
    })


@app.route('/admin/settings', methods=['GET'])
def get_admin_settings():
    ok, reason = _require_admin()
    if not ok:
        return jsonify({"success": False, "message": reason}), 401
    return jsonify({"success": True, "settings": _settings_payload()})


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
                config.set(section, key, value)

    _set_if_present("Credentials", "Username", "seajob_username")
    _set_if_present("Credentials", "Password", "seajob_password")
    _set_if_present("Credentials", "Gemini_API_Key", "gemini_api_key")
    _set_if_present("Credentials", "Pinecone_API_Key", "pinecone_api_key")
    _set_if_present("Settings", "Default_Download_Folder", "default_download_folder")
    _set_if_present("Advanced", "pinecone_environment", "pinecone_environment")
    _set_if_present("Advanced", "pinecone_index_name", "pinecone_index_name")
    _set_if_present("Advanced", "embedding_model_name", "embedding_model_name")
    _set_if_present("Advanced", "reasoning_model_name", "reasoning_model_name")

    if "min_similarity_score" in payload:
        try:
            score = float(payload.get("min_similarity_score"))
        except Exception:
            return jsonify({"success": False, "message": "min_similarity_score must be a valid number"}), 400
        config.set("Advanced", "min_similarity_score", str(score))

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

    env_flags = [
        "use_supabase_db",
        "use_dual_write",
        "use_supabase_reads",
        "use_local_agent",
        "use_cloud_export",
    ]
    for flag_name in env_flags:
        if flag_name in payload:
            env_name = flag_name.upper()
            os.environ[env_name] = "true" if bool(payload.get(flag_name)) else "false"

    app_settings = load_app_settings()
    config = app_settings.config
    creds = app_settings.credentials
    settings = app_settings.settings
    _refresh_runtime_managers()

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
        return jsonify({"success": False, "message": "New admin password is required"}), 400
    if len(new_password) < 8:
        return jsonify({"success": False, "message": "Admin password must be at least 8 characters"}), 400
    if new_password != confirm_password:
        return jsonify({"success": False, "message": "Password confirmation does not match"}), 400

    if "Advanced" not in config:
        config["Advanced"] = {}
    config.set("Advanced", "admin_token", new_password)

    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    with open(config_path, "w", encoding="utf-8") as fh:
        config.write(fh)

    os.environ["NJORDHR_ADMIN_TOKEN"] = new_password
    app_settings = load_app_settings()
    config = app_settings.config

    return jsonify({"success": True, "message": "Admin password updated successfully."})

@app.route('/get_rank_folders', methods=['GET'])
def get_rank_folders():
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
    prompt = request.args.get('prompt')
    rank_folder = request.args.get('rank_folder')

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
            analyzer = Analyzer(creds['Gemini_API_Key'])
            
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
        
        analyzer = Analyzer(creds['Gemini_API_Key'])
        result = analyzer.run_analysis(rank_folder, prompt)
        
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
    try:
        data = request.json
        
        analyzer = Analyzer(creds['Gemini_API_Key'])
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

@app.route('/get_resume/<path:rank_folder>/<path:filename>')
def get_resume(rank_folder, filename):
    try:
        base_dir = os.path.abspath(settings['Default_Download_Folder'])
        full_path = _resolve_within_base(base_dir, rank_folder, filename)

        if not os.path.isfile(full_path):
            print(f"[ERROR] File not found: {full_path}")
            return "File not found", 404

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

@app.route('/verify_resumes', methods=['POST'])
def verify_resumes():
    """Verify resumes by logging initial verification events to master CSV."""
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

                candidate_history = csv_manager.get_candidate_history(candidate_id)
                event_type = 'resume_updated' if candidate_history else 'initial_verification'

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
                    extracted_data=resume_data
                )

                if csv_ok:
                    csv_exports += 1
                    print(f"[VERIFY] Event logged for {filename}")
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
        
        if extraction_errors:
            message += f" Warnings: {len(extraction_errors)} file(s) had extraction issues."
        
        # Get CSV stats for response
        csv_stats = csv_manager.get_csv_stats()
        
        success = csv_exports > 0
        return jsonify({
            "success": success,
            "message": message,
            "processed": processed_files,
            "csv_exports": csv_exports,
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
        view_type = request.args.get('view', 'master')  # 'master' or 'rank'
        rank_name = request.args.get('rank_name', '')
        
        if view_type not in ('master', 'rank'):
            return jsonify({"success": False, "message": "Invalid view type or missing rank_name"}), 400
        if view_type == 'rank' and not rank_name:
            return jsonify({
                "success": True,
                "view": "rank",
                "rank_name": "",
                "total_count": 0,
                "data": [],
                "message": "No rank selected"
            })

        rows = csv_manager.get_latest_status_per_candidate(rank_name if view_type == 'rank' else '')

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
            data.append({
                "candidate_id": row.get('Candidate_ID', ''),
                "filename": row.get('Filename', ''),
                "resume_url": row.get('Resume_URL', ''),
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
        rank_counts = csv_manager.get_rank_counts()
        ranks = []
        for row in rank_counts:
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
    try:
        data = request.json or {}
        candidate_id = str(data.get('candidate_id', '')).strip()
        status = str(data.get('status', '')).strip()

        if not candidate_id or not candidate_id.isdigit():
            return jsonify({"success": False, "message": "Invalid candidate_id"}), 400
        if status not in VALID_STATUSES:
            return jsonify({"success": False, "message": "Invalid status value"}), 400

        ok = csv_manager.log_status_change(candidate_id, status)
        if not ok:
            return jsonify({"success": False, "message": "Candidate not found"}), 404

        return jsonify({"success": True, "message": "Status updated"})
    except Exception as e:
        print(f"[ERROR] Status update failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/add_notes', methods=['POST'])
def add_notes():
    """Append a note_added event for a candidate."""
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

        return jsonify({"success": True, "message": "Notes added"})
    except Exception as e:
        print(f"[ERROR] Add notes failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/export_resumes', methods=['POST'])
def export_resumes():
    """Export selected candidates as ZIP (PDFs + CSV snapshot)."""
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
        return response

    except Exception as e:
        print(f"[ERROR] Export resumes failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == '__main__':
    os.makedirs(settings['Default_Download_Folder'], exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    print("\n" + "="*70)
    print("🚀 NjordHR Backend Server - With Dashboard")
    print("="*70)
    print("\n🌐 Open your browser and go to:")
    print("   👉 http://127.0.0.1:5000")
    print("\n" + "="*70 + "\n")
    
    app.run(port=5000, debug=False, threaded=True)
