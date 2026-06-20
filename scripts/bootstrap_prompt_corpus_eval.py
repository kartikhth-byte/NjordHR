#!/usr/bin/env python3
"""
Evaluate the non-production bootstrap prompt corpus against the current parser.

Usage:
  python3 scripts/bootstrap_prompt_corpus_eval.py
  python3 scripts/bootstrap_prompt_corpus_eval.py --output AI_Search_Results/bootstrap_prompt_corpus_eval_current.json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_analyzer import AIResumeAnalyzer


DEFAULT_CORPUS_PATH = PROJECT_ROOT / "docs" / "AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json"
DEFAULT_THRESHOLD_FAMILIES = [
    "age_range",
    "us_visa",
    "rank_match",
    "coc_document_gate",
    "stcw_basic",
]


def _supported_families(corpus):
    configured = corpus.get("supported_families")
    if configured:
        return set(configured)
    return set(DEFAULT_THRESHOLD_FAMILIES)


def _threshold_families(corpus):
    configured = corpus.get("threshold_families")
    if configured:
        return list(configured)
    return list(DEFAULT_THRESHOLD_FAMILIES)


def _primary_family(constraints, supported_families):
    core_supported = [family for family in constraints["applied_constraints"] if family in supported_families]
    if len(core_supported) == 1:
        return core_supported[0]
    if len(core_supported) > 1:
        return "mixed_supported"
    if constraints["unapplied_constraints"]:
        return "unsupported_only"
    if constraints["parsing_notes"]:
        return "parsing_notes_only"
    return "unclassified"


def _family_present(constraints, expected):
    return expected in constraints["applied_constraints"] or expected in constraints["unapplied_constraints"]


def _presence_expectation_applies(expected):
    return expected not in {"unsupported_only", "parsing_notes_only", "mixed_supported", "unclassified"}


def _evaluate_corpus(corpus):
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    supported_families = _supported_families(corpus)
    family_summaries = {}
    overall_counts = Counter()
    mismatches = []

    for family_name, prompts in corpus["families"].items():
        family_total = len(prompts)
        primary_match = 0
        presence_match = 0
        rows = []

        for entry in prompts:
            prompt = entry["prompt"]
            expected = entry["expected_primary_family"]
            constraints = analyzer._extract_job_constraints(prompt)
            actual = _primary_family(constraints, supported_families)
            primary_matched = actual == expected
            present_matched = (
                _family_present(constraints, expected)
                if _presence_expectation_applies(expected)
                else primary_matched
            )
            if primary_matched:
                primary_match += 1
            if present_matched:
                presence_match += 1
            if not present_matched:
                mismatches.append({
                    "family_name": family_name,
                    "prompt": prompt,
                    "expected_primary_family": expected,
                    "actual_primary_family": actual,
                    "parser_view": constraints,
                })

            rows.append({
                "prompt": prompt,
                "expected_primary_family": expected,
                "actual_primary_family": actual,
                "primary_family_matched": primary_matched,
                "expected_family_present": present_matched,
                "parser_view": constraints,
            })
            overall_counts[actual] += 1

        family_summaries[family_name] = {
            "prompt_count": family_total,
            "primary_match_count": primary_match,
            "primary_match_ratio": round(primary_match / family_total, 4) if family_total else 0.0,
            "expected_family_present_count": presence_match,
            "expected_family_present_ratio": round(presence_match / family_total, 4) if family_total else 0.0,
            "rows": rows,
        }

    threshold_status = {}
    for family_name in _threshold_families(corpus):
        family_prompt_count = sum(
            1
            for prompts in corpus["families"].values()
            for entry in prompts
            if entry["expected_primary_family"] == family_name
        )
        family_rows = [
            row
            for summary in family_summaries.values()
            for row in summary["rows"]
            if row["expected_primary_family"] == family_name
        ]
        primary_matches = sum(1 for row in family_rows if row["primary_family_matched"])
        presence_matches = sum(1 for row in family_rows if row["expected_family_present"])
        threshold_status[family_name] = {
            "bootstrap_prompt_count": family_prompt_count,
            "meets_20_prompt_gate": family_prompt_count >= 20,
            "primary_family_match_ratio": round(primary_matches / family_prompt_count, 4) if family_prompt_count else 0.0,
            "expected_family_present_ratio": round(presence_matches / family_prompt_count, 4) if family_prompt_count else 0.0,
        }

    return {
        "success": True,
        "corpus_status": corpus.get("status"),
        "corpus_date": corpus.get("date"),
        "overall_actual_primary_family_counts": dict(overall_counts),
        "threshold_status": threshold_status,
        "family_summaries": family_summaries,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate the bootstrap prompt corpus against the current parser.")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH), help="Path to bootstrap prompt corpus JSON")
    parser.add_argument("--output", default="", help="Optional path to write JSON output")
    args = parser.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"Corpus file not found: {corpus_path}")
        return 2

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    report = _evaluate_corpus(corpus)
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
