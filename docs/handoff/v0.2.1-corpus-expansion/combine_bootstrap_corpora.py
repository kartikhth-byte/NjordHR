#!/usr/bin/env python3
"""
Combine the two bootstrap prompt corpora into a single corpus file matching the
shape that scripts/bootstrap_prompt_corpus_eval.py expects.

Produces an artifact equivalent to
docs/eval-evidence/ai-search-bootstrap-solved-corpus-2026-06-09.json (175 prompts
combining the April 115-prompt corpus and the May 60-prompt corpus).

scripts/bootstrap_prompt_corpus_eval.py accepts only ONE --corpus argument, but
the v0.2 solved-set evidence is the combined 175-row report. Running the eval
against just the default April corpus would produce incomplete solved-set
coverage (only 115 rows). This combiner closes that gap.

Usage (from repo root):
    python3 combine_bootstrap_corpora.py \\
        --corpus docs/AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json \\
        --corpus docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json \\
        --output docs/eval-evidence/ai-search-bootstrap-solved-corpus-<date>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date as _date
from pathlib import Path


def _merge_families(base: dict, addition: dict) -> dict:
    """Merge addition['families'] into base['families'], concatenating per-family lists."""
    out_families = dict(base.get("families") or {})
    for family, rows in (addition.get("families") or {}).items():
        existing = out_families.get(family, [])
        out_families[family] = list(existing) + list(rows)
    return out_families


def _merge_unique_list(*lists) -> list:
    seen: set = set()
    out: list = []
    for lst in lists:
        for item in lst or []:
            key = json.dumps(item, sort_keys=True) if not isinstance(item, str) else item
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _walk_prompts(obj) -> int:
    if isinstance(obj, dict):
        if "prompt" in obj:
            return 1
        return sum(_walk_prompts(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_walk_prompts(x) for x in obj)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", action="append", required=True,
                    help="Path to a source bootstrap corpus JSON. Repeatable.")
    ap.add_argument("--output", required=True,
                    help="Path to write the combined corpus JSON.")
    ap.add_argument("--purpose", default=None,
                    help="Optional override for the 'purpose' field. Default merges source purposes.")
    args = ap.parse_args()

    if len(args.corpus) < 2:
        print("ERROR: need at least two --corpus arguments to merge.", file=sys.stderr)
        return 2

    corpora = []
    for path in args.corpus:
        p = Path(path)
        if not p.exists():
            print(f"ERROR: corpus not found: {path}", file=sys.stderr)
            return 2
        corpora.append((p, json.loads(p.read_text(encoding="utf-8"))))

    combined: dict = {
        "status": "combined_non_production_bootstrap_solved_set",
        "date": _date.today().isoformat(),
        "source_corpora": [str(p) for p, _ in corpora],
        "supported_families": _merge_unique_list(
            *(c.get("supported_families") for _, c in corpora)
        ),
        "threshold_families": _merge_unique_list(
            *(c.get("threshold_families") for _, c in corpora)
        ),
        "families": {},
    }
    purposes = _merge_unique_list(
        *(
            (c.get("purpose") if isinstance(c.get("purpose"), list) else [c.get("purpose")])
            for _, c in corpora
            if c.get("purpose")
        )
    )
    if args.purpose:
        combined["purpose"] = args.purpose
    elif purposes:
        combined["purpose"] = purposes

    families_acc: dict = {}
    for _, corpus in corpora:
        families_acc = _merge_families({"families": families_acc}, corpus)
    combined["families"] = families_acc

    combined["prompt_count"] = sum(len(rows) for rows in families_acc.values())
    combined["family_counts"] = {fam: len(rows) for fam, rows in families_acc.items()}

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"  source_corpora: {len(corpora)}")
    print(f"  prompt_count: {combined['prompt_count']}")
    print(f"  family_counts:")
    for fam, count in sorted(combined["family_counts"].items()):
        print(f"    {fam}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
