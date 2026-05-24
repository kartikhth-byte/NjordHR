#!/usr/bin/env python3
"""
M2-T7 parity report: compare local CSV/SQLite AI stores with Supabase.

Usage:
  python3 scripts/ai_store_parity_report.py
  python3 scripts/ai_store_parity_report.py --sample-size 10 --output reports/ai_store_parity.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_env import normalize_env_value, normalized_url
from repositories.supabase_registry_repo import canonical_file_key
from scripts.migrate_local_state_to_supabase import _load_runtime_paths, _resolve_supabase_credentials, _load_sqlite_rows


REGISTRY_FIELDS = ["file_key", "last_modified", "resume_id"]
FEEDBACK_FIELDS = [
    "filename",
    "query",
    "llm_decision",
    "llm_reason",
    "llm_confidence",
    "user_decision",
    "user_notes",
    "timestamp",
]


def _normalize_text(value):
    return normalize_env_value(value)


def _normalize_number(value):
    if value is None or value == "":
        return ""
    try:
        return "{:.15g}".format(float(value))
    except Exception:
        return normalize_env_value(value)


def _supabase_request(supabase_url, api_key, method, path, *, params=None, json_body=None, timeout=30, headers=None):
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


def _fetch_all_supabase_rows(supabase_url, api_key, path, *, params=None, page_size=1000):
    rows = []
    offset = 0
    base_params = dict(params or {})
    while True:
        page_params = dict(base_params)
        page_params["limit"] = page_size
        page_params["offset"] = offset
        batch = _supabase_request(supabase_url, api_key, "GET", path, params=page_params)
        rows.extend(batch or [])
        if len(batch or []) < page_size:
            break
        offset += page_size
    return rows


def _registry_file_key(row):
    return _normalize_text(row.get("file_key") or canonical_file_key(row.get("file_path") or ""))


def _registry_payload(row):
    return {
        "file_key": _registry_file_key(row),
        "last_modified": _normalize_number(row.get("last_modified")),
        "resume_id": _normalize_text(row.get("resume_id")),
    }


def _feedback_key(payload):
    identity = {
        "filename": payload.get("filename", ""),
        "query": payload.get("query", ""),
        "timestamp": payload.get("timestamp", ""),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"ai_feedback:{digest}"


def _feedback_payload(row):
    payload = {
        "filename": _normalize_text(row.get("filename")),
        "query": _normalize_text(row.get("query")),
        "llm_decision": _normalize_text(row.get("llm_decision")),
        "llm_reason": _normalize_text(row.get("llm_reason")),
        "llm_confidence": _normalize_number(row.get("llm_confidence")),
        "user_decision": _normalize_text(row.get("user_decision")),
        "user_notes": _normalize_text(row.get("user_notes")),
        "timestamp": _normalize_text(row.get("timestamp")),
    }
    payload["feedback_key"] = _feedback_key(payload)
    return payload


def _index_rows(rows, key_fn, payload_fn):
    index = {}
    collisions = 0
    for row in rows:
        payload = payload_fn(row)
        key = key_fn(payload)
        if not key:
            continue
        if key in index:
            collisions += 1
        index[key] = payload
    return index, collisions


def _compare_indexes(local_map, supabase_map, fields, sample_size, seed):
    local_keys = set(local_map.keys())
    supabase_keys = set(supabase_map.keys())
    common_keys = sorted(local_keys & supabase_keys)
    missing_in_supabase = sorted(local_keys - supabase_keys)
    missing_in_local = sorted(supabase_keys - local_keys)

    rng = random.Random(seed)
    sampled_keys = common_keys if len(common_keys) <= sample_size else sorted(rng.sample(common_keys, sample_size))
    field_mismatches = []
    for key in sampled_keys:
        local_payload = local_map[key]
        supabase_payload = supabase_map[key]
        for field in fields:
            if local_payload.get(field, "") != supabase_payload.get(field, ""):
                field_mismatches.append({
                    "key": key,
                    "field": field,
                    "local": local_payload.get(field, ""),
                    "supabase": supabase_payload.get(field, ""),
                })

    return {
        "local_rows": len(local_map),
        "supabase_rows": len(supabase_map),
        "matched_rows": len(common_keys),
        "missing_in_supabase": missing_in_supabase,
        "missing_in_local": missing_in_local,
        "spot_check": {
            "sampled_keys": sampled_keys,
            "field_mismatches": field_mismatches,
        },
    }


def build_ai_store_parity_report(
    *,
    registry_db_path: str,
    feedback_db_path: str,
    supabase_url: str,
    supabase_api_key: str,
    sample_size: int = 20,
    seed: int = 42,
):
    registry_local_rows = _load_sqlite_rows(
        registry_db_path,
        "SELECT file_path, last_modified, resume_id FROM files ORDER BY file_path ASC",
    )
    registry_supabase_rows = _fetch_all_supabase_rows(
        supabase_url,
        supabase_api_key,
        "/rest/v1/ai_file_registry",
        params={"select": "file_key,last_modified,resume_id", "order": "file_key.asc"},
    )

    registry_local_map, registry_local_collisions = _index_rows(
        registry_local_rows,
        lambda payload: payload["file_key"],
        _registry_payload,
    )
    registry_supabase_map, registry_supabase_collisions = _index_rows(
        registry_supabase_rows,
        lambda payload: payload["file_key"],
        _registry_payload,
    )
    registry_report = _compare_indexes(
        registry_local_map,
        registry_supabase_map,
        REGISTRY_FIELDS,
        sample_size,
        seed,
    )
    registry_report.update({
        "duplicate_local_keys": registry_local_collisions,
        "duplicate_supabase_keys": registry_supabase_collisions,
    })

    feedback_local_rows = _load_sqlite_rows(
        feedback_db_path,
        """
        SELECT filename, query, llm_decision, llm_reason, llm_confidence,
               user_decision, user_notes, timestamp
        FROM feedback
        ORDER BY timestamp ASC, id ASC
        """,
    )
    feedback_supabase_rows = _fetch_all_supabase_rows(
        supabase_url,
        supabase_api_key,
        "/rest/v1/ai_feedback",
        params={
            "select": "filename,query,llm_decision,llm_reason,llm_confidence,user_decision,user_notes,timestamp",
            "order": "timestamp.asc",
        },
    )

    feedback_local_map, feedback_local_collisions = _index_rows(
        feedback_local_rows,
        lambda payload: payload["feedback_key"],
        _feedback_payload,
    )
    feedback_supabase_map, feedback_supabase_collisions = _index_rows(
        feedback_supabase_rows,
        lambda payload: payload["feedback_key"],
        _feedback_payload,
    )
    feedback_report = _compare_indexes(
        feedback_local_map,
        feedback_supabase_map,
        FEEDBACK_FIELDS,
        sample_size,
        seed,
    )
    feedback_report.update({
        "duplicate_local_keys": feedback_local_collisions,
        "duplicate_supabase_keys": feedback_supabase_collisions,
    })

    report = {
        "success": True,
        "parity_ok": (
            not registry_report["missing_in_supabase"]
            and not registry_report["missing_in_local"]
            and not registry_report["spot_check"]["field_mismatches"]
            and registry_report["duplicate_local_keys"] == 0
            and registry_report["duplicate_supabase_keys"] == 0
            and not feedback_report["missing_in_supabase"]
            and not feedback_report["missing_in_local"]
            and not feedback_report["spot_check"]["field_mismatches"]
            and feedback_report["duplicate_local_keys"] == 0
            and feedback_report["duplicate_supabase_keys"] == 0
        ),
        "scope": {
            "sample_size": sample_size,
            "seed": seed,
            "registry_db_path": registry_db_path,
            "feedback_db_path": feedback_db_path,
        },
        "registry": registry_report,
        "feedback": feedback_report,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local AI stores with Supabase for parity checks.")
    parser.add_argument("--config-path", default=os.getenv("NJORDHR_CONFIG_PATH", "config.ini"), help="Config path used to resolve local DB paths.")
    parser.add_argument("--registry-db-path", default="", help="Path to registry.db (defaults from config).")
    parser.add_argument("--feedback-db-path", default="", help="Path to feedback.db (defaults from config).")
    parser.add_argument("--supabase-url", default="", help="Supabase URL override.")
    parser.add_argument("--supabase-api-key", default="", help="Supabase API key override.")
    parser.add_argument("--sample-size", type=int, default=20, help="Spot-check sample size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic sampling.")
    parser.add_argument("--output", default="", help="Optional JSON file for the parity report.")
    args = parser.parse_args()

    paths = _load_runtime_paths(args.config_path)
    registry_db_path = args.registry_db_path or paths["registry_db_path"]
    feedback_db_path = args.feedback_db_path or paths["feedback_db_path"]
    supabase_url, supabase_api_key = _resolve_supabase_credentials(args.supabase_url, args.supabase_api_key)

    if not supabase_url or not supabase_api_key:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are required for parity report.")
        return 2

    report = build_ai_store_parity_report(
        registry_db_path=registry_db_path,
        feedback_db_path=feedback_db_path,
        supabase_url=supabase_url,
        supabase_api_key=supabase_api_key,
        sample_size=max(1, int(args.sample_size)),
        seed=int(args.seed),
    )

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved report to: {output_path}")

    return 0 if report.get("parity_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
