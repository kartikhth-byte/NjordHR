from flask import Flask, request, jsonify, send_from_directory, Response, send_file, redirect, has_request_context, session
from flask_cors import CORS
import configparser
import csv
import hashlib
import io
import math
import os
import re
import sys
import string
import uuid
import json
import zipfile
import logging
import threading
import secrets
from datetime import UTC, datetime
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
from logger_config import setup_logger
from resume_extractor import ResumeExtractor
from app_settings import load_app_settings, FeatureFlags
from cloud_api.runtime import cloud_api_settings_payload, load_cloud_api_settings
from repositories.repo_factory import build_candidate_event_repo
from repositories.search_scope_repo import SQLiteSearchScopeRepository
from repositories.supabase_candidate_event_repo import resolve_supabase_api_key
from runtime_env import config_value, normalize_env_value, normalized_url
from ai_analyzer import Analyzer
from candidate_facts.repository import CandidateFactsRepository
from candidate_facts.validation_cache import candidate_facts_validation_cache_base_dir
from query_understanding import build_shadow_audit_entry, build_shadow_llm_query_plan
from query_understanding.supabase_telemetry_store import SupabaseTelemetryStore

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
candidate_facts_repo = None
supabase_telemetry_store = None

# --- Initialize Extractors ---
resume_extractor = ResumeExtractor()
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _build_analyzer():
    analyzer_config = configparser.ConfigParser()
    analyzer_config.read_dict({section: dict(config[section]) for section in config.sections()})
    if not analyzer_config.has_section("Settings"):
        analyzer_config.add_section("Settings")
    analyzer_config.set("Settings", "Default_Download_Folder", _active_download_root())
    return Analyzer(analyzer_config, feature_flags=feature_flags)


def _resolve_advanced_runtime_path(raw_path, fallback_name):
    candidate = str(raw_path or "").strip() or fallback_name
    candidate = os.path.expanduser(candidate)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate)
    log_dir = os.path.abspath(os.path.expanduser(config.get("Advanced", "log_dir", fallback="logs") or "logs"))
    return os.path.abspath(os.path.join(log_dir, candidate))


def _resolve_search_scope_db_path():
    configured = config.get("Advanced", "search_scope_db_path", fallback="")
    if str(configured or "").strip():
        return _resolve_advanced_runtime_path(configured, "ai_search_scope.db")
    registry_db_path = _resolve_advanced_runtime_path(
        config.get("Advanced", "registry_db_path", fallback="registry.db"),
        "registry.db",
    )
    return os.path.join(os.path.dirname(registry_db_path), "ai_search_scope.db")


def _build_search_scope_repo():
    return SQLiteSearchScopeRepository(_resolve_search_scope_db_path())


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
search_scope_repo = _build_search_scope_repo()


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _payload_bool(payload, key, default=False):
    if key not in payload:
        return default
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ai_search_request_fingerprint(
    *,
    prompt,
    rank_folder_id="",
    rank_folder="",
    applied_ship_type="",
    experienced_ship_type="",
    vessel_tonnage_filter=None,
    parent_search_session_id="",
    changed_content_acknowledgement_id="",
):
    is_refinement = bool(str(parent_search_session_id or "").strip())
    payload = {
        "prompt": str(prompt or "").strip(),
        "rank_folder_id": "" if is_refinement else str(rank_folder_id or "").strip(),
        "rank_folder": "" if is_refinement else str(rank_folder or "").strip(),
        "applied_ship_type": "" if is_refinement else str(applied_ship_type or "").strip(),
        "experienced_ship_type": "" if is_refinement else str(experienced_ship_type or "").strip(),
        "vessel_tonnage_filter": {} if is_refinement else _normalize_vessel_tonnage_filter(vessel_tonnage_filter),
        "parent_search_session_id": str(parent_search_session_id or "").strip(),
        "changed_content_acknowledgement_id": (
            str(changed_content_acknowledgement_id or "").strip()
            if is_refinement
            else ""
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", "ignore")).hexdigest()


def _normalize_vessel_tonnage_filter(value):
    if not isinstance(value, dict):
        return {}

    def positive_int(raw):
        if isinstance(raw, bool) or raw in (None, ""):
            return None
        try:
            parsed = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    min_value = positive_int(value.get("min_value"))
    max_value = positive_int(value.get("max_value"))
    if min_value is None and max_value is None:
        return {}
    if min_value is not None and max_value is not None and min_value > max_value:
        return {}
    unit = str(value.get("unit") or "any").strip().lower()
    if unit not in {"any", "unspecified", "gt_grt", "dwt"}:
        unit = "any"
    return {
        "type": "vessel_tonnage",
        "min_value": min_value,
        "max_value": max_value,
        "unit": unit,
    }


def _parse_vessel_tonnage_filter_payload(raw):
    raw_text = str(raw or "").strip()
    if not raw_text:
        return {}
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError):
        return {}
    return _normalize_vessel_tonnage_filter(payload)


def _request_status_event(claim_result):
    request_status = claim_result.get("request_status") or "SEARCH_REQUEST_STORE_UNAVAILABLE"
    payload = {
        "type": "error" if request_status == "SEARCH_REQUEST_ID_CONFLICT" else "request_status",
        "request_status": request_status,
        "message": claim_result.get("message") or "AI Search request tracking is temporarily unavailable.",
        "retryable": bool(claim_result.get("retryable", False)),
        "search_request_id": claim_result.get("search_request_id") or "",
        "search_session_id": claim_result.get("search_session_id") or "",
    }
    if claim_result.get("retry_after_seconds") is not None:
        payload["retry_after_seconds"] = claim_result.get("retry_after_seconds")
    if claim_result.get("error_code"):
        payload["error_code"] = claim_result.get("error_code")
    if claim_result.get("summary"):
        payload["summary"] = claim_result.get("summary")
        if request_status == "SEARCH_REQUEST_ALREADY_COMPLETE":
            payload["delivery_mode"] = "metadata_only"
            payload["replay_available"] = False
    return payload


def _cloud_api_runtime_summary():
    settings = load_cloud_api_settings()
    return cloud_api_settings_payload(settings)


def _candidate_facts_review_cache_dir():
    override = str(os.getenv("NJORDHR_CANDIDATE_FACTS_CACHE_DIR", "")).strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return candidate_facts_validation_cache_base_dir()


def _candidate_facts_supabase_config():
    if not bool(getattr(feature_flags, "use_supabase_db", False)):
        return {}
    supabase_url = _supabase_url()
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return {}
    return {
        "supabase_url": supabase_url,
        "supabase_service_role_key": supabase_key,
    }


def _candidate_facts_repository():
    global candidate_facts_repo
    cache_dir = _candidate_facts_review_cache_dir()
    supabase_config = _candidate_facts_supabase_config()
    if candidate_facts_repo is None:
        candidate_facts_repo = CandidateFactsRepository(
            validation_cache_dir=cache_dir,
            **supabase_config,
        )
        return candidate_facts_repo

    repo_cache_dir = os.path.abspath(os.path.expanduser(str(candidate_facts_repo.validation_cache_dir or ""))) if candidate_facts_repo.validation_cache_dir else ""
    current_supabase_url = str(getattr(candidate_facts_repo, "supabase_url", "") or "")
    current_supabase_key = str(getattr(candidate_facts_repo, "supabase_service_role_key", "") or "")
    expected_supabase_url = str(supabase_config.get("supabase_url", "") or "")
    expected_supabase_key = str(supabase_config.get("supabase_service_role_key", "") or "")
    if (
        repo_cache_dir != os.path.abspath(os.path.expanduser(cache_dir))
        or current_supabase_url != expected_supabase_url
        or current_supabase_key != expected_supabase_key
    ):
        candidate_facts_repo = CandidateFactsRepository(
            validation_cache_dir=cache_dir,
            **supabase_config,
        )
    return candidate_facts_repo


def _supabase_telemetry_store():
    global supabase_telemetry_store
    supabase_url = _supabase_url()
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return None
    if (
        supabase_telemetry_store is None
        or str(getattr(supabase_telemetry_store, "supabase_url", "") or "") != supabase_url
        or str(getattr(supabase_telemetry_store, "service_role_key", "") or "") != supabase_key
    ):
        supabase_telemetry_store = SupabaseTelemetryStore(
            supabase_url=supabase_url,
            service_role_key=supabase_key,
        )
    return supabase_telemetry_store


def _should_force_shadow_llm(actor_role=None):
    override = os.getenv("NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM_FORCE", "").strip().lower()
    if override in {"1", "true", "yes", "on", "enabled"}:
        return True
    if override in {"0", "false", "no", "off", "disabled"}:
        return False
    role = str(actor_role or "").strip().lower()
    if role:
        return role == "recruiter"
    if has_request_context():
        return _session_role() == "recruiter"
    return False


def _record_supabase_telemetry(
    *,
    telemetry_kind,
    category,
    status,
    summary,
    payload,
    prompt_hash="",
    prompt_text="",
    actor_role="",
    actor_username="",
    session_id="",
):
    store = _supabase_telemetry_store()
    if store is None:
        return None
    request_id = str(session_id or "")
    if not request_id and has_request_context():
        request_id = str(session.get("session_id") or request.headers.get("X-Request-Id") or "")
    try:
        return store.log_event(
            store.build_payload(
                telemetry_kind=telemetry_kind,
                category=category,
                status=status,
                summary=summary,
                payload=payload or {},
                prompt_hash=prompt_hash,
                prompt_text=prompt_text,
                actor_role=actor_role or (_session_role() if has_request_context() else ""),
                actor_username=actor_username or (_session_username() if has_request_context() else ""),
                session_id=request_id,
                source="backend_server",
            )
        )
    except Exception as exc:
        print(f"[BACKEND WARN] Failed to persist telemetry: {exc}")
        return None


def _list_all_prompt_audit_summaries(store, page_size=200):
    rows = []
    offset = 0
    page_size = max(1, min(int(page_size or 200), 1000))
    while True:
        page = store.list_prompt_audit_summaries(limit=page_size, offset=offset)
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def _telemetry_store_raw_prompts_enabled():
    override = os.getenv("NJORDHR_TELEMETRY_STORE_RAW_PROMPTS", "").strip().lower()
    if override in {"1", "true", "yes", "on", "enabled"}:
        return True
    if override in {"0", "false", "no", "off", "disabled"}:
        return False
    return False


def _scrub_shadow_audit_value(value):
    sensitive_keys = {"prompt", "raw_prompt", "source_text", "prompt_text"}
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in sensitive_keys:
                continue
            scrubbed[key] = _scrub_shadow_audit_value(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_shadow_audit_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_shadow_audit_value(item) for item in value)
    return value


def _redact_shadow_audit_payload(payload):
    if not isinstance(payload, dict):
        return payload
    redacted = _scrub_shadow_audit_value(dict(payload))
    redacted.pop("prompt", None)
    redacted.pop("legacy_plan", None)
    redacted.pop("llm_plan", None)
    redacted["payload_redacted"] = True
    return redacted


def _schedule_search_prompt_audit(analyzer, prompt, rank_folder, *, search_session_id="", actor_role="", actor_username=""):
    def _worker():
        try:
            audit_analyzer = _build_analyzer()
            _log_search_prompt_audit(
                audit_analyzer,
                prompt,
                rank_folder,
                search_session_id=search_session_id,
                actor_role=actor_role,
                actor_username=actor_username,
            )
        except Exception as exc:
            print(f"[BACKEND WARN] Failed to schedule prompt audit: {exc}")

    try:
        thread = threading.Thread(
            target=_worker,
            name=f"njordhr-prompt-audit-{search_session_id or 'search'}",
            daemon=True,
        )
        thread.start()
        return thread
    except Exception as exc:
        print(f"[BACKEND WARN] Failed to start prompt audit thread: {exc}")
        return None


def _log_search_prompt_audit(analyzer, prompt, rank_folder, *, search_session_id="", actor_role="", actor_username=""):
    force_shadow = _should_force_shadow_llm(actor_role=actor_role)
    try:
        shadow_audit = build_shadow_audit_entry(
            analyzer,
            prompt,
            rank=rank_folder,
            prompt_id=search_session_id or hashlib.sha256(f"{rank_folder}::{prompt}".encode("utf-8")).hexdigest()[:16],
            llm_plan_provider=build_shadow_llm_query_plan,
            force_shadow=force_shadow,
        )
    except Exception as exc:
        payload = {
            "rank_folder": rank_folder,
            "force_shadow": force_shadow,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _record_supabase_telemetry(
            telemetry_kind="prompt_audit",
            category="query_understanding",
            status="failed",
            summary="Shadow prompt audit failed.",
            payload=payload,
            prompt_hash=hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest(),
            prompt_text=str(prompt or "")[:5000] if _telemetry_store_raw_prompts_enabled() else "",
            actor_role=actor_role,
            actor_username=actor_username,
            session_id=search_session_id,
        )
        return None

    comparison_results = shadow_audit.get("comparison_results") or []
    outcome_counts = {}
    for item in comparison_results:
        outcome = str(item.get("comparison_outcome") or "unknown")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    llm_status = ((shadow_audit.get("shadow_wiring") or {}).get("llm_plan_source") or "disabled")
    issue_outcomes = {"regression", "schema_error", "catalogue_drift"}
    has_issue = any(outcome in issue_outcomes for outcome in outcome_counts)
    status = "disabled"
    if shadow_audit.get("shadow_mode") == "enabled":
        status = "issue" if has_issue or bool((shadow_audit.get("shadow_wiring") or {}).get("failure_reason")) else "ok"
    summary = (
        f"shadow={shadow_audit.get('shadow_mode')} llm={llm_status} "
        f"comparisons={len(comparison_results)} outcomes={outcome_counts}"
    )
    _record_supabase_telemetry(
        telemetry_kind="prompt_audit",
        category="query_understanding",
        status=status,
        summary=summary,
        payload=_redact_shadow_audit_payload(shadow_audit),
        prompt_hash=hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest(),
        prompt_text=str(prompt or "")[:5000] if _telemetry_store_raw_prompts_enabled() else "",
        actor_role=actor_role,
        actor_username=actor_username,
        session_id=search_session_id,
    )
    return shadow_audit


def _candidate_facts_review_capture_callback(candidate_facts, capture_context):
    try:
        repo = _candidate_facts_repository()
        candidate_resume_id = str((capture_context or {}).get("candidate_resume_id") or (capture_context or {}).get("candidate_id") or "").strip()
        resume_blob_id = str((capture_context or {}).get("resume_blob_id") or (capture_context or {}).get("filename") or candidate_resume_id).strip()
        parser_version = str((capture_context or {}).get("parser_version") or ((candidate_facts or {}).get("extraction") or {}).get("parser_version") or "generic_pdf.v1").strip()
        facts_revision = str((capture_context or {}).get("facts_revision") or (candidate_facts or {}).get("facts_version") or (candidate_facts or {}).get("schema_version") or "candidate_facts.v1").strip()
        review_alignment_report = capture_context.get("review_alignment_report") if isinstance(capture_context, dict) else None
        review_alignment_status = str((capture_context or {}).get("review_alignment_status") or "").strip()
        review_alignment_mismatch_count = capture_context.get("review_alignment_mismatch_count") if isinstance(capture_context, dict) else None
        review_alignment_mismatches = capture_context.get("review_alignment_mismatches") if isinstance(capture_context, dict) else None
        if not candidate_resume_id:
            raise RuntimeError("candidate_resume_id is required for review capture")
        if not resume_blob_id:
            raise RuntimeError("resume_blob_id is required for review capture")
        if not isinstance(candidate_facts, dict) or not candidate_facts:
            raise RuntimeError("candidate_facts payload is required for review capture")
        return repo.capture_normalized_candidate_facts_for_review(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
            review_alignment_report=review_alignment_report if isinstance(review_alignment_report, dict) else None,
            review_alignment_status=review_alignment_status or None,
            review_alignment_mismatch_count=review_alignment_mismatch_count,
            review_alignment_mismatches=review_alignment_mismatches if isinstance(review_alignment_mismatches, list) else None,
        )
    except Exception as exc:
        print(f"[REVIEW CAPTURE WARN] Failed to capture candidate facts from live analysis: {exc}")
        return {"success": False, "message": str(exc)}


def _refresh_runtime_managers():
    global app_settings, config, creds, settings, feature_flags, csv_manager, search_scope_repo, VERIFIED_RESUMES_DIR, candidate_facts_repo
    app_settings = load_app_settings()
    config = app_settings.config
    creds = app_settings.credentials
    settings = app_settings.settings
    feature_flags = app_settings.feature_flags
    _load_runtime_secrets_from_cloud()
    VERIFIED_RESUMES_DIR = _resolve_verified_resumes_dir()
    os.makedirs(VERIFIED_RESUMES_DIR, exist_ok=True)
    csv_manager = build_candidate_event_repo(
        flags=feature_flags,
        base_folder=VERIFIED_RESUMES_DIR,
        server_url=app_settings.server_url
    )
    try:
        old_scope_repo = search_scope_repo
    except NameError:
        old_scope_repo = None
    new_scope_db_path = _resolve_search_scope_db_path()
    if old_scope_repo is None or str(getattr(old_scope_repo, "db_path", "")) != new_scope_db_path:
        search_scope_repo = SQLiteSearchScopeRepository(new_scope_db_path)
        if old_scope_repo is not None:
            try:
                old_scope_repo.close()
            except Exception:
                pass
    candidate_facts_repo = None
    try:
        Analyzer._instance = None
    except Exception:
        pass


def _advanced_value(name, fallback=""):
    return config.get("Advanced", name, fallback=fallback)


def _advanced_bool(name, fallback=False):
    raw = normalize_env_value(config.get("Advanced", name, fallback=""))
    if raw:
        return raw.lower() in {"1", "true", "yes", "on"}
    return bool(fallback)


def _credential_value(config_key, env_name, fallback=""):
    config_value = normalize_env_value(creds.get(config_key, fallback=fallback))
    if config_value:
        return config_value
    return normalize_env_value(os.getenv(env_name, ""))


def _set_config_value_or_clear(section, key, value):
    if value:
        _config_set_literal(config, section, key, value)
    elif config.has_option(section, key):
        config.remove_option(section, key)


def _set_env_value_or_clear(name, value):
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def _seajob_username():
    return _credential_value("Username", "SEAJOB_USERNAME", "")


def _seajob_password():
    return _credential_value("Password", "SEAJOB_PASSWORD", "")


def _gemini_api_key():
    return _credential_value("Gemini_API_Key", "GEMINI_API_KEY", "")


def _pinecone_api_key():
    return _credential_value("Pinecone_API_Key", "PINECONE_API_KEY", "")


def _supabase_secret_key():
    return _credential_value("Supabase_Secret_Key", "SUPABASE_SECRET_KEY", "")


def _supabase_service_role_key():
    return _credential_value("Supabase_Service_Role_Key", "SUPABASE_SERVICE_ROLE_KEY", "")


def _supabase_url():
    configured = config_value("Advanced", "supabase_url", "")
    if configured:
        return normalized_url(configured)
    return normalized_url(os.getenv("SUPABASE_URL", ""))


def _supabase_runtime_config_endpoint(supabase_url=None, supabase_key=None):
    supabase_url = normalized_url(supabase_url or _supabase_url())
    supabase_key = normalize_env_value(supabase_key or resolve_supabase_api_key())
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


def _supabase_runtime_config_get(supabase_url=None, supabase_key=None):
    endpoint, headers = _supabase_runtime_config_endpoint(supabase_url=supabase_url, supabase_key=supabase_key)
    if not endpoint:
        return None
    try:
        resp = requests.get(
            endpoint,
            params={"select": "key,value", "limit": 2000},
            headers=headers,
            timeout=10,
        )
        if resp.status_code >= 400:
            return None
        rows = resp.json() if resp.text else []
        out = {}
        for row in rows or []:
            key = str(row.get("key", "")).strip()
            if key:
                out[key] = str(row.get("value", "") or "")
        return out
    except Exception:
        return None


def _supabase_runtime_config_set(pairs, supabase_url=None, supabase_key=None):
    endpoint, headers = _supabase_runtime_config_endpoint(supabase_url=supabase_url, supabase_key=supabase_key)
    if not endpoint:
        raise RuntimeError("Supabase runtime config unavailable")
    body = []
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for key, value in (pairs or {}).items():
        if key is None:
            continue
        key_s = str(key).strip()
        if not key_s:
            continue
        value_s = normalize_env_value(value)
        body.append({
            "key": key_s,
            "value": value_s,
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
    if cfg is None:
        return
    mapping = {
        "seajob_username": "SEAJOB_USERNAME",
        "seajob_password": "SEAJOB_PASSWORD",
        "gemini_api_key": "GEMINI_API_KEY",
        "pinecone_api_key": "PINECONE_API_KEY",
    }
    for key, env_name in mapping.items():
        val = normalize_env_value(cfg.get(key, ""))
        if val:
            os.environ[env_name] = val
        else:
            os.environ.pop(env_name, None)


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


def _is_windows():
    return os.name == "nt"


def _list_windows_drive_entries():
    if not _is_windows():
        return []

    drive_paths = []
    try:
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_paths.append(f"{letter}:\\")
            bitmask >>= 1
    except Exception:
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                drive_paths.append(drive)

    entries = []
    seen = set()
    for drive in drive_paths:
        normalized = os.path.abspath(drive)
        if normalized in seen:
            continue
        seen.add(normalized)
        entries.append({"name": drive, "path": normalized})
    return entries


def _is_windows_drive_root(path):
    if not _is_windows():
        return False
    drive, tail = os.path.splitdrive(path)
    if not drive:
        return False
    return tail in ("\\", "/")


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


def _rank_manifest_data(rank_folder):
    base_folder = _active_download_root()
    rank_path = _resolve_within_base(base_folder, rank_folder)
    manifest_path = os.path.join(rank_path, "manifest.json")
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        files = data.get("files")
        return files if isinstance(files, dict) else {}
    except Exception:
        return {}


def _list_visible_rank_folders(base_folder):
    try:
        names = []
        for entry in os.listdir(base_folder):
            if not entry or entry.startswith("."):
                continue
            full_path = os.path.join(base_folder, entry)
            if not os.path.isdir(full_path):
                continue
            try:
                child_names = os.listdir(full_path)
            except Exception:
                continue
            has_manifest = "manifest.json" in child_names
            has_pdf = any(str(name).lower().endswith(".pdf") for name in child_names)
            if not has_manifest and not has_pdf:
                continue
            names.append(entry)
        return sorted(names)
    except Exception:
        return []


def _list_assignable_rank_folders(base_folder):
    return [name for name in _list_visible_rank_folders(base_folder) if not str(name).startswith("_")]


def _opaque_catalog_id(prefix, *parts):
    payload = json.dumps([str(part or "") for part in parts], separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _catalog_stat_parts(path):
    resolved = os.path.realpath(os.path.abspath(path))
    stat = os.stat(resolved)
    return {
        "resolved_path": resolved,
        "norm_path": os.path.normcase(resolved),
        "device": str(getattr(stat, "st_dev", "")),
        "inode": str(getattr(stat, "st_ino", "")),
    }


def _download_root_catalog_record(base_folder=None):
    root_path = str(base_folder or _active_download_root() or "").strip()
    if not root_path or not os.path.isdir(root_path):
        return None
    try:
        identity = _catalog_stat_parts(root_path)
    except Exception:
        return None
    download_root_id = _opaque_catalog_id(
        "dr",
        identity["norm_path"],
    )
    return {
        "download_root_id": download_root_id,
        "_resolved_path": identity["resolved_path"],
    }


def _rank_folder_catalog_record(base_folder, folder):
    folder = str(folder or "").strip()
    if not folder or not _is_safe_name(folder):
        return None
    root_record = _download_root_catalog_record(base_folder)
    if not root_record:
        return None
    try:
        folder_path = _resolve_within_base(base_folder, folder)
        if not os.path.isdir(folder_path):
            return None
        identity = _catalog_stat_parts(folder_path)
    except Exception:
        return None
    rank_folder_id = _opaque_catalog_id(
        "rf",
        root_record["download_root_id"],
        folder,
        identity["norm_path"],
    )
    return {
        "download_root_id": root_record["download_root_id"],
        "rank_folder_id": rank_folder_id,
        "folder": folder,
        "legacy_rank_folder": folder,
        "display_name": folder.replace("_", " "),
        "_resolved_path": identity["resolved_path"],
    }


def _rank_folder_catalog(base_folder=None):
    root_path = str(base_folder or _active_download_root() or "").strip()
    if not root_path or not os.path.isdir(root_path):
        return []
    records = []
    for folder in _list_assignable_rank_folders(root_path):
        record = _rank_folder_catalog_record(root_path, folder)
        if record:
            records.append(record)
    return records


def _public_rank_folder_record(record):
    return {
        "download_root_id": record.get("download_root_id", ""),
        "rank_folder_id": record.get("rank_folder_id", ""),
        "folder": record.get("folder", ""),
        "legacy_rank_folder": record.get("legacy_rank_folder", record.get("folder", "")),
        "display_name": record.get("display_name", record.get("folder", "")),
    }


def _resolve_rank_folder_reference(*, rank_folder_id="", rank_folder="", base_folder=None):
    catalog = _rank_folder_catalog(base_folder)
    rank_folder_id = str(rank_folder_id or "").strip()
    rank_folder = str(rank_folder or "").strip()
    if rank_folder_id:
        matches = [record for record in catalog if record.get("rank_folder_id") == rank_folder_id]
        if len(matches) == 1:
            return {"success": True, "record": matches[0]}
        if rank_folder and _is_safe_name(rank_folder):
            root_path = str(base_folder or _active_download_root() or "").strip()
            legacy_record = _rank_folder_catalog_record(root_path, rank_folder)
            if legacy_record and legacy_record.get("rank_folder_id") == rank_folder_id:
                return {"success": True, "record": legacy_record}
        return {
            "success": False,
            "error_code": "INVALID_RANK_FOLDER_ID",
            "message": "Selected rank folder is no longer available.",
        }
    if rank_folder:
        if not _is_safe_name(rank_folder):
            return {
                "success": False,
                "error_code": "INVALID_RANK_FOLDER",
                "message": "Invalid rank folder.",
            }
        matches = [record for record in catalog if record.get("folder") == rank_folder]
        if len(matches) == 1:
            return {"success": True, "record": matches[0]}
        root_path = str(base_folder or _active_download_root() or "").strip()
        legacy_record = _rank_folder_catalog_record(root_path, rank_folder)
        if legacy_record:
            return {"success": True, "record": legacy_record}
        return {
            "success": False,
            "error_code": "RANK_FOLDER_NOT_FOUND",
            "message": "Rank folder not found.",
        }
    return {
        "success": False,
        "error_code": "RANK_FOLDER_REQUIRED",
        "message": "Rank folder is required.",
    }


def _configured_rank_options():
    ranks_str = config.get('Ranks', 'rank_options', fallback='').strip()
    return [rank.strip() for rank in ranks_str.split('\n') if rank.strip()]


def _configured_download_root():
    configured = str(settings.get('Default_Download_Folder', '')).strip()
    if not configured:
        return ""
    return os.path.abspath(os.path.expanduser(configured))


def _active_download_root():
    if _use_local_agent():
        try:
            resp = _agent_request("GET", "/settings", timeout=5)
            if getattr(resp, "status_code", 500) < 400:
                payload = resp.json()
                agent_settings = payload.get("settings") if isinstance(payload, dict) else {}
                candidate = str((agent_settings or {}).get("download_folder", "")).strip()
                if candidate:
                    resolved = os.path.abspath(os.path.expanduser(candidate))
                    if os.path.isdir(resolved):
                        return resolved
        except Exception:
            pass
    return _configured_download_root()


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
        global auto_shutdown_in_progress
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
    configured = config.get("Advanced", "admin_token", fallback="").strip()
    if _is_placeholder_password(configured):
        configured = ""
    if configured:
        return configured
    token = os.getenv("NJORDHR_ADMIN_TOKEN", "").strip()
    if token and not _is_placeholder_password(token):
        return token
    return ""


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
    admin_username = auth_cfg.get("admin_username", "").strip() or os.getenv("NJORDHR_ADMIN_USERNAME", "admin").strip() or "admin"
    admin_password = auth_cfg.get("admin_password", "").strip() or os.getenv("NJORDHR_ADMIN_PASSWORD", "").strip() or _admin_token()
    manager_username = auth_cfg.get("manager_username", "").strip() or os.getenv("NJORDHR_MANAGER_USERNAME", "manager").strip() or "manager"
    manager_password = auth_cfg.get("manager_password", "").strip() or os.getenv("NJORDHR_MANAGER_PASSWORD", "").strip()
    recruiter_username = auth_cfg.get("recruiter_username", "").strip() or os.getenv("NJORDHR_RECRUITER_USERNAME", "recruiter").strip() or "recruiter"
    recruiter_password = auth_cfg.get("recruiter_password", "").strip() or os.getenv("NJORDHR_RECRUITER_PASSWORD", "").strip()

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
    raw = normalize_env_value(config.get("Advanced", "auth_mode", fallback="")).lower()
    if not raw:
        raw = normalize_env_value(os.getenv("NJORDHR_AUTH_MODE", "auto")).lower()
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
    return normalize_env_value(os.getenv("NJORDHR_PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000")) or "pbkdf2:sha256:600000"


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
    supabase_url = _supabase_url()
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
            return {"username": username, "role": record.get("role", ""), "id": record.get("id")}
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
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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


def _session_actor_user_id():
    explicit_id = str(session.get("user_id", "")).strip()
    if explicit_id:
        return explicit_id
    username = _session_username()
    if not username:
        return ""
    mode = "cloud" if _auth_mode() == "cloud" else "local"
    digest = hashlib.sha256(username.strip().lower().encode("utf-8", "ignore")).hexdigest()
    return f"{mode}:legacy:{digest[:32]}"


def _scope_memberships_from_verified_matches(verified_matches):
    memberships = []
    missing_identity = []
    seen_scope_ids = set()
    for match in verified_matches or []:
        if not isinstance(match, dict):
            continue
        candidate_scope_id = str(match.get("candidate_scope_id") or "").strip()
        filename = str(match.get("filename") or "").strip()
        if not candidate_scope_id:
            missing_identity.append(filename or str(match.get("resume_id") or "").strip() or "unknown")
            continue
        if candidate_scope_id in seen_scope_ids:
            continue
        seen_scope_ids.add(candidate_scope_id)
        hard_filter_reasons = match.get("hard_filter_reasons") or []
        reason_codes = [
            str(reason.get("reason_code") or "").strip()
            for reason in hard_filter_reasons
            if isinstance(reason, dict) and str(reason.get("reason_code") or "").strip()
        ]
        confidence = match.get("confidence")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        decision_mode = "deterministic" if confidence_value >= 1.0 else "mixed"
        memberships.append({
            "candidate_scope_id": candidate_scope_id,
            "content_hash_at_event": str(match.get("content_hash") or "").strip(),
            "result_bucket": "verified_match",
            "filename": filename,
            "resume_id": str(match.get("resume_id") or "").strip(),
            "decision_mode": decision_mode,
            "facts_version": str(match.get("facts_version") or "").strip(),
            "reason_codes": reason_codes,
            "lineage_warning_codes": list(dict.fromkeys(
                str(code or "").strip()
                for code in match.get("lineage_warning_codes", [])
                if str(code or "").strip()
            )),
        })
    return memberships, missing_identity


def _changed_content_set_fingerprint(changed_members):
    canonical_members = sorted(
        (
            {
                "candidate_scope_id": str(member.get("candidate_scope_id") or "").strip(),
                "parent_content_hash": str(member.get("parent_content_hash") or "").strip(),
                "current_content_hash": str(member.get("current_content_hash") or "").strip(),
            }
            for member in (changed_members or [])
            if str(member.get("candidate_scope_id") or "").strip()
        ),
        key=lambda member: member["candidate_scope_id"],
    )
    if not canonical_members:
        return ""
    payload = json.dumps(canonical_members, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()


def _resolve_refinement_scope_preflight(parent_search_session_id, *, actor_user_id):
    parent_scope = search_scope_repo.get_refinement_parent_scope(
        parent_search_session_id,
        actor_user_id=actor_user_id,
    )
    if not parent_scope.get("success"):
        return parent_scope

    parent_session = parent_scope.get("session") or {}
    rank_folder = str(parent_session.get("rank_folder") or "").strip()
    context = parent_session.get("context") or {}
    rank_folder_id = str(context.get("rank_folder_id") or "").strip()
    resolved_rank = _resolve_rank_folder_reference(
        rank_folder_id=rank_folder_id,
        rank_folder=rank_folder,
    )
    if not resolved_rank.get("success"):
        return {
            "success": False,
            "available": False,
            "error_code": "REFINEMENT_CONTEXT_UNAVAILABLE",
            "message": "The original rank folder is no longer available.",
            "retryable": False,
        }
    rank_record = resolved_rank["record"]
    rank_folder = rank_record["folder"]
    target_folder = rank_record["_resolved_path"]
    if not os.path.isdir(target_folder):
        return {
            "success": False,
            "available": False,
            "error_code": "REFINEMENT_CONTEXT_UNAVAILABLE",
            "message": "The original rank folder is no longer available.",
            "retryable": False,
        }

    analyzer = _build_analyzer()
    scope_summary = analyzer.resolve_candidate_scope_snapshot(
        target_folder,
        parent_scope.get("candidate_scope_ids") or [],
        candidate_scope_memberships=parent_scope.get("memberships") or [],
    )
    changed_members = list(scope_summary.pop("changed_members", []) or [])
    scope_summary.pop("resolved_candidate_scope_ids", None)
    requested_count = int(scope_summary.get("requested_count") or 0)
    resolved_count = int(scope_summary.get("resolved_count") or 0)
    scope_summary["changed_content_set_fingerprint"] = _changed_content_set_fingerprint(changed_members)
    scope_summary["unavailable_percentage"] = round(
        (max(0, requested_count - resolved_count) / requested_count) * 100,
        2,
    ) if requested_count else 100.0
    if resolved_count <= 0:
        return {
            "success": False,
            "available": False,
            "error_code": "REFINEMENT_SCOPE_UNRESOLVABLE",
            "message": "None of the previous verified candidates are currently available to refine.",
            "retryable": False,
            "scope_summary": scope_summary,
        }
    return {
        "success": True,
        "available": True,
        "parent_search_session_id": str(parent_search_session_id or "").strip(),
        "search_context": {
            "rank_folder": rank_folder,
            "rank_folder_id": rank_record["rank_folder_id"],
            "download_root_id": rank_record["download_root_id"],
            "applied_ship_type": str(parent_session.get("applied_ship_type") or "").strip(),
            "experienced_ship_type": str(parent_session.get("experienced_ship_type") or "").strip(),
            "vessel_tonnage_filter": _normalize_vessel_tonnage_filter(
                (parent_session.get("context") or {}).get("vessel_tonnage_filter") or {}
            ),
        },
        "scope_summary": scope_summary,
        "refinement": {
            "available": True,
            "unavailable_reason": "",
            "retryable": False,
            "retry_after_seconds": None,
        },
        "_parent_scope": parent_scope,
        "_changed_members": changed_members,
    }


def _safe_recovery_scalar_mapping(value, allowed_keys):
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in allowed_keys:
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            result[key] = item
    return result


def _safe_recovery_search_context(value):
    result = _safe_recovery_scalar_mapping(
        value,
        ("rank_folder", "applied_ship_type", "experienced_ship_type"),
    )
    result["vessel_tonnage_filter"] = _normalize_vessel_tonnage_filter(
        (value if isinstance(value, dict) else {}).get("vessel_tonnage_filter") or {}
    )
    return result


def _sanitize_recovery_machine_codes(values, *, limit, max_length):
    values = values if isinstance(values, list) else []
    sanitized = []
    for value in values:
        if not isinstance(value, str):
            continue
        code = value.strip()[:max_length]
        if code and re.fullmatch(r"[A-Za-z0-9_.:-]+", code):
            sanitized.append(code)
        if len(sanitized) >= limit:
            break
    return sanitized


def _sanitize_recovery_confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        return None
    return confidence


def _sanitize_recovery_result_bucket(value, default_bucket):
    bucket = str(value or "").strip()
    if bucket in {"verified_match", "uncertain_match", "needs_review"}:
        return bucket
    return default_bucket


def _sanitize_recovery_step_index(value):
    try:
        index = int(value)
    except (TypeError, ValueError):
        return -1
    return max(-1, min(index, 9))


def _sanitize_recovery_result_card(card, *, default_bucket="verified_match"):
    card = card if isinstance(card, dict) else {}
    return {
        "schema_version": "recovery_result_card.v1",
        "candidate_scope_id": str(card.get("candidate_scope_id") or "")[:64],
        "content_hash": str(card.get("content_hash") or "").strip().lower()[:128],
        "filename": str(card.get("filename") or "")[:255],
        "result_bucket": _sanitize_recovery_result_bucket(card.get("result_bucket"), default_bucket),
        "confidence": _sanitize_recovery_confidence(card.get("confidence")),
        "lineage_warning_codes": _sanitize_recovery_machine_codes(
            card.get("lineage_warning_codes"),
            limit=10,
            max_length=64,
        ),
        "evidence_review_badges": _sanitize_recovery_machine_codes(
            card.get("evidence_review_badges"),
            limit=10,
            max_length=64,
        ),
        "detail_available_after_recovery": False,
    }


def _sanitize_recovery_results(results):
    results = results if isinstance(results, dict) else {}
    return {
        "schema_version": "recovery_results.v1",
        "verified_matches": [
            _sanitize_recovery_result_card(card, default_bucket="verified_match")
            for card in (results.get("verified_matches") or [])[:500]
        ],
        "uncertain_matches": [
            _sanitize_recovery_result_card(card, default_bucket="uncertain_match")
            for card in (results.get("uncertain_matches") or [])[:500]
        ],
        "unknown_matches": [
            _sanitize_recovery_result_card(card, default_bucket="needs_review")
            for card in (results.get("unknown_matches") or [])[:500]
        ],
        "hard_filter_summary": _safe_recovery_scalar_mapping(
            results.get("hard_filter_summary"),
            ("scanned", "passed", "failed", "unknown", "matched"),
        ),
        "search_session": _safe_recovery_scalar_mapping(
            results.get("search_session"),
            (
                "search_session_id", "root_search_session_id", "parent_search_session_id",
                "search_mode", "refinement_depth", "search_request_id",
            ),
        ),
        "search_context": _safe_recovery_search_context(results.get("search_context")),
        "scope_summary": _safe_recovery_scalar_mapping(
            results.get("scope_summary"),
            (
                "eligible_population_count", "retrieved_count", "evaluated_count",
                "requested_count", "resolved_count", "changed_content_count",
                "stale_count", "unresolvable_count", "duplicate_count",
            ),
        ),
        "refinement": _safe_recovery_scalar_mapping(
            results.get("refinement"),
            (
                "available", "search_session_id", "search_mode",
                "candidate_scope_member_count", "reason_code", "message",
                "membership_expires_at",
            ),
        ),
        "message": str(results.get("message") or "")[:512],
        "recovered_summary_only": True,
    }


def _sanitize_ai_search_recovery_draft(payload):
    payload = payload if isinstance(payload, dict) else {}
    search_state = payload.get("search_state") if isinstance(payload.get("search_state"), dict) else {}
    current_completed_results = search_state.get("current_completed_results")
    chain = []
    for step in (search_state.get("search_chain") or [])[-10:]:
        if not isinstance(step, dict) or not isinstance(step.get("results"), dict):
            continue
        chain.append({
            "prompt": str(step.get("prompt") or "")[:4000],
            "results": _sanitize_recovery_results(step.get("results")),
        })
    interrupted = search_state.get("interrupted_attempt")
    interrupted_attempt = None
    if isinstance(interrupted, dict):
        interrupted_attempt = _safe_recovery_scalar_mapping(
            interrupted,
            (
                "search_request_id", "prompt_summary", "parent_search_session_id",
                "attempted_at", "interruption_reason",
            ),
        )
    draft = {
        "schema_version": "ai_search_recovery.v1",
        "recovery_reason": str(payload.get("recovery_reason") or "checkpoint")[:64],
        "recovery_completeness": "full",
        "active_tab": str(payload.get("active_tab") or "search")[:64],
        "search_state": {
            "prompt": str(search_state.get("prompt") or "")[:4000],
            "selected_rank_folder": str(search_state.get("selected_rank_folder") or "")[:256],
            "applied_ship_type": str(search_state.get("applied_ship_type") or "")[:256],
            "experienced_ship_type": str(search_state.get("experienced_ship_type") or "")[:256],
            "vessel_tonnage_filter": _normalize_vessel_tonnage_filter(
                search_state.get("vessel_tonnage_filter") or {}
            ),
            "refinement_state": str(search_state.get("refinement_state") or "disabled")[:64],
            "active_search_step_index": _sanitize_recovery_step_index(
                search_state.get("active_search_step_index")
            ),
            "current_completed_results": (
                _sanitize_recovery_results(current_completed_results)
                if isinstance(current_completed_results, dict)
                else None
            ),
            "search_chain": chain,
            "refinement_availability": _safe_recovery_scalar_mapping(
                search_state.get("refinement_availability"),
                ("available", "candidateCount", "reason", "parentSearchSessionId"),
            ),
            "interrupted_attempt": interrupted_attempt,
        },
    }
    encoded = json.dumps(draft, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 4 * 1024 * 1024:
        draft["search_state"]["search_chain"] = []
        draft["recovery_completeness"] = "trimmed"
        encoded = json.dumps(draft, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 4 * 1024 * 1024:
        draft["search_state"]["current_completed_results"] = _sanitize_recovery_results({})
        draft["recovery_completeness"] = "context_only"
    return draft


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


def _require_admin_session():
    return _require_role("admin")


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
            (key, endpoint, datetime.now(UTC).isoformat().replace("+00:00", "Z")),
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
            "received_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "payload": payload or {},
        }) + "\n")


def _usage_log_path():
    logs_dir = _resolve_runtime_path(_advanced_value("log_dir", "logs"), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "usage_audit.jsonl")


def _log_usage(action, summary="", extra=None):
    try:
        row = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
    agent_settings = {}
    if _use_local_agent():
        try:
            resp = _agent_request("GET", "/settings", timeout=5)
            if getattr(resp, "status_code", 500) < 400:
                payload = resp.json()
                if isinstance(payload, dict) and isinstance(payload.get("settings"), dict):
                    agent_settings = payload.get("settings") or {}
        except Exception:
            agent_settings = {}

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
            "LLM_Promotion_Stage": config.get("Settings", "LLM_Promotion_Stage", fallback="0"),
            "supabase_url": _supabase_url(),
            "use_supabase_db": bool(feature_flags.use_supabase_db),
            "use_dual_write": bool(getattr(feature_flags, "use_dual_write", False)),
            "use_supabase_reads": bool(getattr(feature_flags, "use_supabase_reads", False)),
            "use_local_agent": bool(feature_flags.use_local_agent),
            "use_cloud_export": bool(feature_flags.use_cloud_export),
            "email_intake_enabled": _advanced_bool(
                "email_intake_enabled",
                bool(agent_settings.get("email_intake_enabled", False)),
            ),
            "email_intake_mailbox": _advanced_value(
                "email_intake_mailbox",
                str(agent_settings.get("email_intake_mailbox", "")).strip(),
            ),
            "email_intake_monitored_folder": _advanced_value(
                "email_intake_monitored_folder",
                str(agent_settings.get("email_intake_monitored_folder", "")).strip() or "Inbox/NjordHR Resumes",
            ),
            "email_intake_processed_folder": _advanced_value(
                "email_intake_processed_folder",
                str(agent_settings.get("email_intake_processed_folder", "")).strip() or "Inbox/NjordHR Processed",
            ),
            "email_intake_failed_folder": _advanced_value(
                "email_intake_failed_folder",
                str(agent_settings.get("email_intake_failed_folder", "")).strip() or "Inbox/NjordHR Failed",
            ),
            "email_intake_poll_interval_seconds": _int_setting(
                "Advanced",
                "email_intake_poll_interval_seconds",
                int(agent_settings.get("email_intake_poll_interval_seconds", 60) or 60),
            ),
            "outlook_client_id": _advanced_value(
                "outlook_client_id",
                str(agent_settings.get("outlook_client_id", "")).strip(),
            ),
            "outlook_tenant_id": _advanced_value(
                "outlook_tenant_id",
                str(agent_settings.get("outlook_tenant_id", "organizations")).strip() or "organizations",
            ) or "organizations",
        },
        "secrets": {
            "seajob_username": _mask_secret(_seajob_username()),
            "seajob_password": _mask_secret(_seajob_password()),
            "gemini_api_key": _mask_secret(_gemini_api_key()),
            "pinecone_api_key": _mask_secret(_pinecone_api_key()),
            "supabase_secret_key": _mask_secret(resolve_supabase_api_key()),
            "supabase_service_role_key": _mask_secret(_supabase_service_role_key()),
        }
    }
    if include_plain_secrets:
        payload["secrets_plain"] = {
            "seajob_username": _seajob_username(),
            "seajob_password": _seajob_password(),
            "gemini_api_key": _gemini_api_key(),
            "pinecone_api_key": _pinecone_api_key(),
            "supabase_secret_key": resolve_supabase_api_key(),
            "supabase_service_role_key": _supabase_service_role_key(),
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
    explicit = os.getenv("NJORDHR_AGENT_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    url = os.getenv("NJORDHR_AGENT_URL", "").strip()
    if url:
        return url.rstrip("/")
    port = os.getenv("NJORDHR_AGENT_RUNTIME_PORT", "").strip() or os.getenv("NJORDHR_AGENT_PORT", "").strip()
    if port.isdigit():
        return f"http://127.0.0.1:{port}"
    return "http://127.0.0.1:5051"


def _use_local_agent():
    return bool(getattr(feature_flags, "use_local_agent", False))


def _agent_request(method, path, *, json_body=None, params=None, stream=False, timeout=30):
    base = _local_agent_base_url()
    url = f"{base}{path}"
    headers = {}
    token = _agent_sync_token()
    if token:
        headers["X-Device-Token"] = token
    return requests.request(
        method=method,
        url=url,
        json=json_body,
        params=params,
        headers=headers or None,
        stream=stream,
        timeout=timeout,
    )


def _translate_agent_stream_event(event, default_error_message="Download failed"):
    ev_type = str((event or {}).get("type", "")).strip()
    if ev_type == "log":
        return {"type": "log", "line": (event or {}).get("message", "")}
    if ev_type in {"queued", "running", "progress"}:
        event_data = (event or {}).get("data") or {}
        payload = {
            "type": "progress",
            "stage": str(event_data.get("stage", ev_type)),
            "message": (event or {}).get("message", ""),
        }
        if "percent" in event_data:
            payload["percent"] = event_data["percent"]
        if event_data:
            payload["data"] = event_data
        return payload
    if ev_type == "complete":
        result = ((event or {}).get("data") or {}).get("result", {}) or {}
        if result.get("success"):
            return {"type": "complete", **result}
        return {"type": "error", "message": result.get("message", default_error_message)}
    if ev_type == "error":
        return {"type": "error", "message": (event or {}).get("message", default_error_message)}
    return None


def _agent_health_summary():
    base = _local_agent_base_url()
    try:
        resp = requests.get(f"{base}/health", timeout=3)
        if resp.status_code >= 400:
            return {"configured": True, "reachable": False, "base_url": base, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        sync = data.get("sync") if isinstance(data, dict) else {}
        last_resume_upload = sync.get("last_resume_upload") if isinstance(sync, dict) else None
        return {
            "configured": True,
            "reachable": True,
            "base_url": base,
            "last_resume_upload": last_resume_upload,
            "health": data,
        }
    except Exception as exc:
        return {"configured": True, "reachable": False, "base_url": base, "error": str(exc)}


def _build_runtime_resume_url(rank_applied_for, filename):
    return _build_runtime_resume_url_from_stored(rank_applied_for, filename, "")


def _storage_bucket_name():
    return normalize_env_value(os.getenv("SUPABASE_RESUME_BUCKET", "resumes")) or "resumes"


def _safe_storage_segment(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _sha256_bytes(data_bytes):
    h = hashlib.sha256()
    h.update(data_bytes or b"")
    return h.hexdigest()


def _supabase_storage_upload(file_bytes, object_path, content_type="application/pdf"):
    supabase_url = _supabase_url()
    supabase_key = resolve_supabase_api_key()
    if not supabase_url or not supabase_key:
        return None, "Supabase credentials not configured"

    bucket = _storage_bucket_name()
    endpoint = f"{supabase_url}/storage/v1/object/{bucket}/{object_path}"
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

    supabase_url = _supabase_url()
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

    endpoint = f"{supabase_url}/storage/v1/object/sign/{bucket}/{object_path}"
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

    source_base_dir = _active_download_root()
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


@app.route('/ui_vendor/<path:filename>')
def serve_ui_vendor_asset(filename):
    """Serve vendored local UI runtime assets (React, ReactDOM, Babel)."""
    allowed_ext = {'.js', '.map'}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_ext:
        return "Invalid vendor asset request.", 400

    vendor_root = Path("web_vendor").resolve()
    requested_path = (vendor_root / filename).resolve()
    try:
        requested_path.relative_to(vendor_root)
    except ValueError:
        return "Invalid vendor asset request.", 400

    if not requested_path.is_file():
        return "Vendor asset not found.", 404

    return send_from_directory(str(vendor_root), filename)


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
    session["user_id"] = f"local:legacy:{hashlib.sha256(admin_username.strip().lower().encode('utf-8', 'ignore')).hexdigest()[:32]}"
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
    if record.get("id"):
        session["user_id"] = f"cloud:{record.get('id')}"
    else:
        session["user_id"] = f"local:legacy:{hashlib.sha256(username.strip().lower().encode('utf-8', 'ignore')).hexdigest()[:32]}"
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
    try:
        search_scope_repo.delete_recovery_drafts(actor_user_id=_session_actor_user_id())
    except Exception:
        pass
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
                        translated = _translate_agent_stream_event(event)
                        if not translated:
                            continue
                        yield f"data: {json.dumps(translated)}\n\n"
                        if translated.get("type") in {"complete", "error"}:
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


@app.route('/outlook_fetch_stream', methods=['GET'])
def outlook_fetch_stream():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        def denied():
            yield f"data: {json.dumps({'type': 'error', 'message': reason})}\n\n"
        return Response(denied(), mimetype='text/event-stream')

    if not _use_local_agent():
        def no_agent():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Local agent is required for Outlook intake.'})}\n\n"
        return Response(no_agent(), mimetype='text/event-stream')

    def generate_agent():
        try:
            create_resp = _agent_request("POST", "/email-intake/fetch", json_body={}, timeout=60)
            create_payload = create_resp.json()
            if not create_payload.get("success"):
                msg = create_payload.get("message", "Failed to start Outlook fetch job")
                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                return
            job_id = str(create_payload.get("job_id", "")).strip()
            if not job_id:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Local agent did not return job_id'})}\n\n"
                return
            yield f"data: {json.dumps({'type': 'started', 'message': 'Outlook fetch started.', 'job_id': job_id})}\n\n"

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
                    translated = _translate_agent_stream_event(event, default_error_message="Outlook fetch failed")
                    if not translated:
                        continue
                    yield f"data: {json.dumps(translated)}\n\n"
                    if translated.get("type") in {"complete", "error"}:
                        return
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Local agent unavailable: {exc}'})}\n\n"

    return Response(generate_agent(), mimetype='text/event-stream')


@app.route('/email-intake/manual-review/summary', methods=['GET'])
def proxy_email_intake_manual_review_summary():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for manual review."}), 400
    try:
        resp = _agent_request("GET", "/email-intake/manual-review/summary", timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/email-intake/manual-review/items', methods=['GET'])
def proxy_email_intake_manual_review_items():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for manual review."}), 400
    try:
        resp = _agent_request("GET", "/email-intake/manual-review/items", timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/email-intake/manual-review/item', methods=['GET'])
def proxy_email_intake_manual_review_item():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for manual review."}), 400
    item_id = str(request.args.get("id", "")).strip()
    if not item_id:
        return jsonify({"success": False, "message": "id is required"}), 400
    try:
        resp = _agent_request("GET", "/email-intake/manual-review/item", params={"id": item_id}, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/email-intake/manual-review/move', methods=['POST'])
def proxy_email_intake_manual_review_move():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for manual review."}), 400
    payload = request.json or {}
    try:
        resp = _agent_request("POST", "/email-intake/manual-review/move", json_body=payload, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/email-intake/manual-review/open', methods=['POST'])
def proxy_email_intake_manual_review_open():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for manual review."}), 400
    payload = request.json or {}
    try:
        resp = _agent_request("POST", "/email-intake/manual-review/open", json_body=payload, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/candidate-facts/review/capture', methods=['POST'])
def capture_candidate_facts_review_item():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    payload = request.json or {}
    candidate_resume_id = str(payload.get("candidate_resume_id", "")).strip()
    resume_blob_id = str(payload.get("resume_blob_id", "")).strip()
    parser_version = str(payload.get("parser_version", "")).strip()
    facts_revision = str(payload.get("facts_revision", "")).strip()
    candidate_facts = payload.get("candidate_facts") or {}
    if not candidate_resume_id:
        return jsonify({"success": False, "message": "candidate_resume_id is required"}), 400
    if not resume_blob_id:
        return jsonify({"success": False, "message": "resume_blob_id is required"}), 400
    if not parser_version:
        return jsonify({"success": False, "message": "parser_version is required"}), 400
    if not facts_revision:
        return jsonify({"success": False, "message": "facts_revision is required"}), 400
    if not isinstance(candidate_facts, dict) or not candidate_facts:
        return jsonify({"success": False, "message": "candidate_facts is required"}), 400
    try:
        repo = _candidate_facts_repository()
        item = repo.capture_normalized_candidate_facts_for_review(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
        )
        return jsonify({"success": True, "review_item": item["review_item"], "candidate_facts": item["candidate_facts"]})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts capture failed: {exc}"}), 500


@app.route('/candidate-facts/review/items', methods=['GET'])
def list_candidate_facts_review_items():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    review_status = str(request.args.get("status", "")).strip() or None
    try:
        repo = _candidate_facts_repository()
        return jsonify({"success": True, "items": repo.list_candidate_facts_review_summaries(review_status=review_status)})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts review items unavailable: {exc}"}), 500


@app.route('/candidate-facts/review/item', methods=['GET'])
def get_candidate_facts_review_item():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    item_id = str(request.args.get("id", "")).strip()
    if not item_id:
        return jsonify({"success": False, "message": "id is required"}), 400
    try:
        repo = _candidate_facts_repository()
        item = repo.validation_cache.get_review_item(item_id) if repo.validation_cache else None
        if item is None:
            return jsonify({"success": False, "message": "Review item not found"}), 404
        return jsonify({"success": True, "item": item})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts review item unavailable: {exc}"}), 500


@app.route('/candidate-facts/review/approve', methods=['POST'])
def approve_candidate_facts_review_item():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    payload = request.json or {}
    item_id = str(payload.get("id", "")).strip()
    if not item_id:
        return jsonify({"success": False, "message": "id is required"}), 400
    try:
        repo = _candidate_facts_repository()
        item = repo.approve_candidate_facts_review_item(
            item_id,
            reviewed_by=str(payload.get("reviewed_by", "")).strip(),
            review_notes=str(payload.get("review_notes", "")).strip(),
        )
        return jsonify({"success": True, "review_item": item})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts approval failed: {exc}"}), 500


@app.route('/candidate-facts/review/reject', methods=['POST'])
def reject_candidate_facts_review_item():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    payload = request.json or {}
    item_id = str(payload.get("id", "")).strip()
    if not item_id:
        return jsonify({"success": False, "message": "id is required"}), 400
    try:
        repo = _candidate_facts_repository()
        item = repo.reject_candidate_facts_review_item(
            item_id,
            reviewed_by=str(payload.get("reviewed_by", "")).strip(),
            review_notes=str(payload.get("review_notes", "")).strip(),
        )
        return jsonify({"success": True, "review_item": item})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts rejection failed: {exc}"}), 500


@app.route('/candidate-facts/review/promote', methods=['POST'])
def promote_candidate_facts_review_item():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    payload = request.json or {}
    item_id = str(payload.get("id", "")).strip()
    if not item_id:
        return jsonify({"success": False, "message": "id is required"}), 400
    try:
        repo = _candidate_facts_repository()
        result = repo.promote_candidate_facts_review_item(
            item_id,
        )
        committed = bool(result.get("persist", {}).get("committed"))
        if not committed:
            return jsonify({
                "success": False,
                "message": "Candidate facts were written but not marked current for this resume. Nothing was promoted to authoritative state.",
                "review_item": result["review_item"],
                "persist": result["persist"],
            }), 409
        response = {
            "success": True,
            "review_item": result["review_item"],
            "persist": result["persist"],
            "supabase": result.get("supabase") or {},
            "supabase_synced": bool(result.get("supabase_synced", False)),
        }
        warnings = [warning for warning in (result.get("warnings") or []) if warning]
        review_item = result.get("review_item") or {}
        supabase_status = str(review_item.get("supabase_persistence_status") or "").strip()
        supabase_error = str(review_item.get("supabase_error") or "").strip()
        if not warnings:
            if supabase_status == "failed":
                warnings.append(f"Supabase candidate facts sync failed: {supabase_error or 'unknown error'}")
            elif supabase_status in {"not_current", "skipped_non_current"}:
                warnings.append("Supabase candidate facts row was written but not marked current.")
        if warnings:
            response["warnings"] = warnings
        return jsonify(response)
    except ValueError as exc:
        try:
            review_item = repo.validation_cache.get_review_item(item_id) if getattr(repo, "validation_cache", None) else None
        except Exception:
            review_item = None
        return jsonify({
            "success": False,
            "message": str(exc),
            "review_item": review_item,
        }), 409
    except Exception as exc:
        return jsonify({"success": False, "message": f"Candidate facts promotion failed: {exc}"}), 500


@app.route('/email-intake/auth/start', methods=['POST'])
def proxy_email_intake_auth_start():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for Outlook mailbox connection."}), 400
    payload = request.json or {}
    try:
        status_resp = _agent_request("GET", "/email-intake/auth/status", timeout=15)
        status_payload = status_resp.json() if getattr(status_resp, "status_code", 500) < 400 else {}
        auth_status = status_payload.get("auth") if isinstance(status_payload, dict) else {}
        missing = []
        if not str((auth_status or {}).get("mailbox", "")).strip():
            missing.append("Mailbox")
        if not (auth_status or {}).get("client_id_present"):
            missing.append("Outlook Client ID")
        if missing:
            return jsonify({
                "success": False,
                "message": (
                    f"{' and '.join(missing)} missing. Add it in Settings > Operational Settings > "
                    "Mailbox Intake, save settings, then connect the mailbox."
                ),
            }), 400
        resp = _agent_request("POST", "/email-intake/auth/start", json_body=payload, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502


@app.route('/email-intake/auth/disconnect', methods=['POST'])
def proxy_email_intake_auth_disconnect():
    ok, reason = _require_role("admin", "manager")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    if not _use_local_agent():
        return jsonify({"success": False, "message": "Local agent is required for Outlook mailbox disconnection."}), 400
    try:
        resp = _agent_request("POST", "/email-intake/auth/disconnect", json_body={}, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as exc:
        return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502

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
    bootstrap = _bootstrap_status()
    auth_backend = _cloud_auth_state(force_refresh=False)
    if not _is_authenticated():
        return jsonify({
            "success": True,
            "feature_flags": {
                "use_supabase_db": bool(feature_flags.use_supabase_db),
                "use_local_agent": bool(feature_flags.use_local_agent),
            },
            "auth_backend": auth_backend,
            "bootstrap": {
                "required": bool(bootstrap.get("bootstrap_required")),
                "completed": bool(bootstrap.get("bootstrap_completed")),
                "reason": bootstrap.get("reason", ""),
                "valid_user_count": int(bootstrap.get("valid_user_count", 0) or 0),
                "auth_mode": bootstrap.get("auth_mode", _auth_mode()),
            },
            "cloud_api": _cloud_api_runtime_summary(),
            "local_agent": _agent_health_summary() if _use_local_agent() else {
                "configured": False,
                "reachable": False,
                "base_url": _local_agent_base_url(),
            },
            "ui_auto_shutdown": {
                "enabled": _ui_idle_autoshutdown_enabled(),
                "idle_seconds": _ui_idle_shutdown_seconds(),
                "active_clients": _active_ui_client_count(),
            },
        })

    key_source = "none"
    if normalize_env_value(os.getenv("SUPABASE_SECRET_KEY", "")):
        key_source = "secret"
    elif normalize_env_value(os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")):
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
        "process_identity": {
            "project_dir": os.path.abspath(os.getcwd()),
            "config_path": os.path.abspath(os.getenv("NJORDHR_CONFIG_PATH", "config.ini")),
            "runtime_dir": os.path.abspath(os.getenv("NJORDHR_RUNTIME_DIR", "")) if os.getenv("NJORDHR_RUNTIME_DIR", "").strip() else "",
        },
        "ui_auto_shutdown": {
            "enabled": _ui_idle_autoshutdown_enabled(),
            "idle_seconds": _ui_idle_shutdown_seconds(),
            "active_clients": _active_ui_client_count(),
        },
        "verified_resumes_dir": VERIFIED_RESUMES_DIR,
        "supabase_auth": {
            "key_source": key_source,
            "key_hint": key_hint,
            "url_configured": bool(_supabase_url()),
        },
        "agent_ingest_auth": {
            "required": bool(_agent_sync_token()),
        },
        "admin_settings_enabled": bool(_admin_token()),
        "auth_backend": auth_backend,
        "bootstrap": {
            "required": bool(bootstrap.get("bootstrap_required")),
            "completed": bool(bootstrap.get("bootstrap_completed")),
            "reason": bootstrap.get("reason", ""),
            "valid_user_count": int(bootstrap.get("valid_user_count", 0) or 0),
            "auth_mode": bootstrap.get("auth_mode", _auth_mode()),
        },
        "cloud_api": _cloud_api_runtime_summary(),
        "local_agent": _agent_health_summary() if _use_local_agent() else {
            "configured": False,
            "reachable": False,
            "base_url": _local_agent_base_url(),
        },
    })


@app.route('/runtime/ready', methods=['GET'])
def runtime_ready():
    """Local unauthenticated readiness + identity endpoint for desktop shell startup."""
    if not _is_local_request():
        return jsonify({"success": False, "error": "Forbidden"}), 403
    backend_port = int(os.getenv("NJORDHR_PORT", "5000"))
    agent_port_raw = os.getenv("NJORDHR_AGENT_RUNTIME_PORT", "").strip() or os.getenv("NJORDHR_AGENT_PORT", "").strip()
    auth_pref = _auth_mode_preference()
    if bool(getattr(feature_flags, "use_supabase_db", False)) or auth_pref == "cloud":
        auth_mode = "cloud"
    elif auth_pref == "local":
        auth_mode = "local"
    else:
        auth_mode = "local"
    payload = {
        "success": True,
        "backend_ready": True,
        "process_identity": {
            "project_dir": os.path.abspath(os.getcwd()),
            "config_path": os.path.abspath(os.getenv("NJORDHR_CONFIG_PATH", "config.ini")),
            "runtime_dir": os.path.abspath(os.getenv("NJORDHR_RUNTIME_DIR", "")) if os.getenv("NJORDHR_RUNTIME_DIR", "").strip() else "",
        },
        "ports": {
            "backend_port": backend_port,
            "agent_port": int(agent_port_raw) if agent_port_raw.isdigit() else None,
        },
        "runtime": {
            "feature_flags": {
                "use_supabase_db": bool(feature_flags.use_supabase_db),
                "use_dual_write": bool(getattr(feature_flags, "use_dual_write", False)),
                "use_supabase_reads": bool(getattr(feature_flags, "use_supabase_reads", False)),
                "use_local_agent": bool(feature_flags.use_local_agent),
                "use_cloud_export": bool(feature_flags.use_cloud_export),
            },
            # Keep /runtime/ready fast and local-only. Heavy cloud/bootstrap checks
            # belong to the authenticated runtime/bootstrap endpoints, not the shell
            # readiness handshake used during startup.
            "auth_mode": auth_mode,
            "bootstrap_required": False,
            "bootstrap_reason": "startup_ready",
        },
        "cloud_api": _cloud_api_runtime_summary(),
        "version": {
            "backend": "python-backend",
        },
    }
    return jsonify(payload)


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
            "macos_full": "cd ./electron && npm run dist:mac",
            "macos_install_app": "./scripts/packaging/macos/install_app.sh",
            "windows_full": "cd .\\electron; npm run dist:win",
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

    checksum = _sha256_bytes(content)
    storage_url, error = _supabase_storage_upload(content, object_path)
    _append_agent_sync_jsonl("resume_upload", {
        "filename": filename,
        "metadata": metadata,
        "resume_source": "cloud_synced" if storage_url else "local_only",
        "resume_upload_status": "uploaded" if storage_url else "failed",
        "resume_storage_path": storage_url or "",
        "resume_checksum_sha256": checksum,
        "resume_uploaded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "object_path": object_path,
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
            "resume_source": "local_only",
            "resume_upload_status": "failed",
            "resume_checksum_sha256": checksum,
        }), 500
    return jsonify({
        "success": True,
        "upload_status": "uploaded",
        "resume_storage_path": storage_url,
        "resume_source": "cloud_synced",
        "resume_upload_status": "uploaded",
        "resume_checksum_sha256": checksum,
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
    supabase_url = normalized_url(data.get("supabase_url", "") or _supabase_url())
    supabase_secret_key = str(
        data.get("supabase_secret_key", "")
        or data.get("supabase_service_role_key", "")
        or resolve_supabase_api_key()
    )
    supabase_secret_key = normalize_env_value(supabase_secret_key)
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
    # Capture the current cloud connection details before mutating config so a
    # mid-save Supabase write still targets the existing runtime endpoint/key.
    current_supabase_url = _supabase_url()
    current_supabase_key = resolve_supabase_api_key()
    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            original_config_text = fh.read()
    except OSError:
        original_config_text = None
    env_snapshot = {
        name: os.environ.get(name)
        for name in [
            "SEAJOB_USERNAME",
            "SEAJOB_PASSWORD",
            "GEMINI_API_KEY",
            "PINECONE_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "USE_SUPABASE_DB",
            "USE_DUAL_WRITE",
            "USE_SUPABASE_READS",
            "USE_LOCAL_AGENT",
            "USE_CLOUD_EXPORT",
        ]
    }

    if "Credentials" not in config:
        config["Credentials"] = {}
    if "Settings" not in config:
        config["Settings"] = {}
    if "Advanced" not in config:
        config["Advanced"] = {}

    def _set_if_present(section, key, payload_key):
        if payload_key in payload:
            value = normalize_env_value(payload.get(payload_key, ""))
            _set_config_value_or_clear(section, key, value)
            return value
        return None

    def _restore_state():
        global app_settings, config, creds, settings
        if original_config_text is not None:
            with open(config_path, "w", encoding="utf-8") as fh:
                fh.write(original_config_text)
        for env_name, prev_value in env_snapshot.items():
            if prev_value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = prev_value
        restored_settings = load_app_settings()
        app_settings = restored_settings
        config = restored_settings.config
        creds = restored_settings.credentials
        settings = restored_settings.settings
        try:
            _refresh_runtime_managers()
        except Exception:
            pass

    # Credentials are applied to the running process immediately and also persisted
    # for future manual terminal starts via config.ini.
    sensitive_pairs = {}
    if "seajob_username" in payload:
        val = normalize_env_value(payload.get("seajob_username", ""))
        _set_config_value_or_clear("Credentials", "Username", val)
        _set_env_value_or_clear("SEAJOB_USERNAME", val)
        sensitive_pairs["seajob_username"] = val
    if "seajob_password" in payload:
        val = normalize_env_value(payload.get("seajob_password", ""))
        _set_config_value_or_clear("Credentials", "Password", val)
        _set_env_value_or_clear("SEAJOB_PASSWORD", val)
        sensitive_pairs["seajob_password"] = val
    if "gemini_api_key" in payload:
        val = normalize_env_value(payload.get("gemini_api_key", ""))
        _set_config_value_or_clear("Credentials", "Gemini_API_Key", val)
        _set_env_value_or_clear("GEMINI_API_KEY", val)
        sensitive_pairs["gemini_api_key"] = val
    if "pinecone_api_key" in payload:
        val = normalize_env_value(payload.get("pinecone_api_key", ""))
        _set_config_value_or_clear("Credentials", "Pinecone_API_Key", val)
        _set_env_value_or_clear("PINECONE_API_KEY", val)
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

    if "LLM_Promotion_Stage" in payload:
        try:
            llm_stage = int(str(payload.get("LLM_Promotion_Stage", "")).strip())
        except Exception:
            return jsonify({"success": False, "message": "LLM_Promotion_Stage must be an integer between 0 and 5"}), 400
        if llm_stage < 0 or llm_stage > 5:
            return jsonify({"success": False, "message": "LLM_Promotion_Stage must be an integer between 0 and 5"}), 400
        _config_set_literal(config, "Settings", "LLM_Promotion_Stage", str(llm_stage))

    if "otp_window_seconds" in payload:
        try:
            otp_seconds = int(str(payload.get("otp_window_seconds", "")).strip())
            if otp_seconds < 30 or otp_seconds > 900:
                raise ValueError("out of range")
        except Exception:
            return jsonify({"success": False, "message": "otp_window_seconds must be an integer between 30 and 900"}), 400
        _config_set_literal(config, "Advanced", "otp_window_seconds", str(otp_seconds))

    intended_use_local_agent = _payload_bool(payload, "use_local_agent", feature_flags.use_local_agent)
    requested_download_folder = normalize_env_value(payload.get("default_download_folder", "")) if "default_download_folder" in payload else ""
    pending_agent_download_folder = requested_download_folder or ""

    email_intake_payload = {}
    if "email_intake_enabled" in payload:
        enabled = _payload_bool(payload, "email_intake_enabled")
        _set_config_value_or_clear("Advanced", "email_intake_enabled", "true" if enabled else "false")
        email_intake_payload["email_intake_enabled"] = enabled
    for key in [
        "email_intake_mailbox",
        "email_intake_monitored_folder",
        "email_intake_processed_folder",
        "email_intake_failed_folder",
        "outlook_client_id",
        "outlook_tenant_id",
    ]:
        if key in payload:
            value = normalize_env_value(payload.get(key, ""))
            if not value:
                continue
            _set_config_value_or_clear("Advanced", key, value)
            email_intake_payload[key] = value
    if "email_intake_poll_interval_seconds" in payload:
        raw_interval = str(payload.get("email_intake_poll_interval_seconds", "")).strip()
        if raw_interval:
            try:
                poll_interval = int(raw_interval)
                _config_set_literal(config, "Advanced", "email_intake_poll_interval_seconds", str(poll_interval))
                email_intake_payload["email_intake_poll_interval_seconds"] = poll_interval
            except Exception:
                return jsonify({"success": False, "message": "email_intake_poll_interval_seconds must be an integer"}), 400
    if email_intake_payload:
        try:
            if intended_use_local_agent:
                resp = _agent_request("PUT", "/settings", json_body=email_intake_payload, timeout=15)
                if getattr(resp, "status_code", 500) >= 400:
                    try:
                        message = resp.json().get("message", "Local agent rejected Outlook mailbox intake settings.")
                    except Exception:
                        message = "Local agent rejected Outlook mailbox intake settings."
                    return jsonify({"success": False, "message": message}), resp.status_code
        except Exception as exc:
            return jsonify({"success": False, "message": f"Local agent unavailable: {exc}"}), 502

    if "supabase_url" in payload:
        supabase_url = normalized_url(payload.get("supabase_url", ""))
        _set_config_value_or_clear("Advanced", "supabase_url", supabase_url)
        _set_env_value_or_clear("SUPABASE_URL", supabase_url)
        sensitive_pairs["supabase_url"] = supabase_url
    if "supabase_secret_key" in payload:
        sup_key = normalize_env_value(payload.get("supabase_secret_key", ""))
        _set_config_value_or_clear("Credentials", "Supabase_Secret_Key", sup_key)
        _set_env_value_or_clear("SUPABASE_SECRET_KEY", sup_key)
        if sup_key:
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        else:
            os.environ.pop("SUPABASE_SECRET_KEY", None)
        sensitive_pairs["supabase_secret_key"] = sup_key
    if "supabase_service_role_key" in payload:
        legacy_key = normalize_env_value(payload.get("supabase_service_role_key", ""))
        _set_config_value_or_clear("Credentials", "Supabase_Service_Role_Key", legacy_key)
        _set_env_value_or_clear("SUPABASE_SERVICE_ROLE_KEY", legacy_key)
        sensitive_pairs["supabase_service_role_key"] = legacy_key

    # Persist sensitive runtime config centrally in Supabase when available.
    if sensitive_pairs and bool(getattr(feature_flags, "use_supabase_db", False)):
        try:
            _supabase_runtime_config_set(
                sensitive_pairs,
                supabase_url=current_supabase_url,
                supabase_key=current_supabase_key,
            )
        except Exception as exc:
            _restore_state()
            return jsonify({"success": False, "message": f"Failed to save cloud secrets: {exc}"}), 400

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
            coerced = _payload_bool(payload, flag_name)
            _config_set_literal(config, "Advanced", env_name.lower(), "true" if coerced else "false")
            os.environ[env_name] = "true" if coerced else "false"

    try:
        with open(config_path, "w", encoding="utf-8") as fh:
            config.write(fh)
        app_settings = load_app_settings()
        config = app_settings.config
        creds = app_settings.credentials
        settings = app_settings.settings
        _refresh_runtime_managers()
    except Exception as exc:
        _restore_state()
        return jsonify({"success": False, "message": str(exc)}), 400

    warnings = []
    if pending_agent_download_folder and intended_use_local_agent:
        try:
            resp = _agent_request(
                "PUT",
                "/settings/download-folder",
                json_body={"download_folder": pending_agent_download_folder},
                timeout=15,
            )
            if getattr(resp, "status_code", 500) >= 400:
                try:
                    message = resp.json().get("message", "Local agent rejected download folder setting.")
                except Exception:
                    message = "Local agent rejected download folder setting."
                warnings.append(message)
        except Exception as exc:
            warnings.append(f"Local agent unavailable: {exc}")

    if email_intake_payload and not intended_use_local_agent:
        warnings.append("Mailbox intake settings were saved locally; enable Local Agent to sync them.")

    response = {
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
    }
    if warnings:
        response["warnings"] = warnings
        response["message"] = "Admin settings saved and applied, but local agent sync needs attention."
    return jsonify(response)


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

    if _is_windows():
        include_drive_entries = not requested_path or _is_windows_drive_root(target_path)
        if include_drive_entries:
            existing_paths = {item["path"] for item in entries}
            for drive_entry in _list_windows_drive_entries():
                if drive_entry["path"] in existing_paths:
                    continue
                entries.append(drive_entry)

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
    global app_settings, config, creds, settings
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

    config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            original_config_text = fh.read()
    except OSError:
        original_config_text = None

    if "Advanced" not in config:
        config["Advanced"] = {}
    _config_set_literal(config, "Advanced", "admin_token", new_password)

    try:
        with open(config_path, "w", encoding="utf-8") as fh:
            config.write(fh)

        app_settings = load_app_settings()
        config = app_settings.config
        creds = app_settings.credentials
        settings = app_settings.settings
        _refresh_runtime_managers()
    except Exception as exc:
        if original_config_text is not None:
            with open(config_path, "w", encoding="utf-8") as fh:
                fh.write(original_config_text)
        app_settings = load_app_settings()
        config = app_settings.config
        creds = app_settings.credentials
        settings = app_settings.settings
        try:
            _refresh_runtime_managers()
        except Exception:
            pass
        return jsonify({"success": False, "message": str(exc)}), 400

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


@app.route('/admin/telemetry_logs', methods=['GET'])
def admin_telemetry_logs():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        limit = int(str(request.args.get("limit", "200")).strip())
    except Exception:
        limit = 200
    limit = max(1, min(limit, 1000))
    telemetry_kind = str(request.args.get("telemetry_kind", "")).strip() or None
    category = str(request.args.get("category", "")).strip() or None
    store = _supabase_telemetry_store()
    if store is None:
        return jsonify({"success": True, "rows": [], "count": 0, "message": "Telemetry store not configured."})
    try:
        rows = store.list_events(limit=limit, telemetry_kind=telemetry_kind, category=category)
        return jsonify({"success": True, "rows": rows, "count": len(rows), "table_name": store.table_name})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Telemetry logs unavailable: {exc}"}), 500


@app.route('/admin/telemetry_summary', methods=['GET'])
def admin_telemetry_summary():
    ok, reason = _require_admin_session()
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        limit = int(str(request.args.get("limit", "100")).strip())
    except Exception:
        limit = 100
    limit = max(1, min(limit, 1000))
    try:
        threshold = int(str(request.args.get("threshold", "10")).strip())
    except Exception:
        threshold = 10
    threshold = max(1, threshold)
    store = _supabase_telemetry_store()
    if store is None:
        return jsonify({
            "success": True,
            "summary": {
                "prompt_audit_count": 0,
                "prompt_audit_issue_count": 0,
                "prompt_audit_ok_count": 0,
                "prompt_audit_disabled_count": 0,
                "prompt_audit_hash_count": 0,
                "prompt_audit_threshold": threshold,
                "prompt_audit_over_threshold_count": 0,
                "prompt_audit_over_threshold_is_partial": False,
                "system_error_count": 0,
            },
            "prompt_audit_summaries": [],
            "recent_system_errors": [],
            "message": "Telemetry store not configured.",
        })
    try:
        all_prompt_audit_summaries = _list_all_prompt_audit_summaries(store, page_size=1000)
        prompt_audit_summaries = all_prompt_audit_summaries[:limit]
        prompt_audit_totals = store.get_prompt_audit_totals()
        recent_system_errors = store.list_events(
            limit=limit,
            telemetry_kind="system_log",
            status="error",
        )
        prompt_audit_count = int(prompt_audit_totals.get("total_count") or 0)
        prompt_audit_issue_count = int(prompt_audit_totals.get("issue_count") or 0)
        prompt_audit_ok_count = int(prompt_audit_totals.get("ok_count") or 0)
        prompt_audit_disabled_count = int(prompt_audit_totals.get("disabled_count") or 0)
        prompt_hash_count = int(prompt_audit_totals.get("prompt_hash_count") or 0)
        over_threshold = [
            row for row in all_prompt_audit_summaries
            if int(row.get("total_count") or 0) >= threshold
        ]
        summary_is_partial = len(all_prompt_audit_summaries) > len(prompt_audit_summaries)
        summary = {
            "prompt_audit_count": prompt_audit_count,
            "prompt_audit_issue_count": prompt_audit_issue_count,
            "prompt_audit_ok_count": prompt_audit_ok_count,
            "prompt_audit_disabled_count": prompt_audit_disabled_count,
            "prompt_audit_hash_count": prompt_hash_count,
            "prompt_audit_threshold": threshold,
            "prompt_audit_over_threshold_count": len(over_threshold),
            "prompt_audit_over_threshold_is_partial": summary_is_partial,
            "system_error_count": len(recent_system_errors),
        }
        return jsonify({
            "success": True,
            "summary": summary,
            "prompt_audit_summaries": prompt_audit_summaries,
            "prompt_audit_over_threshold": over_threshold,
            "recent_system_errors": recent_system_errors,
            "prompt_audit_totals": prompt_audit_totals,
            "table_name": getattr(store, "table_name", "njordhr_telemetry_logs"),
        })
    except Exception as exc:
        return jsonify({"success": False, "message": f"Telemetry summary unavailable: {exc}"}), 500

@app.route('/get_rank_folders', methods=['GET'])
def get_rank_folders():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    base_folder = _active_download_root()
    if not os.path.isdir(base_folder):
        return jsonify({"success": False, "folders": [], "message": "Download folder not found."})

    try:
        records = _rank_folder_catalog(base_folder)
        subfolders = [record["folder"] for record in records]
        root_record = _download_root_catalog_record(base_folder) or {}
        return jsonify({
            "success": True,
            "folders": subfolders,
            "rank_folder_options": [_public_rank_folder_record(record) for record in records],
            "download_root": {
                "download_root_id": root_record.get("download_root_id", ""),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "folders": [], "message": str(e)})


@app.route('/get_rank_folder_summaries', methods=['GET'])
def get_rank_folder_summaries():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    base_folder = _active_download_root()
    if not os.path.isdir(base_folder):
        return jsonify({"success": False, "folders": [], "message": "Download folder not found."})

    try:
        summaries = []
        for record in _rank_folder_catalog(base_folder):
            folder = record["folder"]
            folder_path = record["_resolved_path"]
            pdf_count = len([name for name in os.listdir(folder_path) if name.lower().endswith('.pdf')])
            summaries.append({
                "folder": folder,
                "rank_folder_id": record["rank_folder_id"],
                "download_root_id": record["download_root_id"],
                "display_name": record["display_name"],
                "pdf_count": pdf_count,
            })
        return jsonify({"success": True, "folders": summaries})
    except Exception as e:
        return jsonify({"success": False, "folders": [], "message": str(e)})


@app.route('/get_rank_options', methods=['GET'])
def get_rank_options():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        base_folder = _active_download_root()
        live_records = _rank_folder_catalog(base_folder) if os.path.isdir(base_folder) else []
        if live_records:
            return jsonify({
                "success": True,
                "ranks": [record["folder"] for record in live_records],
                "rank_folder_options": [_public_rank_folder_record(record) for record in live_records],
                "source": "active_download_root",
            })
        return jsonify({"success": True, "ranks": _configured_rank_options(), "source": "configured_rank_options"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "ranks": []}), 500


@app.route('/get_download_results_summary', methods=['GET'])
def get_download_results_summary():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    base_folder = _active_download_root()
    if not os.path.isdir(base_folder):
        return jsonify({"success": False, "message": "Download folder not found."})

    try:
        role_folders = _list_assignable_rank_folders(base_folder)
        mailbox_role_counts = {}
        online_role_counts = {}
        mailbox_success_count = 0
        online_success_count = 0
        mailbox_latest_ts = 0.0
        online_latest_ts = 0.0

        for folder in role_folders:
            folder_path = os.path.join(base_folder, folder)
            mailbox_folder_count = 0
            online_folder_count = 0
            for name in os.listdir(folder_path):
                if not name.lower().endswith('.pdf'):
                    continue
                full_path = os.path.join(folder_path, name)
                modified_ts = os.path.getmtime(full_path)
                if name.startswith("EMAIL_"):
                    mailbox_folder_count += 1
                    mailbox_success_count += 1
                    mailbox_latest_ts = max(mailbox_latest_ts, modified_ts)
                else:
                    online_folder_count += 1
                    online_success_count += 1
                    online_latest_ts = max(online_latest_ts, modified_ts)
            if mailbox_folder_count:
                mailbox_role_counts[folder] = mailbox_folder_count
            if online_folder_count:
                online_role_counts[folder] = online_folder_count

        manual_review_dir = os.path.join(base_folder, "_EmailInbox_ManualReview")
        manual_review_count = 0
        if os.path.isdir(manual_review_dir):
            for name in os.listdir(manual_review_dir):
                if name.lower().endswith(".pdf"):
                    manual_review_count += 1
                    mailbox_latest_ts = max(mailbox_latest_ts, os.path.getmtime(os.path.join(manual_review_dir, name)))

        failed_dir = os.path.join(base_folder, "_EmailInbox_Failed")
        failed_count = 0
        if os.path.isdir(failed_dir):
            for name in os.listdir(failed_dir):
                full_path = os.path.join(failed_dir, name)
                if not os.path.isfile(full_path) or name.lower().endswith(".json"):
                    continue
                failed_count += 1
                mailbox_latest_ts = max(mailbox_latest_ts, os.path.getmtime(full_path))

        def _serialize_role_counts(role_counts):
            return [
                {"folder": folder, "count": int(role_counts.get(folder, 0))}
                for folder in sorted(role_counts.keys())
            ]

        def _iso_or_empty(timestamp):
            if not timestamp:
                return ""
            return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")

        return jsonify({
            "success": True,
            "mailbox": {
                "last_fetch_at": _iso_or_empty(mailbox_latest_ts),
                "total_processed": mailbox_success_count + manual_review_count + failed_count,
                "successfully_routed": mailbox_success_count,
                "manual_review_count": manual_review_count,
                "failed_count": failed_count,
                "role_counts": _serialize_role_counts(mailbox_role_counts),
            },
            "online": {
                "last_download_at": _iso_or_empty(online_latest_ts),
                "total_downloaded": online_success_count,
                "role_counts": _serialize_role_counts(online_role_counts),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/get_rank_folder_files', methods=['GET'])
def get_rank_folder_files():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    rank_folder_id = str(request.args.get('rank_folder_id', '')).strip()
    rank_folder = str(request.args.get('rank_folder', '')).strip()
    base_folder = _active_download_root()
    try:
        resolved = _resolve_rank_folder_reference(
            rank_folder_id=rank_folder_id,
            rank_folder=rank_folder,
            base_folder=base_folder,
        )
        if not resolved.get("success"):
            status = 400 if resolved.get("error_code") in {"INVALID_RANK_FOLDER", "RANK_FOLDER_REQUIRED"} else 404
            return jsonify({
                "success": False,
                "message": resolved.get("message") or "Rank folder not found.",
                "error_code": resolved.get("error_code", "RANK_FOLDER_NOT_FOUND"),
                "files": [],
            }), status
        record = resolved["record"]
        folder_path = record["_resolved_path"]
        files = sorted(name for name in os.listdir(folder_path) if name.lower().endswith('.pdf'))
        return jsonify({
            "success": True,
            "files": files,
            "rank_folder": record["folder"],
            "rank_folder_id": record["rank_folder_id"],
            "download_root_id": record["download_root_id"],
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "files": []}), 500


@app.route('/get_rank_folder_ship_types', methods=['GET'])
def get_rank_folder_ship_types():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    rank_folder_id = str(request.args.get('rank_folder_id', '')).strip()
    rank_folder = str(request.args.get('rank_folder', '')).strip()
    if not rank_folder and not rank_folder_id:
        return jsonify({"success": True, "ship_types": []})
    resolved = _resolve_rank_folder_reference(
        rank_folder_id=rank_folder_id,
        rank_folder=rank_folder,
    )
    if not resolved.get("success"):
        status = 400 if resolved.get("error_code") in {"INVALID_RANK_FOLDER", "RANK_FOLDER_REQUIRED"} else 404
        return jsonify({
            "success": False,
            "message": resolved.get("message") or "Rank folder not found.",
            "error_code": resolved.get("error_code", "RANK_FOLDER_NOT_FOUND"),
        }), status
    record = resolved["record"]
    files = _rank_manifest_data(record["folder"])
    ship_types = set()
    for entry in files.values():
        if not isinstance(entry, dict):
            continue
        values = entry.get("applied_ship_types") or []
        if isinstance(values, list):
            for value in values:
                normalized = str(value or "").strip()
                if normalized:
                    ship_types.add(normalized)
    return jsonify({
        "success": True,
        "ship_types": sorted(ship_types),
        "rank_folder": record["folder"],
        "rank_folder_id": record["rank_folder_id"],
        "download_root_id": record["download_root_id"],
    })


@app.route('/get_config_ship_types', methods=['GET'])
def get_config_ship_types():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        ship_types_str = config.get('ShipTypes', 'ship_type_options', fallback='').strip()
        ship_types = [s.strip() for s in ship_types_str.split('\n') if s.strip()]
        return jsonify({"success": True, "ship_types": ship_types})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "ship_types": []}), 500


@app.route('/ai_search/refinement_scope/<search_session_id>/preflight', methods=['GET'])
def ai_search_refinement_scope_preflight(search_session_id):
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "available": False, "message": reason}), 403
    try:
        payload = _resolve_refinement_scope_preflight(
            search_session_id,
            actor_user_id=_session_actor_user_id(),
        )
        payload.pop("_parent_scope", None)
        payload.pop("_changed_members", None)
        status = 200 if payload.get("success") else 409
        return jsonify(payload), status
    except Exception as exc:
        return jsonify({
            "success": False,
            "available": False,
            "error_code": "REFINEMENT_SCOPE_STORE_UNAVAILABLE",
            "message": str(exc),
            "retryable": True,
        }), 503


@app.route('/ai_search/refinement_scope/changed_content_acknowledgements', methods=['POST'])
def ai_search_changed_content_acknowledgement():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    payload = request.json if request.is_json else {}
    parent_search_session_id = str((payload or {}).get("parent_search_session_id") or "").strip()
    search_request_id = str((payload or {}).get("search_request_id") or "").strip()
    submitted_fingerprint = str(
        (payload or {}).get("changed_content_set_fingerprint") or ""
    ).strip()
    if not parent_search_session_id or not search_request_id or not submitted_fingerprint:
        return jsonify({
            "success": False,
            "error_code": "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED",
            "message": "A fresh changed-content acknowledgement is required.",
            "retryable": False,
        }), 400
    try:
        preflight = _resolve_refinement_scope_preflight(
            parent_search_session_id,
            actor_user_id=_session_actor_user_id(),
        )
        current_fingerprint = str(
            (preflight.get("scope_summary") or {}).get("changed_content_set_fingerprint") or ""
        ).strip()
        if (
            not preflight.get("success")
            or not current_fingerprint
            or current_fingerprint != submitted_fingerprint
        ):
            return jsonify({
                "success": False,
                "error_code": "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED",
                "message": "The changed resume set was updated. Review the latest warning before continuing.",
                "retryable": False,
            }), 409
        acknowledgement = search_scope_repo.issue_changed_content_acknowledgement(
            actor_user_id=_session_actor_user_id(),
            parent_search_session_id=parent_search_session_id,
            search_request_id=search_request_id,
            changed_content_set_fingerprint=current_fingerprint,
        )
        return jsonify({"success": True, **acknowledgement})
    except Exception as exc:
        print(f"[BACKEND WARN] Failed to issue changed-content acknowledgement: {exc}")
        return jsonify({
            "success": False,
            "error_code": "REFINEMENT_SCOPE_STORE_UNAVAILABLE",
            "message": "The refinement acknowledgement store is temporarily unavailable.",
            "retryable": True,
        }), 503


@app.route('/ai_search/recovery_draft', methods=['GET', 'PUT', 'DELETE'])
def ai_search_recovery_draft():
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    actor_user_id = _session_actor_user_id()
    try:
        if request.method == "GET":
            record = search_scope_repo.load_latest_recovery_draft(actor_user_id=actor_user_id)
            return jsonify({"success": True, "draft": record})
        payload = request.json if request.is_json else {}
        if request.method == "DELETE":
            search_scope_repo.delete_recovery_drafts(
                actor_user_id=actor_user_id,
                tab_id=str((payload or {}).get("tab_id") or "").strip() or None,
            )
            return jsonify({"success": True})

        tab_id = str((payload or {}).get("tab_id") or "").strip()
        if not re.match(r"^[A-Za-z0-9._-]{8,128}$", tab_id):
            return jsonify({"success": False, "message": "Invalid recovery tab identifier."}), 400
        draft = _sanitize_ai_search_recovery_draft((payload or {}).get("draft"))
        saved = search_scope_repo.save_recovery_draft(
            actor_user_id=actor_user_id,
            tab_id=tab_id,
            draft=draft,
        )
        return jsonify({"success": True, **saved, "recovery_completeness": draft["recovery_completeness"]})
    except Exception as exc:
        print(f"[BACKEND WARN] AI Search recovery draft operation failed: {exc}")
        return jsonify({
            "success": False,
            "message": "AI Search recovery storage is temporarily unavailable.",
        }), 503


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
    rank_folder_id = request.args.get('rank_folder_id', '').strip()
    applied_ship_type = request.args.get('applied_ship_type', '').strip()
    experienced_ship_type = request.args.get('experienced_ship_type', '').strip()
    vessel_tonnage_filter = _parse_vessel_tonnage_filter_payload(request.args.get('vessel_tonnage_filter', ''))
    parent_search_session_id = request.args.get('parent_search_session_id', '').strip()
    search_request_id = request.args.get('search_request_id', '').strip() or str(uuid.uuid4())
    changed_content_acknowledgement_id = request.args.get(
        'changed_content_acknowledgement_id',
        '',
    ).strip()
    request_fingerprint = _ai_search_request_fingerprint(
        prompt=prompt,
        rank_folder_id=rank_folder_id,
        rank_folder=rank_folder,
        applied_ship_type=applied_ship_type,
        experienced_ship_type=experienced_ship_type,
        vessel_tonnage_filter=vessel_tonnage_filter,
        parent_search_session_id=parent_search_session_id,
        changed_content_acknowledgement_id=changed_content_acknowledgement_id,
    )
    request_claim_payload = {
        "prompt": str(prompt or "").strip(),
        "rank_folder_id": rank_folder_id,
        "rank_folder": str(rank_folder or "").strip(),
        "applied_ship_type": applied_ship_type,
        "experienced_ship_type": experienced_ship_type,
        "vessel_tonnage_filter": vessel_tonnage_filter,
        "parent_search_session_id": parent_search_session_id,
        "changed_content_acknowledgement_id": changed_content_acknowledgement_id,
    }
    _log_usage("analyze_stream", f"AI search started for rank_folder={rank_folder}")
    search_session_id = str(uuid.uuid4())
    actor_role = _session_role()
    actor_username = _session_username()
    actor_user_id = _session_actor_user_id()

    def _log_ai_search_audit_rows(audit_rows, rank_folder, prompt, applied_ship_type, experienced_ship_type, search_session_id):
        for row in audit_rows or []:
            filename = str(row.get("filename", "")).strip()
            candidate_id = str(row.get("candidate_id", "")).strip()
            if not candidate_id and filename:
                candidate_id = _extract_candidate_id_from_filename(filename) or ""
            reasons = row.get("hard_filter_reasons") or []
            reason_codes = ";".join(
                str(reason.get("reason_code", "")).strip()
                for reason in reasons
                if str(reason.get("reason_code", "")).strip()
            )
            reason_messages = "; ".join(
                str(reason.get("message", "")).strip()
                for reason in reasons
                if str(reason.get("message", "")).strip()
            )
            llm_promoted = row.get("llm_promoted") or []
            if not isinstance(llm_promoted, (list, tuple, set)):
                llm_promoted = [llm_promoted]
            llm_promoted_families = ";".join(
                str(family).strip()
                for family in llm_promoted
                if str(family).strip()
            )
            csv_manager.log_ai_search_audit(
                search_session_id=search_session_id,
                candidate_id=candidate_id,
                filename=filename,
                facts_version=str(row.get("facts_version", "")).strip(),
                rank_applied_for=rank_folder,
                ai_prompt=prompt,
                applied_ship_type_filter=applied_ship_type,
                experienced_ship_type_filter=experienced_ship_type,
                hard_filter_decision=str(row.get("hard_filter_decision", "")).strip(),
                reason_codes=reason_codes,
                reason_messages=reason_messages,
                llm_reached=bool(row.get("llm_reached", False)),
                result_bucket=str(row.get("result_bucket", "")).strip(),
                llm_promoted_families=llm_promoted_families,
            )

    def generate():
        request_claim_started = False
        claim_terminal_recorded = False

        def _mark_request_failed(error_code, message):
            nonlocal claim_terminal_recorded
            if not request_claim_started:
                return True
            if claim_terminal_recorded:
                return True
            try:
                marked = bool(search_scope_repo.fail_search_request(
                    search_request_id=search_request_id,
                    actor_user_id=actor_user_id,
                    request_fingerprint=request_fingerprint,
                    error_code=error_code,
                    error_message=message,
                ))
                if marked:
                    claim_terminal_recorded = True
                return marked
            except Exception as claim_exc:
                print(f"[BACKEND WARN] Failed to mark AI search request failed: {claim_exc}")
                return False

        def _mark_request_complete(summary):
            nonlocal claim_terminal_recorded
            if not request_claim_started:
                return True
            if claim_terminal_recorded:
                return True
            try:
                marked = bool(search_scope_repo.complete_search_request(
                    search_request_id=search_request_id,
                    actor_user_id=actor_user_id,
                    request_fingerprint=request_fingerprint,
                    summary=summary,
                ))
                if marked:
                    claim_terminal_recorded = True
                return marked
            except Exception as claim_exc:
                print(f"[BACKEND WARN] Failed to mark AI search request complete: {claim_exc}")
                return False

        def _request_store_unavailable_sse(message=None):
            payload = _request_status_event({
                "request_status": "SEARCH_REQUEST_STORE_UNAVAILABLE",
                "message": message or "AI Search request tracking is temporarily unavailable. Please retry with a new request.",
                "retryable": True,
                "search_request_id": search_request_id,
                "search_session_id": search_session_id,
            })
            return f"data: {json.dumps(payload)}\n\n"

        def _error_sse(message, *, error_code="AI_SEARCH_REQUEST_FAILED", retryable=False):
            if not _mark_request_failed(error_code, message):
                return _request_store_unavailable_sse(
                    "AI Search request tracking could not record the failed request. Please retry with a new request.",
                )
            payload = {
                "type": "error",
                "message": message,
                "retryable": bool(retryable),
            }
            if error_code:
                payload["error_code"] = error_code
            return f"data: {json.dumps(payload)}\n\n"

        try:
            if not prompt:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Missing required data'})}\n\n"
                return

            try:
                claim = search_scope_repo.claim_search_request(
                    search_request_id=search_request_id,
                    actor_user_id=actor_user_id,
                    request_fingerprint=request_fingerprint,
                    search_session_id=search_session_id,
                    request=request_claim_payload,
                )
            except Exception as claim_exc:
                print(f"[BACKEND WARN] Failed to claim AI search request: {claim_exc}")
                yield f"data: {json.dumps(_request_status_event({'request_status': 'SEARCH_REQUEST_STORE_UNAVAILABLE', 'message': 'AI Search request tracking is temporarily unavailable. Please retry.', 'retryable': True, 'search_request_id': search_request_id}))}\n\n"
                return
            if not claim.get("claimed"):
                yield f"data: {json.dumps(_request_status_event(claim))}\n\n"
                return
            request_claim_started = True

            effective_rank_folder = str(rank_folder or "").strip()
            effective_rank_folder_id = str(rank_folder_id or "").strip()
            effective_download_root_id = ""
            effective_applied_ship_type = applied_ship_type
            effective_experienced_ship_type = experienced_ship_type
            effective_vessel_tonnage_filter = dict(vessel_tonnage_filter or {})
            search_mode = "root"
            root_search_session_id = search_session_id
            refinement_depth = 0
            candidate_scope_ids = None
            candidate_scope_memberships = None
            parent_scope = {}
            target_record = None

            if parent_search_session_id:
                try:
                    parent_scope = search_scope_repo.get_refinement_parent_scope(
                        parent_search_session_id,
                        actor_user_id=actor_user_id,
                    )
                except Exception as scope_exc:
                    print(f"[BACKEND WARN] Failed to resolve AI search refinement scope: {scope_exc}")
                    yield _error_sse(
                        "The previous verified search scope is temporarily unavailable.",
                        error_code="REFINEMENT_SCOPE_STORE_UNAVAILABLE",
                        retryable=True,
                    )
                    return
                if not parent_scope.get("success"):
                    yield _error_sse(
                        parent_scope.get("message") or "The previous verified search scope is not available for refinement.",
                        error_code=parent_scope.get("error_code", "REFINEMENT_PARENT_NOT_FOUND"),
                        retryable=bool(parent_scope.get("retryable", False)),
                    )
                    return

                parent_session = parent_scope.get("session") or {}
                parent_rank_folder = str(parent_session.get("rank_folder") or "").strip()
                parent_applied_ship_type = str(parent_session.get("applied_ship_type") or "").strip()
                parent_experienced_ship_type = str(parent_session.get("experienced_ship_type") or "").strip()
                parent_context = parent_session.get("context") or {}
                parent_vessel_tonnage_filter = _normalize_vessel_tonnage_filter(
                    parent_context.get("vessel_tonnage_filter") or {}
                )
                context_mismatch = (
                    (effective_rank_folder and effective_rank_folder != parent_rank_folder)
                    or (effective_applied_ship_type and effective_applied_ship_type != parent_applied_ship_type)
                    or (
                        effective_experienced_ship_type
                        and effective_experienced_ship_type != parent_experienced_ship_type
                    )
                    or (
                        effective_vessel_tonnage_filter
                        and effective_vessel_tonnage_filter != parent_vessel_tonnage_filter
                    )
                )
                if context_mismatch:
                    yield _error_sse(
                        "Refinement must use the rank, ship-type, and tonnage filters saved with the previous verified search.",
                        error_code="REFINEMENT_CONTEXT_MISMATCH",
                        retryable=False,
                    )
                    return

                try:
                    authoritative_preflight = _resolve_refinement_scope_preflight(
                        parent_search_session_id,
                        actor_user_id=actor_user_id,
                    )
                except Exception as scope_exc:
                    print(f"[BACKEND WARN] Failed to resolve live AI search refinement scope: {scope_exc}")
                    yield _error_sse(
                        "The previous verified search scope is temporarily unavailable.",
                        error_code="REFINEMENT_SCOPE_STORE_UNAVAILABLE",
                        retryable=True,
                    )
                    return
                if not authoritative_preflight.get("success"):
                    yield _error_sse(
                        authoritative_preflight.get("message") or "The previous verified search scope is not available for refinement.",
                        error_code=authoritative_preflight.get("error_code", "REFINEMENT_SCOPE_UNRESOLVABLE"),
                        retryable=bool(authoritative_preflight.get("retryable", False)),
                    )
                    return

                effective_rank_folder = parent_rank_folder
                effective_applied_ship_type = parent_applied_ship_type
                effective_experienced_ship_type = parent_experienced_ship_type
                effective_vessel_tonnage_filter = parent_vessel_tonnage_filter
                authoritative_context = authoritative_preflight.get("search_context") or {}
                effective_rank_folder_id = str(
                    authoritative_context.get("rank_folder_id") or ""
                ).strip()
                effective_download_root_id = str(
                    authoritative_context.get("download_root_id") or ""
                ).strip()
                search_mode = "refinement"
                root_search_session_id = str(
                    parent_session.get("root_search_session_id") or parent_search_session_id
                ).strip()
                refinement_depth = int(parent_session.get("refinement_depth") or 0) + 1
                candidate_scope_ids = list(parent_scope.get("candidate_scope_ids") or [])
                candidate_scope_memberships = list(parent_scope.get("memberships") or [])
                current_changed_fingerprint = str(
                    (authoritative_preflight.get("scope_summary") or {}).get(
                        "changed_content_set_fingerprint"
                    ) or ""
                ).strip()
                if current_changed_fingerprint:
                    acknowledgement_ok = search_scope_repo.consume_changed_content_acknowledgement(
                        acknowledgement_id=changed_content_acknowledgement_id,
                        actor_user_id=actor_user_id,
                        parent_search_session_id=parent_search_session_id,
                        search_request_id=search_request_id,
                        changed_content_set_fingerprint=current_changed_fingerprint,
                    )
                    if not acknowledgement_ok:
                        yield _error_sse(
                            "One or more resumes changed after the previous search. Review and acknowledge the updated resume set before continuing.",
                            error_code="REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED",
                            retryable=False,
                        )
                        return

            if search_mode == "root":
                resolved_rank = _resolve_rank_folder_reference(
                    rank_folder_id=effective_rank_folder_id,
                    rank_folder=effective_rank_folder,
                )
                if not resolved_rank.get("success"):
                    error_code = resolved_rank.get("error_code", "RANK_FOLDER_NOT_FOUND")
                    yield _error_sse(
                        resolved_rank.get("message") or "Rank folder not found.",
                        error_code=error_code,
                        retryable=False,
                    )
                    return
                rank_record = resolved_rank["record"]
                effective_rank_folder = rank_record["folder"]
                effective_rank_folder_id = rank_record["rank_folder_id"]
                effective_download_root_id = rank_record["download_root_id"]
                target_record = rank_record

            if not effective_rank_folder:
                yield _error_sse(
                    "Missing required data",
                    error_code="AI_SEARCH_MISSING_REQUIRED_DATA",
                    retryable=False,
                )
                return
            if not _is_safe_name(effective_rank_folder):
                error_code = "REFINEMENT_CONTEXT_UNAVAILABLE" if search_mode == "refinement" else "INVALID_RANK_FOLDER"
                yield _error_sse(
                    "Invalid rank folder.",
                    error_code=error_code,
                    retryable=False,
                )
                return

            if target_record is None:
                resolved_target = _resolve_rank_folder_reference(
                    rank_folder_id=effective_rank_folder_id,
                    rank_folder=effective_rank_folder,
                )
                if not resolved_target.get("success"):
                    error_code = "REFINEMENT_CONTEXT_UNAVAILABLE" if search_mode == "refinement" else "RANK_FOLDER_NOT_FOUND"
                    yield _error_sse(
                        resolved_target.get("message") or ("Rank folder not found: " + effective_rank_folder),
                        error_code=error_code,
                        retryable=False,
                    )
                    return
                target_record = resolved_target["record"]
            target_folder = target_record["_resolved_path"]
            effective_rank_folder = target_record["folder"]
            effective_rank_folder_id = target_record["rank_folder_id"]
            effective_download_root_id = target_record["download_root_id"]
            
            # Create analyzer and run streaming analysis
            analyzer = _build_analyzer()
            _record_supabase_telemetry(
                telemetry_kind="system_log",
                category="ai_search",
                status="started",
                summary=f"Streaming AI search started for rank={effective_rank_folder}.",
                payload={
                    "rank_folder": effective_rank_folder,
                    "rank_folder_id": effective_rank_folder_id,
                    "download_root_id": effective_download_root_id,
                    "applied_ship_type": effective_applied_ship_type,
                    "experienced_ship_type": effective_experienced_ship_type,
                    "vessel_tonnage_filter": effective_vessel_tonnage_filter,
                    "search_session_id": search_session_id,
                    "search_mode": search_mode,
                    "parent_search_session_id": parent_search_session_id,
                    "search_request_id": search_request_id,
                },
                actor_role=actor_role,
                actor_username=actor_username,
                session_id=search_session_id,
            )
            _schedule_search_prompt_audit(
                analyzer,
                prompt,
                effective_rank_folder,
                search_session_id=search_session_id,
                actor_role=actor_role,
                actor_username=actor_username,
            )
            
            # Stream progress events
            for progress_event in analyzer.run_analysis_stream(
                effective_rank_folder,
                prompt,
                applied_ship_type=effective_applied_ship_type,
                experienced_ship_type=effective_experienced_ship_type,
                vessel_tonnage_filter=effective_vessel_tonnage_filter,
                review_capture_callback=_candidate_facts_review_capture_callback,
                candidate_scope_ids=candidate_scope_ids,
                candidate_scope_memberships=candidate_scope_memberships,
            ):
                event_to_client = progress_event
                if progress_event.get("type") == "complete":
                    try:
                        _log_ai_search_audit_rows(
                            progress_event.get("hard_filter_audit"),
                            effective_rank_folder,
                            prompt,
                            effective_applied_ship_type,
                            effective_experienced_ship_type,
                            search_session_id,
                        )
                    except Exception as audit_exc:
                        print(f"[BACKEND WARN] Failed to persist AI search audit rows: {audit_exc}")
                    # The frontend does not consume the raw audit rows. Excluding them
                    # keeps the final SSE payload smaller and more reliable.
                    event_to_client = {
                        key: value
                        for key, value in progress_event.items()
                        if key != "hard_filter_audit"
                    }
                    verified_matches = progress_event.get("verified_matches", [])
                    memberships, missing_identity = _scope_memberships_from_verified_matches(verified_matches)
                    memberships_to_persist = memberships if not missing_identity else []
                    hard_filter_summary = progress_event.get("hard_filter_summary") or {}
                    scope_summary = dict(progress_event.get("scope_summary") or {})
                    if not scope_summary:
                        scanned_count = int(hard_filter_summary.get("scanned", 0) or 0)
                        scope_summary = {
                            "eligible_population_count": scanned_count,
                            "retrieved_count": None,
                            "evaluated_count": scanned_count,
                            "requested_count": scanned_count,
                            "resolved_count": scanned_count,
                            "changed_content_count": 0,
                            "stale_count": 0,
                            "unresolvable_count": 0,
                            "duplicate_count": 0,
                        }
                    refinement_payload = {
                        "available": False,
                        "search_session_id": search_session_id,
                        "search_mode": search_mode,
                        "candidate_scope_member_count": 0,
                        "reason_code": "",
                        "message": "",
                    }
                    if not verified_matches:
                        refinement_payload.update({
                            "reason_code": "NO_VERIFIED_MATCHES",
                            "message": "No verified matches are available to refine.",
                        })
                    elif missing_identity:
                        refinement_payload.update({
                            "reason_code": "REFINEMENT_SCOPE_IDENTITY_INCOMPLETE",
                            "message": "These results cannot be refined because candidate identity preparation is incomplete.",
                            "missing_identity_count": len(missing_identity),
                        })

                    try:
                        output_counts = {
                            "candidate_scope_member_count": len(memberships_to_persist),
                            "verified_count": len(verified_matches),
                            "uncertain_count": len(progress_event.get("uncertain_matches", [])),
                            "needs_review_count": len(progress_event.get("unknown_matches", [])),
                        }
                        persisted_scope = search_scope_repo.complete_search_session(
                            search_session_id=search_session_id,
                            actor_user_id=actor_user_id,
                            actor_username=actor_username,
                            actor_role=actor_role,
                            rank_folder=effective_rank_folder,
                            applied_ship_type=effective_applied_ship_type,
                            experienced_ship_type=effective_experienced_ship_type,
                            prompt=prompt,
                            search_mode=search_mode,
                            root_search_session_id=root_search_session_id,
                            parent_search_session_id=parent_search_session_id or None,
                            refinement_depth=refinement_depth,
                            context={
                                "rank_folder": effective_rank_folder,
                                "rank_folder_id": effective_rank_folder_id,
                                "download_root_id": effective_download_root_id,
                                "applied_ship_type": effective_applied_ship_type,
                                "experienced_ship_type": effective_experienced_ship_type,
                                "vessel_tonnage_filter": effective_vessel_tonnage_filter,
                            },
                            input_scope=scope_summary,
                            output=output_counts,
                            memberships=memberships_to_persist,
                        )
                        if verified_matches and not missing_identity:
                            refinement_payload.update({
                                "available": True,
                                "candidate_scope_member_count": persisted_scope.get(
                                    "candidate_scope_member_count",
                                    len(memberships_to_persist),
                                ),
                                "membership_expires_at": persisted_scope.get("membership_expires_at", ""),
                                "message": "Search scope saved. Previous verified matches can be refined.",
                            })
                    except Exception as scope_exc:
                        print(f"[BACKEND WARN] Failed to persist AI search refinement scope: {scope_exc}")
                        refinement_payload.update({
                            "available": False,
                            "reason_code": "REFINEMENT_SCOPE_STORE_UNAVAILABLE",
                            "message": "These results cannot be refined because the search scope could not be saved.",
                        })
                    event_to_client["search_session_id"] = search_session_id
                    event_to_client["search_session"] = {
                        "search_session_id": search_session_id,
                        "root_search_session_id": root_search_session_id,
                        "parent_search_session_id": parent_search_session_id or None,
                        "search_mode": search_mode,
                        "refinement_depth": refinement_depth,
                        "search_request_id": search_request_id,
                    }
                    event_to_client["search_context"] = {
                        "rank_folder": effective_rank_folder,
                        "rank_folder_id": effective_rank_folder_id,
                        "download_root_id": effective_download_root_id,
                        "applied_ship_type": effective_applied_ship_type,
                        "experienced_ship_type": effective_experienced_ship_type,
                        "vessel_tonnage_filter": effective_vessel_tonnage_filter,
                    }
                    event_to_client["scope_summary"] = scope_summary
                    event_to_client["refinement"] = refinement_payload
                    _record_supabase_telemetry(
                        telemetry_kind="system_log",
                        category="ai_search",
                        status="complete",
                        summary=f"Streaming AI search completed for rank={effective_rank_folder}.",
                        payload={
                            "rank_folder": effective_rank_folder,
                            "applied_ship_type": effective_applied_ship_type,
                            "experienced_ship_type": effective_experienced_ship_type,
                            "vessel_tonnage_filter": effective_vessel_tonnage_filter,
                            "search_session_id": search_session_id,
                            "search_mode": search_mode,
                            "parent_search_session_id": parent_search_session_id,
                            "verified_matches": len(progress_event.get("verified_matches", [])),
                            "uncertain_matches": len(progress_event.get("uncertain_matches", [])),
                        },
                        actor_role=actor_role,
                        actor_username=actor_username,
                        session_id=search_session_id,
                    )
                if event_to_client.get("type") in {"complete", "graceful_failure"}:
                    # Analyzer streams are expected to produce one terminal event.
                    # Treat any duplicate terminal event as already recorded so a
                    # producer regression cannot mask the original search result.
                    terminal_refinement = event_to_client.get("refinement") or {}
                    terminal_marked = _mark_request_complete({
                        "search_session_id": search_session_id,
                        "search_mode": search_mode,
                        "parent_search_session_id": parent_search_session_id or "",
                        "verified_count": len(event_to_client.get("verified_matches", []) or []),
                        "uncertain_count": len(event_to_client.get("uncertain_matches", []) or []),
                        "needs_review_count": len(event_to_client.get("unknown_matches", []) or []),
                        "refinement_available": bool(terminal_refinement.get("available", False)),
                        "candidate_scope_member_count": int(
                            terminal_refinement.get("candidate_scope_member_count") or 0
                        ),
                        "graceful_failure": event_to_client.get("type") == "graceful_failure",
                        "message": event_to_client.get("message") or "",
                    })
                    if not terminal_marked:
                        yield _request_store_unavailable_sse(
                            "AI Search request tracking could not record the completed request. Please retry with a new request.",
                        )
                        return
                yield f"data: {json.dumps(event_to_client)}\n\n"
            
        except Exception as e:
            print(f"[BACKEND ERROR] {e}")
            import traceback
            traceback.print_exc()
            _record_supabase_telemetry(
                telemetry_kind="system_log",
                category="ai_search",
                status="error",
                summary=f"Streaming AI search failed for rank={rank_folder}.",
                payload={
                    "rank_folder": rank_folder,
                    "applied_ship_type": applied_ship_type,
                    "experienced_ship_type": experienced_ship_type,
                    "search_session_id": search_session_id,
                    "error": f"{type(e).__name__}: {e}",
                },
                actor_role=actor_role,
                actor_username=actor_username,
                session_id=search_session_id,
            )
            yield _error_sse(str(e), error_code=type(e).__name__, retryable=False)
        finally:
            if request_claim_started and not claim_terminal_recorded:
                try:
                    search_scope_repo.fail_search_request(
                        search_request_id=search_request_id,
                        actor_user_id=actor_user_id,
                        request_fingerprint=request_fingerprint,
                        error_code="AI_SEARCH_REQUEST_ABANDONED",
                        error_message="AI Search request ended before a terminal event was durably recorded.",
                    )
                except Exception as claim_exc:
                    print(f"[BACKEND WARN] Failed to mark AI search request abandoned: {claim_exc}")
    
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
        applied_ship_type = str(data.get('applied_ship_type', '')).strip()
        experienced_ship_type = str(data.get('experienced_ship_type', '')).strip()
        vessel_tonnage_filter = _normalize_vessel_tonnage_filter(data.get('vessel_tonnage_filter') or {})

        if not prompt or not rank_folder:
            return jsonify({"success": False, "message": "AI prompt and a rank folder selection are required."}), 400
        
        target_folder = os.path.join(_active_download_root(), rank_folder)
        if not os.path.isdir(target_folder):
            return jsonify({"success": False, "message": f"Rank folder not found: {rank_folder}"}), 400
        
        print(f"[BACKEND] Starting analysis for rank folder: {rank_folder}")
        print(f"[BACKEND] Prompt: {prompt}")
        
        analyzer = _build_analyzer()
        actor_role = _session_role()
        actor_username = _session_username()
        _record_supabase_telemetry(
            telemetry_kind="system_log",
            category="ai_search",
            status="started",
            summary=f"AI search started for rank={rank_folder}.",
            payload={
                "rank_folder": rank_folder,
                "applied_ship_type": applied_ship_type,
                "experienced_ship_type": experienced_ship_type,
                "vessel_tonnage_filter": vessel_tonnage_filter,
            },
            actor_role=actor_role,
            actor_username=actor_username,
        )
        _schedule_search_prompt_audit(
            analyzer,
            prompt,
            rank_folder,
            search_session_id=str(request.headers.get("X-Request-Id") or ""),
            actor_role=actor_role,
            actor_username=actor_username,
        )
        result = analyzer.run_analysis(
            rank_folder,
            prompt,
            applied_ship_type=applied_ship_type,
            experienced_ship_type=experienced_ship_type,
            vessel_tonnage_filter=vessel_tonnage_filter,
            review_capture_callback=_candidate_facts_review_capture_callback,
        )
        _log_usage("analyze", f"AI search completed for rank_folder={rank_folder}", {
            "success": bool(result.get("success")),
            "verified_matches": len(result.get("verified_matches", [])),
            "uncertain_matches": len(result.get("uncertain_matches", [])),
        })
        _record_supabase_telemetry(
            telemetry_kind="system_log",
            category="ai_search",
            status="complete",
            summary=f"AI search completed for rank={rank_folder}.",
            payload={
                "rank_folder": rank_folder,
                "applied_ship_type": applied_ship_type,
                "experienced_ship_type": experienced_ship_type,
                "vessel_tonnage_filter": vessel_tonnage_filter,
                "success": bool(result.get("success")),
                "verified_matches": len(result.get("verified_matches", [])),
                "uncertain_matches": len(result.get("uncertain_matches", [])),
            },
            actor_role=actor_role,
            actor_username=actor_username,
        )
        
        print(f"[BACKEND] Analysis complete. Success: {result.get('success')}")
        return jsonify(result)
    
    except Exception as e:
        print(f"[BACKEND ERROR] {e}")
        import traceback
        traceback.print_exc()
        _record_supabase_telemetry(
            telemetry_kind="system_log",
            category="ai_search",
            status="error",
            summary=f"AI search failed for rank={rank_folder}.",
            payload={
                "rank_folder": rank_folder,
                "applied_ship_type": applied_ship_type,
                "experienced_ship_type": experienced_ship_type,
                "vessel_tonnage_filter": vessel_tonnage_filter,
                "error": f"{type(e).__name__}: {e}",
            },
            actor_role=actor_role,
            actor_username=actor_username,
        )
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    """Store user feedback for learning"""
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return jsonify({"success": False, "message": reason}), 403
    try:
        data = request.json
        
        analyzer = _build_analyzer()
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
        base_dir = _active_download_root()
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


@app.route('/preview_downloaded_resume/<path:rank_folder>/<path:filename>')
def preview_downloaded_resume(rank_folder, filename):
    ok, reason = _require_role("admin", "manager", "recruiter")
    if not ok:
        return reason, 403
    if _use_local_agent():
        try:
            resp = _agent_request("GET", f"/preview_downloaded_resume/{quote(rank_folder, safe='')}/{quote(filename, safe='')}", timeout=15)
            if resp.status_code < 400:
                content_type = resp.headers.get("Content-Type", "application/pdf")
                return Response(resp.content, status=resp.status_code, content_type=content_type)
            if resp.status_code != 404:
                return jsonify({"success": False, "message": resp.text or "Local preview unavailable"}), resp.status_code
        except Exception as exc:
            print(f"[RESUME] Local agent preview proxy failed: {exc}")
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

    source_base_dir = _active_download_root()
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
        download_root = _active_download_root()
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
