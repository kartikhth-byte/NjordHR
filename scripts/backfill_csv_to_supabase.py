#!/usr/bin/env python3
"""
Backfill master CSV candidate events into Supabase (idempotent).

Usage:
  python3 scripts/backfill_csv_to_supabase.py --dry-run
  python3 scripts/backfill_csv_to_supabase.py --apply
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from csv_manager import CSVManager
from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo, can_enable_supabase_repo


def _norm(value):
    return str(value or "").strip()


def _event_key_from_csv_row(row):
    return (
        _norm(row.get("Candidate_ID")),
        _norm(row.get("Filename")),
        _norm(row.get("Event_Type")),
        _norm(row.get("Status")),
        _norm(row.get("Notes")),
        _norm(row.get("Rank_Applied_For")),
        _norm(row.get("Search_Ship_Type")),
        _norm(row.get("AI_Search_Prompt")),
        _norm(row.get("AI_Match_Reason")),
    )


def _event_key_from_supabase_row(row):
    return (
        _norm(row.get("candidate_external_id")),
        _norm(row.get("filename")),
        _norm(row.get("event_type")),
        _norm(row.get("status")),
        _norm(row.get("notes")),
        _norm(row.get("rank_applied_for")),
        _norm(row.get("search_ship_type")),
        _norm(row.get("ai_search_prompt")),
        _norm(row.get("ai_match_reason")),
    )


def _load_csv_events(master_csv_path):
    if not os.path.isfile(master_csv_path):
        return pd.DataFrame(columns=CSVManager.COLUMNS)
    df = pd.read_csv(master_csv_path, keep_default_na=False)
    for col in CSVManager.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CSVManager.COLUMNS].copy()
    for col in CSVManager.COLUMNS:
        df[col] = df[col].astype(str).fillna("")
    if "Date_Added" in df.columns:
        df = df.sort_values("Date_Added")
    return df.reset_index(drop=True)


def backfill_csv_to_supabase(
    base_folder="Verified_Resumes",
    server_url="http://127.0.0.1:5000",
    apply=False,
    limit=0,
):
    if not can_enable_supabase_repo():
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are required.")

    master_csv_path = os.path.join(base_folder, "verified_resumes.csv")
    df = _load_csv_events(master_csv_path)
    if df.empty:
        return {
            "success": True,
            "applied": apply,
            "master_csv_path": master_csv_path,
            "csv_rows": 0,
            "existing_supabase_events": 0,
            "planned_inserts": 0,
            "inserted": 0,
            "skipped_existing": 0,
            "errors": [],
        }

    repo = SupabaseCandidateEventRepo(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        service_role_key=(os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")),
        server_url=server_url,
    )

    existing_events = repo._fetch_events(order_desc=False)
    existing_keys = {_event_key_from_supabase_row(row) for row in existing_events}

    planned_rows = []
    for _, row in df.iterrows():
        key = _event_key_from_csv_row(row)
        if key in existing_keys:
            continue
        planned_rows.append(row)

    if limit and limit > 0:
        planned_rows = planned_rows[:limit]

    inserted = 0
    skipped_existing = len(df) - len(planned_rows)
    errors = []

    for row in planned_rows:
        try:
            candidate_external_id = _norm(row.get("Candidate_ID"))
            filename = _norm(row.get("Filename"))
            rank_applied_for = _norm(row.get("Rank_Applied_For"))
            created_at = _norm(row.get("Date_Added"))

            if not candidate_external_id or not filename:
                errors.append(f"Missing candidate_id/filename for row: {row.to_dict()}")
                continue

            if apply:
                repo._upsert_candidate(
                    candidate_external_id,
                    {
                        "latest_filename": filename,
                        "rank_applied_for": rank_applied_for,
                        "name": _norm(row.get("Name")),
                        "present_rank": _norm(row.get("Present_Rank")),
                        "email": _norm(row.get("Email")),
                        "country": _norm(row.get("Country")),
                        "mobile_no": _norm(row.get("Mobile_No")),
                    },
                )
                repo._insert_event(
                    {
                        "candidate_external_id": candidate_external_id,
                        "filename": filename,
                        "resume_url": _norm(row.get("Resume_URL")) or f"{server_url}/get_resume/{rank_applied_for}/{filename}",
                        "event_type": _norm(row.get("Event_Type")) or "initial_verification",
                        "status": _norm(row.get("Status")) or "New",
                        "notes": _norm(row.get("Notes")),
                        "rank_applied_for": rank_applied_for,
                        "search_ship_type": _norm(row.get("Search_Ship_Type")),
                        "ai_search_prompt": _norm(row.get("AI_Search_Prompt")),
                        "ai_match_reason": _norm(row.get("AI_Match_Reason")),
                        "name": _norm(row.get("Name")),
                        "present_rank": _norm(row.get("Present_Rank")),
                        "email": _norm(row.get("Email")),
                        "country": _norm(row.get("Country")),
                        "mobile_no": _norm(row.get("Mobile_No")),
                        "created_at": created_at,
                    }
                )
            inserted += 1
        except Exception as exc:
            errors.append(str(exc))

    return {
        "success": len(errors) == 0,
        "applied": apply,
        "master_csv_path": master_csv_path,
        "csv_rows": int(len(df)),
        "existing_supabase_events": int(len(existing_events)),
        "planned_inserts": int(len(planned_rows)),
        "inserted": int(inserted),
        "skipped_existing": int(skipped_existing),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill CSV candidate events into Supabase.")
    parser.add_argument("--base-folder", default="Verified_Resumes", help="Path to Verified_Resumes folder.")
    parser.add_argument("--server-url", default="http://127.0.0.1:5000", help="Server URL for resume links.")
    parser.add_argument("--apply", action="store_true", help="Apply inserts. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max rows to insert.")
    parser.add_argument("--output", default="", help="Optional path to write JSON result.")
    args = parser.parse_args()

    result = backfill_csv_to_supabase(
        base_folder=args.base_folder,
        server_url=args.server_url,
        apply=args.apply,
        limit=args.limit,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved result to: {out}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
