#!/usr/bin/env python3
"""
One-time migration helpers for moving local NjordHR runtime state into Supabase.

This script orchestrates:
- verified_resumes.csv -> candidate_events/candidates
- registry.db -> ai_file_registry
- feedback.db -> ai_feedback
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_csv_to_supabase import backfill_csv_to_supabase, _load_csv_events
from runtime_env import normalize_env_value, normalized_url
from repositories.supabase_registry_repo import canonical_file_key


def _env(name: str, default: str = "") -> str:
    return normalize_env_value(os.getenv(name, default))


def _chunked(values: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _load_runtime_paths(config_path: str | None = None):
    config_path = config_path or _env("NJORDHR_CONFIG_PATH", "config.ini")
    parser = configparser.ConfigParser()
    if config_path and Path(config_path).exists():
        parser.read(config_path)

    log_dir = parser.get("Advanced", "log_dir", fallback="logs") if parser.has_section("Advanced") else "logs"

    def resolve(raw_path: str, fallback_name: str) -> str:
        candidate = normalize_env_value(raw_path) or fallback_name
        candidate = os.path.expanduser(candidate)
        if os.path.isabs(candidate):
            return os.path.abspath(candidate)
        return os.path.abspath(os.path.join(log_dir, candidate))

    registry_db_path = resolve(
        parser.get("Advanced", "registry_db_path", fallback="registry.db") if parser.has_section("Advanced") else "registry.db",
        "registry.db",
    )
    feedback_db_path = resolve(
        parser.get("Advanced", "feedback_db_path", fallback="feedback.db") if parser.has_section("Advanced") else "feedback.db",
        "feedback.db",
    )
    return {
        "config_path": config_path,
        "log_dir": log_dir,
        "registry_db_path": registry_db_path,
        "feedback_db_path": feedback_db_path,
    }


def _resolve_supabase_credentials(supabase_url: str | None = None, supabase_api_key: str | None = None):
    resolved_url = normalized_url(supabase_url or _env("SUPABASE_URL"))
    resolved_key = normalize_env_value(supabase_api_key or _env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY"))
    return resolved_url, resolved_key


def _supabase_request(supabase_url: str, api_key: str, method: str, path: str, *, params=None, json_body=None, timeout=30, headers=None):
    req_headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    resp = requests.request(
        method=method,
        url=f"{supabase_url.rstrip('/')}{path}",
        params=params or {},
        json=json_body,
        headers=req_headers,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase request failed ({resp.status_code}): {resp.text}")
    if not resp.text:
        return []
    try:
        return resp.json()
    except Exception:
        return []


def _load_sqlite_rows(db_path: str, query: str) -> list[dict]:
    if not db_path or not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def migrate_verified_resumes(
    *,
    base_folder: str = "Verified_Resumes",
    server_url: str = "http://127.0.0.1:5000",
    apply: bool = False,
    limit: int = 0,
):
    supabase_url, supabase_api_key = _resolve_supabase_credentials()
    if apply or (supabase_url and supabase_api_key):
        result = backfill_csv_to_supabase(
            base_folder=base_folder,
            server_url=server_url,
            apply=apply,
            limit=limit,
        )
        result["step"] = "verified_resumes_csv"
        return result

    master_csv_path = os.path.join(base_folder, "verified_resumes.csv")
    df = _load_csv_events(master_csv_path)
    planned_rows = len(df)
    if limit and limit > 0:
        planned_rows = min(planned_rows, limit)
    return {
        "step": "verified_resumes_csv",
        "success": True,
        "applied": False,
        "master_csv_path": master_csv_path,
        "csv_rows": int(len(df)),
        "existing_supabase_events": None,
        "planned_inserts": int(planned_rows),
        "inserted": 0,
        "skipped_existing": 0,
        "errors": [],
    }


def migrate_ai_registry(
    *,
    registry_db_path: str,
    supabase_url: str | None = None,
    supabase_api_key: str | None = None,
    apply: bool = False,
    chunk_size: int = 200,
):
    supabase_url, supabase_api_key = _resolve_supabase_credentials(supabase_url, supabase_api_key)
    local_rows = _load_sqlite_rows(
        registry_db_path,
        "SELECT file_path, last_modified, resume_id FROM files ORDER BY file_path ASC",
    )
    planned_rows = [
        {
            "file_key": canonical_file_key(row.get("file_path", "")),
            "last_modified": float(row.get("last_modified", 0) or 0),
            "resume_id": str(row.get("resume_id", "") or ""),
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        for row in local_rows
        if str(row.get("file_path", "") or "").strip()
    ]

    result = {
        "step": "ai_file_registry",
        "local_db_path": registry_db_path,
        "local_rows": len(local_rows),
        "planned_upserts": len(planned_rows),
        "applied": apply,
        "inserted": 0,
        "skipped": 0,
        "errors": [],
    }

    if not planned_rows:
        result["success"] = True
        return result

    if not apply:
        result["success"] = True
        return result

    if not supabase_url or not supabase_api_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are required.")

    inserted = 0
    try:
        for batch in _chunked(planned_rows, max(1, int(chunk_size))):
            _supabase_request(
                supabase_url,
                supabase_api_key,
                "POST",
                "/rest/v1/ai_file_registry",
                params={"on_conflict": "file_key"},
                json_body=batch,
                timeout=45,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            inserted += len(batch)
        result["inserted"] = inserted
        result["success"] = True
    except Exception as exc:
        result["errors"].append(str(exc))
        result["success"] = False
    return result


def migrate_ai_feedback(
    *,
    feedback_db_path: str,
    supabase_url: str | None = None,
    supabase_api_key: str | None = None,
    apply: bool = False,
    chunk_size: int = 200,
):
    supabase_url, supabase_api_key = _resolve_supabase_credentials(supabase_url, supabase_api_key)
    local_rows = _load_sqlite_rows(
        feedback_db_path,
        """
        SELECT filename, query, llm_decision, llm_reason, llm_confidence,
               user_decision, user_notes, timestamp
        FROM feedback
        ORDER BY id ASC
        """,
    )
    planned_rows = [
        {
            "filename": str(row.get("filename", "") or ""),
            "query": str(row.get("query", "") or ""),
            "llm_decision": str(row.get("llm_decision", "") or ""),
            "llm_reason": str(row.get("llm_reason", "") or ""),
            "llm_confidence": row.get("llm_confidence", None),
            "user_decision": str(row.get("user_decision", "") or ""),
            "user_notes": str(row.get("user_notes", "") or ""),
            "timestamp": str(row.get("timestamp", "") or datetime.now(UTC).isoformat().replace("+00:00", "Z")),
        }
        for row in local_rows
        if str(row.get("filename", "") or "").strip() and str(row.get("query", "") or "").strip()
    ]

    result = {
        "step": "ai_feedback",
        "local_db_path": feedback_db_path,
        "local_rows": len(local_rows),
        "planned_inserts": len(planned_rows),
        "applied": apply,
        "inserted": 0,
        "errors": [],
    }

    if not planned_rows:
        result["success"] = True
        return result

    if not apply:
        result["success"] = True
        return result

    if not supabase_url or not supabase_api_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are required.")

    inserted = 0
    try:
        for batch in _chunked(planned_rows, max(1, int(chunk_size))):
            _supabase_request(
                supabase_url,
                supabase_api_key,
                "POST",
                "/rest/v1/ai_feedback",
                json_body=batch,
                timeout=45,
                headers={"Prefer": "return=minimal"},
            )
            inserted += len(batch)
        result["inserted"] = inserted
        result["success"] = True
    except Exception as exc:
        result["errors"].append(str(exc))
        result["success"] = False
    return result


def migrate_local_state_to_supabase(
    *,
    base_folder: str = "Verified_Resumes",
    server_url: str = "http://127.0.0.1:5000",
    registry_db_path: str,
    feedback_db_path: str,
    supabase_url: str | None = None,
    supabase_api_key: str | None = None,
    apply: bool = False,
    limit: int = 0,
    chunk_size: int = 200,
):
    results = {
        "success": True,
        "applied": apply,
        "verified_resumes_csv": migrate_verified_resumes(
            base_folder=base_folder,
            server_url=server_url,
            apply=apply,
            limit=limit,
        ),
        "ai_file_registry": migrate_ai_registry(
            registry_db_path=registry_db_path,
            supabase_url=supabase_url,
            supabase_api_key=supabase_api_key,
            apply=apply,
            chunk_size=chunk_size,
        ),
        "ai_feedback": migrate_ai_feedback(
            feedback_db_path=feedback_db_path,
            supabase_url=supabase_url,
            supabase_api_key=supabase_api_key,
            apply=apply,
            chunk_size=chunk_size,
        ),
    }
    results["success"] = all(bool(results[key].get("success", False)) for key in ("verified_resumes_csv", "ai_file_registry", "ai_feedback"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate local NjordHR runtime state to Supabase.")
    parser.add_argument("--base-folder", default="Verified_Resumes", help="Path to Verified_Resumes root.")
    parser.add_argument("--server-url", default="http://127.0.0.1:5000", help="Server URL for resume links.")
    parser.add_argument("--config-path", default=_env("NJORDHR_CONFIG_PATH", "config.ini"), help="Config path used to resolve default runtime DB locations.")
    parser.add_argument("--registry-db-path", default="", help="Path to registry.db (defaults from config).")
    parser.add_argument("--feedback-db-path", default="", help="Path to feedback.db (defaults from config).")
    parser.add_argument("--apply", action="store_true", help="Perform writes instead of dry-run planning.")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for verified_resumes.csv backfill.")
    parser.add_argument("--chunk-size", type=int, default=200, help="Batch size for Supabase upserts/inserts.")
    parser.add_argument("--output", default="", help="Optional JSON file for the migration report.")
    args = parser.parse_args()

    paths = _load_runtime_paths(args.config_path)
    registry_db_path = args.registry_db_path or paths["registry_db_path"]
    feedback_db_path = args.feedback_db_path or paths["feedback_db_path"]
    creds_url, creds_key = _resolve_supabase_credentials()

    report = migrate_local_state_to_supabase(
        base_folder=args.base_folder,
        server_url=args.server_url,
        registry_db_path=registry_db_path,
        feedback_db_path=feedback_db_path,
        supabase_url=creds_url,
        supabase_api_key=creds_key,
        apply=args.apply,
        limit=args.limit,
        chunk_size=args.chunk_size,
    )

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved report to: {output_path}")

    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
