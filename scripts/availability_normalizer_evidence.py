#!/usr/bin/env python3
"""Evaluate the availability shadow-normalizer evidence corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from query_understanding.compound_prompt_normalizer_evidence import evaluate_corpus_file, write_report


DEFAULT_CORPUS = Path("docs/eval-evidence/availability-shadow-normalizer-corpus-2026-06-29.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the availability shadow-normalizer evidence corpus.")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Path to the availability evidence corpus JSON.")
    parser.add_argument("--output", default="", help="Optional path for the evidence report JSON.")
    args = parser.parse_args()

    report = evaluate_corpus_file(Path(args.corpus))
    if args.output:
        write_report(report, Path(args.output))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
