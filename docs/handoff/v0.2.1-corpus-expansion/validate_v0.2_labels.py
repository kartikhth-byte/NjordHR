#!/usr/bin/env python3
"""
Validate label correctness in seajobs_tail_set_v0.2.json against the legacy parser.

Runs every prompt through the corresponding `_extract_<family>_constraint` on the
current main HEAD and checks the row's `current_parser` label against what the
parser actually does.

Usage (from repo root):
    python3 validate_v0.2_labels.py AI_Search_Results/seajobs_tail_set_v0.2.json

Exit code:
    0 — all labels match parser behavior
    1 — at least one mismatch (review report and fix labels before running eval)
    2 — file unreadable / malformed

Output goes to stdout. A sidecar JSON `<file>.validation_report.json` is also
written for diff-friendly reuse.

Detection logic per current_parser value:

  miss:           parser must return None / falsy
                  if parser fires -> MISLABEL_MISS (row belongs in solved-set
                  or should be 'partial')

  partial:        parser must return non-None (something fired)
                  if parser returns None -> MISLABEL_PARTIAL_IS_MISS

  wrong_family:   parser MAY fire on this prompt for the *wrong* family;
                  hard to detect mechanically — we just warn that the row exists
                  and report the parser's output for human review.

  unsupported_ok: controls. Legacy may incorrectly fire here (the row exists
                  exactly because the shadow LLM with anchor veto must NOT fire).
                  Report which families legacy fires on for awareness, but do
                  NOT flag as mislabel — the row's label is about shadow
                  behavior, not legacy.

For multi-family rows (expected_primary_family='multi'), the script runs every
mapped extractor and checks that AT LEAST ONE family fires (for 'partial') or
NONE fires (for 'miss'). It does NOT validate per-key correctness — that
requires the full eval.

For new buckets that don't have a mapped extractor (e.g.,
recent_contract_vessel_experience), the row is reported as INFO_UNVALIDATED_BUCKET.

Cannot detect:
  - 'partial' rows where parser fires the correct family but my
    expected_constraint shape is wrong. Human review still needed.
  - Convention calls (e.g., '1 year -> 12 months') — only the eval can catch
    these via comparison against the shadow plan.
"""

from __future__ import annotations

import json
import sys
import types
from collections import defaultdict
from pathlib import Path


def _stub_ai_dependencies():
    """Stub out the heavy AI deps so we can import ai_analyzer without them."""
    if "fitz" not in sys.modules:
        sys.modules["fitz"] = types.ModuleType("fitz")
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        image = types.ModuleType("PIL.Image")
        pil.Image = image
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = image
    if "pinecone" not in sys.modules:
        pc = types.ModuleType("pinecone")
        class _Stub:
            def __init__(self, *_a, **_k): pass
        pc.Pinecone = _Stub
        pc.ServerlessSpec = _Stub
        sys.modules["pinecone"] = pc


_stub_ai_dependencies()
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, ".")

from ai_analyzer import AIResumeAnalyzer  # noqa: E402


# Map family id in the corpus -> bound legacy extractor method name on the analyzer.
# Family ids that don't have a direct extractor (multi, recent_contract_vessel_experience)
# are handled separately — the multi case runs all extractors; new buckets are reported
# as INFO_UNVALIDATED_BUCKET.
FAMILY_TO_EXTRACTOR = {
    "passport_validity": "_extract_passport_validity_constraint",
    "age_range": "_extract_age_constraint",
    "us_visa": "_extract_us_visa_constraint",
    "stcw_basic": "_extract_stcw_basic_constraint",
    "certificate_requirement": "_extract_coc_requirement_constraint",
    "rank_match": "_extract_rank_constraint",
}


def _run_full_pipeline(analyzer, prompt):
    """Run the full _extract_job_constraints pipeline. Returns the constraints dict."""
    try:
        return analyzer._extract_job_constraints(prompt, rank=None)
    except Exception as exc:
        return {"_exception": str(exc)}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_v0.2_labels.py <tail_set.json>", file=sys.stderr)
        return 2

    corpus_path = Path(sys.argv[1])
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"could not read {corpus_path}: {exc}", file=sys.stderr)
        return 2

    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    rescue_extractors = {
        fam: getattr(analyzer, name) for fam, name in FAMILY_TO_EXTRACTOR.items()
    }

    findings = []
    stats: dict[str, int] = defaultdict(int)

    families_dict = corpus.get("families") or {}
    for bucket_name, rows in families_dict.items():
        for row_idx, row in enumerate(rows):
            prompt = str(row.get("prompt") or "")
            current_parser = str(row.get("current_parser") or "")
            expected_primary = str(row.get("expected_primary_family") or "")

            stats["total"] += 1

            if bucket_name == "controls":
                # For controls, run all extractors; report which fire (for awareness).
                fires = {}
                for fam, extractor in rescue_extractors.items():
                    try:
                        result = extractor(prompt)
                    except Exception as exc:
                        result = f"<exception: {exc}>"
                    if result:
                        fires[fam] = result
                if fires:
                    stats["controls_legacy_fires"] += 1
                    findings.append({
                        "bucket": bucket_name,
                        "row_idx": row_idx,
                        "prompt": prompt,
                        "current_parser": current_parser,
                        "type": "INFO_CONTROL_LEGACY_FIRES",
                        "fires_on_families": list(fires.keys()),
                        "parser_outputs": {fam: str(out)[:200] for fam, out in fires.items()},
                        "note": "Control row: legacy parser fires here. Shadow LLM + anchor veto must NOT. Not a label error.",
                    })
                else:
                    stats["controls_legacy_clean"] += 1
                continue

            # Multi-family rows: run full pipeline, check that families fire as expected
            if expected_primary == "multi":
                full_result = _run_full_pipeline(analyzer, prompt)
                if "_exception" in full_result:
                    findings.append({
                        "bucket": bucket_name,
                        "row_idx": row_idx,
                        "prompt": prompt,
                        "current_parser": current_parser,
                        "type": "EXTRACTOR_EXCEPTION",
                        "exception": full_result["_exception"],
                    })
                    stats["extractor_exception"] += 1
                    continue
                applied = full_result.get("applied_constraints") or []
                expected_families = list((row.get("expected_constraint") or {}).keys())
                hits = [f for f in expected_families if f in applied]
                if current_parser == "miss":
                    if hits:
                        stats["mislabel_miss"] += 1
                        findings.append({
                            "bucket": bucket_name,
                            "row_idx": row_idx,
                            "prompt": prompt,
                            "current_parser": current_parser,
                            "type": "MISLABEL_MISS_PARSER_FIRES",
                            "expected_families": expected_families,
                            "legacy_applied": applied,
                            "hits": hits,
                            "note": "Multi-family row labeled 'miss' but legacy applied at least one expected family. Relabel as 'partial'.",
                        })
                    else:
                        stats["ok_miss_multi"] += 1
                elif current_parser == "partial":
                    if not hits:
                        stats["mislabel_partial"] += 1
                        findings.append({
                            "bucket": bucket_name,
                            "row_idx": row_idx,
                            "prompt": prompt,
                            "current_parser": current_parser,
                            "type": "MISLABEL_PARTIAL_IS_MISS",
                            "expected_families": expected_families,
                            "legacy_applied": applied,
                            "note": "Multi-family row labeled 'partial' but legacy applied none of the expected families. Relabel as 'miss'.",
                        })
                    else:
                        stats["ok_partial_multi"] += 1
                        findings.append({
                            "bucket": bucket_name,
                            "row_idx": row_idx,
                            "prompt": prompt,
                            "current_parser": current_parser,
                            "type": "INFO_MULTI_PARTIAL_OUTPUT",
                            "expected_families": expected_families,
                            "legacy_applied": applied,
                            "hits": hits,
                            "missing_from_legacy": [f for f in expected_families if f not in applied],
                            "note": "Compare hits vs expected_families. If hits == expected_families, row may be solved-set, not partial.",
                        })
                continue

            # Rescue rows with a single-family extractor.
            extractor = rescue_extractors.get(bucket_name)
            if extractor is None:
                findings.append({
                    "bucket": bucket_name,
                    "row_idx": row_idx,
                    "prompt": prompt,
                    "type": "INFO_UNVALIDATED_BUCKET",
                    "note": f"No extractor mapped for family '{bucket_name}'. Validate via the full _extract_job_constraints pipeline manually; see HANDOFF §pre-splice-checks.",
                })
                stats["unvalidated_bucket"] += 1
                continue

            try:
                actual = extractor(prompt)
            except Exception as exc:
                findings.append({
                    "bucket": bucket_name,
                    "row_idx": row_idx,
                    "prompt": prompt,
                    "current_parser": current_parser,
                    "type": "EXTRACTOR_EXCEPTION",
                    "exception": str(exc),
                })
                stats["extractor_exception"] += 1
                continue

            if current_parser == "miss":
                if actual is not None and actual is not False:
                    stats["mislabel_miss"] += 1
                    findings.append({
                        "bucket": bucket_name,
                        "row_idx": row_idx,
                        "prompt": prompt,
                        "current_parser": current_parser,
                        "type": "MISLABEL_MISS_PARSER_FIRES",
                        "parser_output": str(actual)[:300],
                        "note": "Labeled 'miss' but legacy parser returned a non-empty result. Reconsider: belongs in solved-set, or relabel as 'partial'.",
                    })
                else:
                    stats["ok_miss"] += 1
            elif current_parser == "partial":
                if actual is None or actual is False:
                    stats["mislabel_partial"] += 1
                    findings.append({
                        "bucket": bucket_name,
                        "row_idx": row_idx,
                        "prompt": prompt,
                        "current_parser": current_parser,
                        "type": "MISLABEL_PARTIAL_IS_MISS",
                        "parser_output": None,
                        "note": "Labeled 'partial' but legacy returned None. Relabel as 'miss'.",
                    })
                else:
                    stats["ok_partial"] += 1
                    findings.append({
                        "bucket": bucket_name,
                        "row_idx": row_idx,
                        "prompt": prompt,
                        "current_parser": current_parser,
                        "type": "INFO_PARTIAL_PARSER_OUTPUT",
                        "parser_output": str(actual)[:300],
                        "expected_constraint": row.get("expected_constraint"),
                        "note": "Compare parser_output against expected_constraint to confirm 'partial' captures the right family with missing fields.",
                    })
            elif current_parser == "wrong_family":
                stats["info_wrong_family"] += 1
                fires_elsewhere = {}
                for fam, ex in rescue_extractors.items():
                    if fam == bucket_name:
                        continue
                    try:
                        r = ex(prompt)
                        if r:
                            fires_elsewhere[fam] = r
                    except Exception:
                        pass
                findings.append({
                    "bucket": bucket_name,
                    "row_idx": row_idx,
                    "prompt": prompt,
                    "current_parser": current_parser,
                    "type": "INFO_WRONG_FAMILY",
                    "primary_family_output": str(actual)[:200] if actual else None,
                    "other_families_firing": {fam: str(out)[:200] for fam, out in fires_elsewhere.items()},
                    "note": "Human review: confirm at least one family fires incorrectly. expected_primary_family should be 'unsupported' for these rows.",
                })
            else:
                stats["unknown_current_parser"] += 1
                findings.append({
                    "bucket": bucket_name,
                    "row_idx": row_idx,
                    "prompt": prompt,
                    "type": "UNKNOWN_CURRENT_PARSER_VALUE",
                    "current_parser": current_parser,
                    "note": "current_parser must be one of: miss, partial, wrong_family, unsupported_ok.",
                })

    # Write sidecar.
    sidecar = corpus_path.with_suffix(corpus_path.suffix + ".validation_report.json")
    sidecar.write_text(json.dumps({"stats": dict(stats), "findings": findings}, indent=2), encoding="utf-8")

    # Console summary.
    print(f"Scanned {corpus_path}")
    print(f"Total rows: {stats['total']}")
    print()
    print("Counts:")
    for k in sorted(stats):
        print(f"  {stats[k]:>5}  {k}")
    print()
    print(f"Sidecar with full findings: {sidecar}")

    label_errors = stats.get("mislabel_miss", 0) + stats.get("mislabel_partial", 0)
    if label_errors:
        print()
        print(f"!!! {label_errors} label errors detected. Showing first 8:")
        shown = 0
        for f in findings:
            if f["type"] in {"MISLABEL_MISS_PARSER_FIRES", "MISLABEL_PARTIAL_IS_MISS"}:
                print()
                print(f"  [{f['type']}] {f['bucket']}[{f['row_idx']}]")
                print(f"    prompt: {f['prompt']!r}")
                print(f"    label:  {f['current_parser']}")
                if "parser_output" in f:
                    print(f"    parser_output: {f['parser_output']}")
                if "note" in f:
                    print(f"    -> {f['note']}")
                shown += 1
                if shown >= 8:
                    if label_errors > 8:
                        print(f"  (… {label_errors - 8} more in sidecar)")
                    break
        return 1

    print()
    print("All labels match legacy parser behavior. Safe to proceed to shadow eval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
