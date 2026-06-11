#!/usr/bin/env python3
"""
Compare two shadow eval runs and produce a stability-comparison artifact in
the same shape as docs/eval-evidence/ai-search-tail-plus-solved-stability-comparison-2026-06-09.json.

Usage:
    python3 compare_eval_stability.py \\
        --run-1-eval  docs/eval-evidence/ai-search-v0.2.1-eval-<run1-date>.json \\
        --run-1-score docs/eval-evidence/ai-search-v0.2.1-score-<run1-date>.json \\
        --run-2-eval  docs/eval-evidence/ai-search-v0.2.1-eval-<run2-date>.json \\
        --run-2-score docs/eval-evidence/ai-search-v0.2.1-score-<run2-date>.json \\
        --output      docs/eval-evidence/ai-search-v0.2.1-stability-comparison-<date>.json \\
        --purpose     "Issue #19 calibration evidence: compare two full LLM shadow eval runs before any LLM_Promotion_Stage > 0 rollout."

Exit code:
    0 — stable_match (no deltas, score projections identical)
    1 — deltas detected
    2 — bad input / unreadable files

Output artifact shape (mirrors the existing 2026-06-09 stability artifact):

    {
        "status": "stability_rerun_complete",
        "date": "<ISO date>",
        "purpose": "<string>",
        "run_1": {"eval": "<path>", "score": "<path>", "integrity": {...}},
        "run_2": {"eval": "<path>", "score": "<path>", "integrity": {...}},
        "normalized_prompt_match": {"common": N, "missing_in_run_1": [...], "missing_in_run_2": [...]},
        "comparison_outcome_deltas": [...],
        "llm_payload_deltas": [...],
        "run_1_score_projection": {...},
        "run_2_score_projection": {...},
        "score_projection_match": bool,
        "verdict": "stable_match" | "unstable_deltas_detected"
    }

What counts as a delta:

- normalized_prompt_match.missing_in_run_*: prompts present in one eval but
  not the other after normalization. Should be empty.

- comparison_outcome_deltas: rows where shadow_audit_compare_to_legacy.outcome
  differs between runs (e.g., "shadow_match" vs "shadow_diverged" for the same
  prompt). Each entry: {prompt, run_1_outcome, run_2_outcome}.

- llm_payload_deltas: rows where the LLM plan (applied_constraints set + each
  family's normalized payload) differs between runs. Each entry: {prompt,
  diff_summary}. Float comparisons are exact — Flash Lite is expected to be
  deterministic at temperature=0; any drift is a real signal.

- score_projection_match: rescue_by_family + controls + promote_candidates
  identical between the two score JSONs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date as _date
from pathlib import Path


def _norm_prompt(text):
    return " ".join(str(text or "").lower().split())


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _integrity(eval_doc: dict) -> dict:
    """Summarise eval integrity per the existing artifact shape."""
    rows = eval_doc.get("rows") or []
    states = Counter()
    for row in rows:
        states[(
            row.get("shadow_mode"),
            row.get("llm_plan_source"),
            row.get("fallback_used"),
            (row.get("shadow_failure") or {}).get("reason", "ok") if isinstance(row.get("shadow_failure"), dict) else "ok",
        )] += 1
    states_list = [
        {
            "shadow_mode": k[0],
            "llm_plan_source": k[1],
            "fallback_used": k[2],
            "failure_reason": k[3],
            "count": v,
        }
        for k, v in states.items()
    ]
    all_llm_ok = all(
        s.get("shadow_mode") == "enabled"
        and s.get("llm_plan_source") == "llm"
        and not s.get("fallback_used")
        and s.get("failure_reason") == "ok"
        for s in states_list
    )
    return {
        "prompt_count": len(rows),
        "row_count": len(rows),
        "all_llm_ok": bool(all_llm_ok),
        "states": states_list,
    }


def _normalized_prompt_match(rows_1: list, rows_2: list) -> dict:
    set_1 = {_norm_prompt(r.get("prompt")) for r in rows_1}
    set_2 = {_norm_prompt(r.get("prompt")) for r in rows_2}
    return {
        "common": len(set_1 & set_2),
        "missing_in_run_1": sorted(set_2 - set_1),
        "missing_in_run_2": sorted(set_1 - set_2),
    }


def _llm_payload_snapshot(row: dict) -> dict:
    """Project the LLM-decisive fields from a row for comparison.

    Compares the LLM plan's applied_constraints set and each constraint's
    canonical payload. Order-independent for applied_constraints.
    """
    llm_plan = row.get("llm_plan") or {}
    applied = sorted(
        (
            (item.get("id"), json.dumps(item.get("constraint") or {}, sort_keys=True))
            for item in (llm_plan.get("applied_constraints") or [])
            if isinstance(item, dict)
        ),
        key=lambda pair: pair[0] or "",
    )
    unapplied = sorted(
        item.get("id") or "" for item in (llm_plan.get("unapplied_constraints") or [])
        if isinstance(item, dict)
    )
    return {
        "applied": applied,
        "unapplied": unapplied,
    }


def _comparison_outcome(row: dict):
    cmp = row.get("comparison") or row.get("shadow_audit_compare_to_legacy") or {}
    if isinstance(cmp, dict):
        return cmp.get("outcome") or cmp.get("comparison_outcome")
    return None


def _compute_deltas(rows_1: list, rows_2: list):
    by_prompt_1 = {_norm_prompt(r.get("prompt")): r for r in rows_1}
    by_prompt_2 = {_norm_prompt(r.get("prompt")): r for r in rows_2}
    common = sorted(by_prompt_1.keys() & by_prompt_2.keys())

    comparison_outcome_deltas = []
    llm_payload_deltas = []

    for prompt in common:
        r1 = by_prompt_1[prompt]
        r2 = by_prompt_2[prompt]
        out_1 = _comparison_outcome(r1)
        out_2 = _comparison_outcome(r2)
        if out_1 != out_2:
            comparison_outcome_deltas.append({
                "prompt": prompt,
                "run_1_outcome": out_1,
                "run_2_outcome": out_2,
            })

        p1 = _llm_payload_snapshot(r1)
        p2 = _llm_payload_snapshot(r2)
        if p1 != p2:
            llm_payload_deltas.append({
                "prompt": prompt,
                "applied_run_1": [pair[0] for pair in p1["applied"]],
                "applied_run_2": [pair[0] for pair in p2["applied"]],
                "unapplied_run_1": p1["unapplied"],
                "unapplied_run_2": p2["unapplied"],
                "diff_note": "applied_constraints set or per-family payload differs",
            })

    return comparison_outcome_deltas, llm_payload_deltas


def _score_projection(score_doc: dict) -> dict:
    """Project the score fields that matter for stability comparison.

    Reads the actual current keys produced by scripts/tail_set_score.py:
        - rescue_by_family
        - controls
        - solved_set
        - promote_candidates (if present at top level)
    """
    rescue = score_doc.get("rescue_by_family") or {}
    controls = score_doc.get("controls") or {}
    solved_set = score_doc.get("solved_set") or {}
    promote_candidates = score_doc.get("promote_candidates") or sorted(
        fam
        for fam, tally in rescue.items()
        if isinstance(tally, dict)
        and tally.get("rescue_rate", 0) >= 0.8
        and not tally.get("solved_set_regressions", 0)
        and not any(case for case in (controls.get("cases") or []) if fam in case.get("hallucinated_families", []))
    )
    return {
        "rescue_by_family": {
            fam: {
                "rescued": tally.get("rescued"),
                "total": tally.get("total"),
                "rescue_rate": tally.get("rescue_rate"),
                "solved_set_regressions": tally.get("solved_set_regressions"),
                "solved_set_total": tally.get("solved_set_total"),
            }
            for fam, tally in rescue.items()
            if isinstance(tally, dict)
        },
        "controls": {
            "total": controls.get("total"),
            "violations": controls.get("violations"),
            "cases": controls.get("cases") or [],
        },
        "solved_set": {
            "total": solved_set.get("total"),
            "regressions": solved_set.get("regressions"),
        },
        "promote_candidates": promote_candidates,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-1-eval", required=True)
    ap.add_argument("--run-1-score", required=True)
    ap.add_argument("--run-2-eval", required=True)
    ap.add_argument("--run-2-score", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--purpose", default="Stability rerun comparison between two consecutive shadow eval runs.")
    args = ap.parse_args()

    eval_1 = _load_json(Path(args.run_1_eval))
    score_1 = _load_json(Path(args.run_1_score))
    eval_2 = _load_json(Path(args.run_2_eval))
    score_2 = _load_json(Path(args.run_2_score))

    rows_1 = eval_1.get("rows") or []
    rows_2 = eval_2.get("rows") or []

    integrity_1 = _integrity(eval_1)
    integrity_2 = _integrity(eval_2)

    npm = _normalized_prompt_match(rows_1, rows_2)
    comparison_deltas, payload_deltas = _compute_deltas(rows_1, rows_2)

    score_projection_1 = _score_projection(score_1)
    score_projection_2 = _score_projection(score_2)
    score_projection_match = score_projection_1 == score_projection_2

    deltas_empty = (
        not npm["missing_in_run_1"]
        and not npm["missing_in_run_2"]
        and not comparison_deltas
        and not payload_deltas
    )
    verdict = "stable_match" if deltas_empty and score_projection_match else "unstable_deltas_detected"

    artifact = {
        "status": "stability_rerun_complete",
        "date": _date.today().isoformat(),
        "purpose": args.purpose,
        "run_1": {
            "eval": args.run_1_eval,
            "score": args.run_1_score,
            "integrity": integrity_1,
        },
        "run_2": {
            "eval": args.run_2_eval,
            "score": args.run_2_score,
            "integrity": integrity_2,
        },
        "normalized_prompt_match": npm,
        "comparison_outcome_deltas": comparison_deltas,
        "llm_payload_deltas": payload_deltas,
        "run_1_score_projection": score_projection_1,
        "run_2_score_projection": score_projection_2,
        "score_projection_match": score_projection_match,
        "verdict": verdict,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Verdict: {verdict}")
    print(f"  normalized_prompt_match.common: {npm['common']}")
    print(f"  missing_in_run_1: {len(npm['missing_in_run_1'])}")
    print(f"  missing_in_run_2: {len(npm['missing_in_run_2'])}")
    print(f"  comparison_outcome_deltas: {len(comparison_deltas)}")
    print(f"  llm_payload_deltas: {len(payload_deltas)}")
    print(f"  score_projection_match: {score_projection_match}")

    return 0 if verdict == "stable_match" else 1


if __name__ == "__main__":
    sys.exit(main())
