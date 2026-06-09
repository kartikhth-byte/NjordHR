#!/usr/bin/env python3
"""Score a shadow-audit run against the labeled prompt tail set and solved set.

Pure stdlib. No app credentials or heavy deps required — it only reads two JSON
files: the gold tail set and the harness output.

Inputs
------
--tail-set : the gold file (AI_Search_Results/seajobs_tail_set_v0.1.json), whose
             entries carry `prompt`, `expected_primary_family`, `expected_constraint`,
             and `current_parser` (miss | partial | wrong_family | unsupported_ok).
--eval     : the shadow-audit output produced by
             scripts/query_understanding_shadow_audit.py with the LLM enabled
             (NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1). Its `rows` are
             build_shadow_audit_entry records; each has `comparison_results` whose
             `llm_record` entries (status == "applied") tell us which families the
             normalizer proposed.

Two metrics that gate promotion
-------------------------------
1. rescue_rate (per family): of the prompts the regex parser currently fails
   (miss/partial/wrong_family), how many did the normalizer map to the expected
   family? This is the upside you promote *for*.
2. control_violations: of the qualitative control prompts that must stay
   unsupported, how many did the normalizer hallucinate an ACTIVE family for?
   This is the safety floor — it must be zero.
3. solved_set_regression: on a held-out set of prompts the regex parser already
   handles correctly, how many did the normalizer get wrong (different family,
   different values, or dropped a constraint)? This is the regression floor and
   must also be zero.

A family is "promote candidate" when rescue_rate is high, it contributed zero
control violations, AND it has zero solved-set regressions. Value-level correctness
is surfaced for human review, not auto-judged, because the plan payload shape
differs from the gold shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Families that are active hard filters in the catalogue. Hallucinating any of
# these on a control prompt is a violation. Mirrors
# query_understanding/hard_filter_catalog.ACTIVE_FAMILY_IDS (kept inline so this
# script has no import dependency on the app).
ACTIVE_FAMILY_IDS = {
    "age_range", "rank_match", "coc_document_gate", "coc_grade_match", "stcw_basic",
    "us_visa", "passport_validity", "recent_contract_vessel_experience",
    "engine_experience", "engine_vessel_experience", "company_continuity", "recency",
    "rank_duration_experience", "stcw_endorsement", "rank_certificate_expectation",
    "certificate_requirement", "experience_ship_type", "availability",
}

# Gold family id -> plan family id, where they differ.
GOLD_TO_PLAN_FAMILY = {
    "sea_service": "min_sea_service",   # catalogue models sea service as unsupported family
    "vessel_type": "vessel_type",
}

RESCUE_STATUSES = {"miss", "partial", "wrong_family"}
ACCEPTABLE_SOLVED_SET_OUTCOMES = {"equivalent", "expected_delta", "unsupported_family_delta", "legacy_missed"}


def _norm_prompt(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _load(path: str):
    p = Path(path)
    if not p.exists():
        sys.exit(f"File not found: {path}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def _llm_applied_families(row: dict) -> tuple[set, str]:
    """Return (set of families the LLM plan applied, evaluation_state).

    evaluation_state is 'evaluated' when the LLM actually ran, else 'not_evaluated'.
    """
    shadow_wiring = row.get("shadow_wiring") if isinstance(row.get("shadow_wiring"), dict) else {}
    if (
        str(row.get("shadow_mode")) != "enabled"
        or row.get("llm_plan") is None
        or bool(shadow_wiring.get("llm_plan_fallback_used"))
        or str(shadow_wiring.get("llm_plan_source") or "") == "legacy_fallback"
    ):
        return set(), "not_evaluated"
    applied = set()
    for result in row.get("comparison_results") or []:
        rec = result.get("llm_record")
        if isinstance(rec, dict) and rec.get("status") == "applied":
            fam = str(rec.get("family") or "")
            if fam:
                applied.add(fam)
    return applied, "evaluated"


def _llm_payload_for(row: dict, family: str):
    for result in row.get("comparison_results") or []:
        rec = result.get("llm_record")
        if isinstance(rec, dict) and str(rec.get("family")) == family and rec.get("status") == "applied":
            return rec.get("normalized_payload")
    return None


def _gold_rows(tail_set: dict):
    for family_bucket, entries in (tail_set.get("families") or {}).items():
        for entry in entries:
            yield family_bucket, entry


def _known_good_solved_rows(solved_report: dict):
    for family_name, summary in (solved_report.get("family_summaries") or {}).items():
        for entry in summary.get("rows") or []:
            if not entry.get("primary_family_matched"):
                continue
            if not entry.get("expected_family_present"):
                continue
            expected = str(entry.get("expected_primary_family") or "")
            if expected in {"", "unsupported", "unsupported_only", "mixed_supported", "parsing_notes_only", "unclassified"}:
                continue
            yield family_name, entry


def _expected_families(entry: dict) -> list:
    epf = str(entry.get("expected_primary_family") or "")
    if epf == "multi":
        keys = list((entry.get("expected_constraint") or {}).keys())
        return [GOLD_TO_PLAN_FAMILY.get(k, k) for k in keys]
    if epf in {"unsupported", ""}:
        return []
    return [GOLD_TO_PLAN_FAMILY.get(epf, epf)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Score a shadow-audit run against the labeled tail set.")
    ap.add_argument("--tail-set", default="AI_Search_Results/seajobs_tail_set_v0.1.json")
    ap.add_argument("--eval", required=True, help="shadow-audit output JSON (LLM enabled)")
    ap.add_argument(
        "--solved-set-report",
        default="",
        help="optional bootstrap-corpus evaluation report JSON for solved-set regression scoring",
    )
    ap.add_argument("--output", default="", help="optional path to write the summary JSON")
    ap.add_argument("--promote-threshold", type=float, default=0.8,
                    help="min rescue_rate for a family to be a promote candidate (default 0.8)")
    args = ap.parse_args()

    tail_set = _load(args.tail_set)
    evaluation = _load(args.eval)
    solved_report = _load(args.solved_set_report) if args.solved_set_report else None
    solved_gate_active = solved_report is not None

    rows_by_prompt = {}
    for row in evaluation.get("rows") or []:
        rows_by_prompt[_norm_prompt(row.get("prompt"))] = row

    # Per-family rescue tallies and a list of control violations.
    rescue = defaultdict(lambda: {"total": 0, "rescued": 0, "cases": []})
    controls = {"total": 0, "violations": 0, "cases": []}
    not_evaluated = []
    not_found = []
    solved = defaultdict(lambda: {"total": 0, "regressions": 0, "cases": []})
    solved_not_found = []
    solved_not_evaluated = []

    for bucket, entry in _gold_rows(tail_set):
        prompt = entry.get("prompt")
        key = _norm_prompt(prompt)
        row = rows_by_prompt.get(key)
        if row is None:
            not_found.append(prompt)
            continue
        applied, state = _llm_applied_families(row)
        if state == "not_evaluated":
            not_evaluated.append(prompt)
            continue

        status = str(entry.get("current_parser") or "")
        active_applied = {f for f in applied if f in ACTIVE_FAMILY_IDS}

        if status == "unsupported_ok":
            controls["total"] += 1
            if active_applied:
                controls["violations"] += 1
                controls["cases"].append({
                    "prompt": prompt,
                    "hallucinated_families": sorted(active_applied),
                })
            continue

        if status not in RESCUE_STATUSES:
            continue  # not a scored row

        expected = _expected_families(entry)
        # Use the first/primary expected family as the rescue bucket label.
        bucket_family = expected[0] if expected else (str(entry.get("expected_primary_family")) or bucket)
        rescued = bool(expected) and set(expected).issubset(applied)
        rescue[bucket_family]["total"] += 1
        if rescued:
            rescue[bucket_family]["rescued"] += 1
        rescue[bucket_family]["cases"].append({
            "prompt": prompt,
            "expected_families": expected,
            "llm_applied_families": sorted(applied),
            "family_rescued": rescued,
            "expected_constraint": entry.get("expected_constraint"),
            "llm_payload": {f: _llm_payload_for(row, f) for f in expected} if rescued else None,
            "value_match": "NEEDS_HUMAN_REVIEW" if rescued else "n/a",
        })

    if solved_report:
        for family_name, entry in _known_good_solved_rows(solved_report):
            prompt = entry.get("prompt")
            key = _norm_prompt(prompt)
            row = rows_by_prompt.get(key)
            if row is None:
                solved_not_found.append(prompt)
                continue
            applied, state = _llm_applied_families(row)
            if state == "not_evaluated":
                solved_not_evaluated.append(prompt)
                continue

            expected_family = GOLD_TO_PLAN_FAMILY.get(str(entry.get("expected_primary_family") or ""), str(entry.get("expected_primary_family") or ""))
            comparison_outcomes = [str(result.get("comparison_outcome") or "") for result in row.get("comparison_results") or []]
            regression = (not comparison_outcomes) or any(outcome not in ACCEPTABLE_SOLVED_SET_OUTCOMES for outcome in comparison_outcomes)
            solved[expected_family]["total"] += 1
            if regression:
                solved[expected_family]["regressions"] += 1
            solved[expected_family]["cases"].append({
                "prompt": prompt,
                "expected_family": expected_family,
                "legacy_applied_families": sorted(
                    {
                        str(record.get("family") or "")
                        for record in (row.get("legacy_comparison_records") or [])
                        if str(record.get("status") or "") == "applied" and str(record.get("family") or "")
                    }
                ),
                "llm_applied_families": sorted(applied),
                "comparison_outcomes": comparison_outcomes,
                "regression": regression,
                "expected_primary_family": entry.get("expected_primary_family"),
                "expected_constraint": entry.get("expected_constraint"),
            })

    # Build the report.
    family_summaries = {}
    promote_candidates = []
    global_solved_regressions = sum(tally["regressions"] for tally in solved.values())
    for fam, tally in sorted(rescue.items()):
        rate = (tally["rescued"] / tally["total"]) if tally["total"] else 0.0
        contributed_violation = any(
            fam in c["hallucinated_families"] for c in controls["cases"]
        )
        solved_tally = solved.get(fam) or {"total": 0, "regressions": 0, "cases": []}
        solved_regression = bool(solved_tally["regressions"])
        verdict = "promote_candidate" if (
            solved_gate_active
            and global_solved_regressions == 0
            and rate >= args.promote_threshold
            and not contributed_violation
            and not solved_regression
        ) else "hold"
        if verdict == "promote_candidate":
            promote_candidates.append(fam)
        family_summaries[fam] = {
            "rescued": tally["rescued"],
            "total": tally["total"],
            "rescue_rate": round(rate, 3),
            "contributed_control_violation": contributed_violation,
            "solved_set_regressions": solved_tally["regressions"],
            "solved_set_total": solved_tally["total"],
            "solved_set_regression_rate": round((solved_tally["regressions"] / solved_tally["total"]) if solved_tally["total"] else 0.0, 3),
            "verdict": verdict,
            "cases": tally["cases"],
            "solved_set_cases": solved_tally["cases"],
        }

    report = {
        "tail_set": str(args.tail_set),
        "eval": str(args.eval),
        "solved_set_report": str(args.solved_set_report) if args.solved_set_report else "",
        "promote_threshold": args.promote_threshold,
        "llm_run_detected": len(not_evaluated) == 0 and bool(rows_by_prompt),
        "rescue_by_family": family_summaries,
        "controls": controls,
        "solved_set": {
            "total": sum(tally["total"] for tally in solved.values()),
            "regressions": global_solved_regressions,
            "not_found": solved_not_found,
            "not_evaluated_llm_disabled": solved_not_evaluated,
            "cases_by_family": {fam: tally["cases"] for fam, tally in sorted(solved.items())},
        },
        "promote_candidates": promote_candidates,
        "rows_not_evaluated_llm_disabled": not_evaluated,
        "rows_not_found_in_eval": not_found,
        "solved_set_gate_active": solved_gate_active,
        "solved_set_gate_reason": "enabled" if solved_gate_active else "no solved-set report supplied; promotion held",
        "value_match_caveat": "family_rescued is automatic; value correctness is flagged NEEDS_HUMAN_REVIEW.",
    }

    # Console summary.
    print("=" * 72)
    if not report["llm_run_detected"]:
        print("WARNING: LLM does not appear to have run for some/all prompts.")
        print("  Re-run the harness with NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM=1 and a Gemini key in config.")
        if not_evaluated:
            print(f"  {len(not_evaluated)} prompt(s) had shadow_mode disabled / no llm_plan.")
    if not solved_gate_active:
        print("WARNING: solved-set report was not supplied; promotion is held.")
    print("RESCUE RATE BY FAMILY (prompts the regex parser currently fails)")
    print("-" * 72)
    for fam, s in family_summaries.items():
        flag = "  <-- promote candidate" if s["verdict"] == "promote_candidate" else ""
        viol = "  [!] contributed a control violation" if s["contributed_control_violation"] else ""
        reg = f"  solved-set regressions={s['solved_set_regressions']}/{s['solved_set_total']}" if args.solved_set_report else ""
        print(f"  {fam:34s} {s['rescued']:2d}/{s['total']:<2d}  rate={s['rescue_rate']:.2f}{flag}{viol}{reg}")
    print("-" * 72)
    print(f"CONTROLS (must stay unsupported): {controls['total'] - controls['violations']}/{controls['total']} clean, "
          f"{controls['violations']} violation(s)")
    for c in controls["cases"]:
        print(f"  [!] '{c['prompt']}' -> hallucinated {c['hallucinated_families']}")
    print("-" * 72)
    print(f"PROMOTE CANDIDATES (rescue>= {args.promote_threshold} and no control violation): "
          f"{promote_candidates or 'none yet'}")
    if args.solved_set_report:
        print("-" * 72)
        print(f"SOLVED SET (must have zero regressions): {report['solved_set']['regressions']}/{report['solved_set']['total']} regression(s)")
        if solved_not_found:
            print(f"NOTE: {len(solved_not_found)} solved-set prompt(s) not found in eval output (prompt-text mismatch).")
        if solved_not_evaluated:
            print(f"NOTE: {len(solved_not_evaluated)} solved-set prompt(s) were not evaluated by the LLM.")
    else:
        print("-" * 72)
        print("SOLVED SET: not supplied; promotion candidates are suppressed.")
    if not_found:
        print(f"NOTE: {len(not_found)} gold prompt(s) not found in eval output (prompt-text mismatch).")
    print("=" * 72)

    if args.output:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {outp}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
