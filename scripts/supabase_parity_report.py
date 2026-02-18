#!/usr/bin/env python3
"""
M2-T7 parity report: compare CSV-backed and Supabase-backed candidate event views.

Usage:
  python3 scripts/supabase_parity_report.py
  python3 scripts/supabase_parity_report.py --rank 2nd_Officer --sample-size 25
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from repositories.csv_candidate_event_repo import CSVCandidateEventRepo
from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo, can_enable_supabase_repo


COMPARE_FIELDS = [
    "Filename",
    "Rank_Applied_For",
    "Status",
    "Notes",
    "Name",
    "Present_Rank",
    "Email",
    "Country",
    "Mobile_No",
]


def _normalize_value(value):
    return str(value or "").strip()


def _normalize_rank_counts(rows):
    out = {}
    for row in rows:
        rank = _normalize_value(row.get("Rank_Applied_For"))
        if not rank:
            continue
        out[rank] = int(row.get("count", 0) or 0)
    return out


def _df_to_candidate_map(df):
    mapping = {}
    if df is None or df.empty:
        return mapping
    for _, row in df.iterrows():
        cid = _normalize_value(row.get("Candidate_ID"))
        if not cid:
            continue
        payload = {}
        for field in COMPARE_FIELDS:
            payload[field] = _normalize_value(row.get(field))
        mapping[cid] = payload
    return mapping


def _build_parity_report(csv_repo, supabase_repo, rank_name="", sample_size=20, seed=42):
    csv_stats = csv_repo.get_csv_stats()
    sup_stats = supabase_repo.get_csv_stats()

    csv_latest = csv_repo.get_latest_status_per_candidate(rank_name)
    sup_latest = supabase_repo.get_latest_status_per_candidate(rank_name)

    csv_map = _df_to_candidate_map(csv_latest)
    sup_map = _df_to_candidate_map(sup_latest)

    csv_ids = set(csv_map.keys())
    sup_ids = set(sup_map.keys())
    common_ids = sorted(csv_ids & sup_ids)
    missing_in_supabase = sorted(csv_ids - sup_ids)
    missing_in_csv = sorted(sup_ids - csv_ids)

    rng = random.Random(seed)
    sample_ids = common_ids
    if len(sample_ids) > sample_size:
        sample_ids = sorted(rng.sample(sample_ids, sample_size))

    field_mismatches = []
    for cid in sample_ids:
        left = csv_map[cid]
        right = sup_map[cid]
        for field in COMPARE_FIELDS:
            if left.get(field, "") != right.get(field, ""):
                field_mismatches.append({
                    "candidate_id": cid,
                    "field": field,
                    "csv": left.get(field, ""),
                    "supabase": right.get(field, ""),
                })

    csv_rank_counts = _normalize_rank_counts(csv_repo.get_rank_counts())
    sup_rank_counts = _normalize_rank_counts(supabase_repo.get_rank_counts())
    rank_count_mismatches = []
    for rank in sorted(set(csv_rank_counts.keys()) | set(sup_rank_counts.keys())):
        left = csv_rank_counts.get(rank, 0)
        right = sup_rank_counts.get(rank, 0)
        if left != right:
            rank_count_mismatches.append({"rank": rank, "csv": left, "supabase": right})

    counts = {
        "csv": {
            "master_rows": int(csv_stats.get("master_csv_rows", 0) or 0),
            "latest_candidates": int(len(csv_ids)),
        },
        "supabase": {
            "event_rows": int(sup_stats.get("master_csv_rows", 0) or 0),
            "latest_candidates": int(len(sup_ids)),
        },
    }

    parity_ok = (
        not missing_in_supabase
        and not missing_in_csv
        and not field_mismatches
        and not rank_count_mismatches
    )

    return {
        "success": True,
        "parity_ok": parity_ok,
        "scope": {"rank_name": rank_name or "", "sample_size": sample_size},
        "counts": counts,
        "missing_candidate_ids": {
            "missing_in_supabase": missing_in_supabase,
            "missing_in_csv": missing_in_csv,
        },
        "spot_check": {
            "sampled_candidates": sample_ids,
            "field_mismatches": field_mismatches,
        },
        "rank_count_mismatches": rank_count_mismatches,
    }


def main():
    parser = argparse.ArgumentParser(description="CSV vs Supabase parity report for candidate events.")
    parser.add_argument("--base-folder", default="Verified_Resumes", help="CSV base folder path.")
    parser.add_argument("--server-url", default="http://127.0.0.1:5000", help="Server URL used in resume links.")
    parser.add_argument("--rank", default="", help="Optional rank filter (e.g. 2nd_Officer).")
    parser.add_argument("--sample-size", type=int, default=20, help="Spot-check sample size from common candidates.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic sampling.")
    parser.add_argument("--output", default="", help="Optional path to write JSON report.")
    args = parser.parse_args()

    if not can_enable_supabase_repo():
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for parity report.")
        return 2

    csv_repo = CSVCandidateEventRepo(base_folder=args.base_folder, server_url=args.server_url)
    sup_repo = SupabaseCandidateEventRepo(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        server_url=args.server_url,
    )

    report = _build_parity_report(
        csv_repo=csv_repo,
        supabase_repo=sup_repo,
        rank_name=args.rank,
        sample_size=max(1, args.sample_size),
        seed=args.seed,
    )

    rendered = json.dumps(report, indent=2)
    print(rendered)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved report to: {out_path}")

    return 0 if report.get("parity_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
