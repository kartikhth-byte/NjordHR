#!/usr/bin/env python3
"""
Build a prompt-corpus review report from stored AI search audit rows.

Usage:
  python3 scripts/prompt_corpus_review_report.py
  python3 scripts/prompt_corpus_review_report.py --audit-csv Verified_Resumes/ai_search_audit.csv
  python3 scripts/prompt_corpus_review_report.py --output AI_Search_Results/prompt_corpus_review_current.json
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_analyzer import AIResumeAnalyzer


CORE_SUPPORTED_FAMILIES = [
    "age_range",
    "us_visa",
    "rank_match",
    "coc_document_gate",
    "stcw_basic",
]


def _load_rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _build_prompt_sessions(rows):
    sessions = {}
    for row in rows:
        prompt = str(row.get("AI_Search_Prompt", "") or "").strip()
        if not prompt:
            continue

        search_session_id = str(row.get("Search_Session_ID", "") or "").strip()
        key = (search_session_id, prompt)
        entry = sessions.setdefault(
            key,
            {
                "search_session_id": search_session_id,
                "prompt": prompt,
                "rank_folders": set(),
                "applied_ship_type_filters": set(),
                "experienced_ship_type_filters": set(),
                "hard_filter_decisions": Counter(),
                "result_buckets": Counter(),
                "candidate_rows": 0,
            },
        )
        rank_folder = str(row.get("Rank_Applied_For", "") or "").strip()
        if rank_folder:
            entry["rank_folders"].add(rank_folder)
        applied_ship_type = str(row.get("Applied_Ship_Type_Filter", "") or "").strip()
        if applied_ship_type:
            entry["applied_ship_type_filters"].add(applied_ship_type)
        experienced_ship_type = str(row.get("Experienced_Ship_Type_Filter", "") or "").strip()
        if experienced_ship_type:
            entry["experienced_ship_type_filters"].add(experienced_ship_type)
        entry["hard_filter_decisions"][str(row.get("Hard_Filter_Decision", "") or "").strip() or "UNKNOWN"] += 1
        entry["result_buckets"][str(row.get("Result_Bucket", "") or "").strip() or "unknown"] += 1
        entry["candidate_rows"] += 1

    normalized = []
    for entry in sessions.values():
        normalized.append(
            {
                "search_session_id": entry["search_session_id"],
                "prompt": entry["prompt"],
                "rank_folders": sorted(entry["rank_folders"]),
                "applied_ship_type_filters": sorted(entry["applied_ship_type_filters"]),
                "experienced_ship_type_filters": sorted(entry["experienced_ship_type_filters"]),
                "hard_filter_decisions": dict(entry["hard_filter_decisions"]),
                "result_buckets": dict(entry["result_buckets"]),
                "candidate_rows": entry["candidate_rows"],
            }
        )
    return normalized


def _family_counts(prompt_entries):
    supported_counts = Counter()
    unsupported_counts = Counter()
    mixed_supported = []
    no_supported = 0

    for entry in prompt_entries:
        applied = entry["parser_view"]["applied_constraints"]
        unapplied = entry["parser_view"]["unapplied_constraints"]
        supported = [family for family in applied if family in CORE_SUPPORTED_FAMILIES]
        if len(supported) == 1:
            supported_counts[supported[0]] += 1
        elif len(supported) > 1:
            mixed_supported.append(entry["prompt"])
        else:
            no_supported += 1

        for family in unapplied:
            unsupported_counts[family] += 1

    return supported_counts, unsupported_counts, mixed_supported, no_supported


def _classify_prompts(prompt_entries):
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    classified = []
    for entry in prompt_entries:
        constraints = analyzer._extract_job_constraints(entry["prompt"])
        supported = [family for family in constraints["applied_constraints"] if family in CORE_SUPPORTED_FAMILIES]
        if len(supported) == 1:
            primary_family = supported[0]
        elif len(supported) > 1:
            primary_family = "mixed_supported"
        elif constraints["unapplied_constraints"]:
            primary_family = "unsupported_only"
        elif constraints["parsing_notes"]:
            primary_family = "parsing_notes_only"
        else:
            primary_family = "unclassified"

        classified.append(
            {
                **entry,
                "primary_family": primary_family,
                "parser_view": {
                    "applied_constraints": constraints["applied_constraints"],
                    "unapplied_constraints": constraints["unapplied_constraints"],
                    "parsing_notes": constraints["parsing_notes"],
                },
            }
        )
    return classified


def _build_report(rows):
    prompt_entries = _classify_prompts(_build_prompt_sessions(rows))
    supported_counts, unsupported_counts, mixed_supported, no_supported = _family_counts(prompt_entries)
    primary_family_counts = Counter(entry["primary_family"] for entry in prompt_entries)
    threshold_status = {
        family: {
            "stored_prompt_count": supported_counts.get(family, 0),
            "meets_20_prompt_gate": supported_counts.get(family, 0) >= 20,
        }
        for family in CORE_SUPPORTED_FAMILIES
    }

    prompts_by_family = defaultdict(list)
    for entry in sorted(prompt_entries, key=lambda item: (item["primary_family"], item["prompt"].lower())):
        prompts_by_family[entry["primary_family"]].append(
            {
                "prompt": entry["prompt"],
                "search_session_id": entry["search_session_id"],
                "rank_folders": entry["rank_folders"],
                "applied_ship_type_filters": entry["applied_ship_type_filters"],
                "experienced_ship_type_filters": entry["experienced_ship_type_filters"],
                "parser_view": entry["parser_view"],
                "hard_filter_decisions": entry["hard_filter_decisions"],
                "result_buckets": entry["result_buckets"],
                "candidate_rows": entry["candidate_rows"],
            }
        )

    return {
        "success": True,
        "audit_row_count": len(rows),
        "stored_prompt_count": len(prompt_entries),
        "core_supported_families": CORE_SUPPORTED_FAMILIES,
        "primary_family_counts": dict(primary_family_counts),
        "supported_family_counts_for_20_prompt_gate": dict(supported_counts),
        "unsupported_family_counts": dict(unsupported_counts),
        "threshold_status": threshold_status,
        "mixed_supported_prompt_count": len(mixed_supported),
        "mixed_supported_prompt_examples": mixed_supported[:20],
        "no_supported_family_prompt_count": no_supported,
        "prompts_by_family": dict(prompts_by_family),
    }


def main():
    parser = argparse.ArgumentParser(description="Build a prompt-corpus review report from stored AI search audit rows.")
    parser.add_argument(
        "--audit-csv",
        default=str(PROJECT_ROOT / "Verified_Resumes" / "ai_search_audit.csv"),
        help="Path to ai_search_audit.csv",
    )
    parser.add_argument("--output", default="", help="Optional path to write JSON output")
    args = parser.parse_args()

    audit_path = Path(args.audit_csv)
    if not audit_path.exists():
        print(f"Audit CSV not found: {audit_path}")
        return 2

    rows = _load_rows(audit_path)
    report = _build_report(rows)
    rendered = json.dumps(report, indent=2)
    print(rendered)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved report to: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
