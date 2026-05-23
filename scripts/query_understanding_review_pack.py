#!/usr/bin/env python3
"""Build a lightweight query-understanding review pack.

This is a local workflow helper, not a production search path. It combines the
bootstrap prompt-corpus evaluation, the stored prompt-corpus review, and the
disabled shadow-audit output into a single JSON pack for review.
"""

import argparse
import configparser
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_analyzer import AIResumeAnalyzer, AdvancedPDFProcessor, ConfigManager
from query_understanding.shadow_audit import build_shadow_audit_rows
from query_understanding.shadow_llm_provider import build_shadow_llm_query_plan
from scripts.bootstrap_prompt_corpus_eval import _evaluate_corpus as evaluate_bootstrap_corpus
from scripts.prompt_corpus_review_report import _build_report as build_prompt_corpus_report, _load_rows as load_audit_rows


DEFAULT_BOOTSTRAP_CORPUS = PROJECT_ROOT / "docs" / "AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json"
DEFAULT_AUDIT_CSV = PROJECT_ROOT / "Verified_Resumes" / "ai_search_audit.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "AI_Search_Results" / "query_understanding_review_pack_current.json"


class _RegistryStub:
    def generate_resume_id(self, file_path):
        return Path(file_path).stem


def _build_analyzer():
    parser = configparser.ConfigParser()
    parser.read(PROJECT_ROOT / "config.ini")
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    analyzer.config = ConfigManager(parser)
    analyzer.registry = _RegistryStub()
    analyzer.pdf_processor = AdvancedPDFProcessor()
    analyzer._configured_ship_type_labels_cache = None
    return analyzer


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _build_shadow_audit(corpus: dict):
    analyzer = _build_analyzer()
    prompts = []
    for family, entries in (corpus.get("families") or {}).items():
        for index, entry in enumerate(entries, start=1):
            prompts.append(
                {
                    "prompt_id": f"{family}:{index}",
                    "prompt": entry.get("prompt"),
                    "family": family,
                    "expected_primary_family": entry.get("expected_primary_family"),
                }
            )
    return build_shadow_audit_rows(analyzer, prompts, llm_plan_provider=build_shadow_llm_query_plan)


def main():
    parser = argparse.ArgumentParser(description="Build a lightweight query-understanding review pack.")
    parser.add_argument("--bootstrap-corpus", default=str(DEFAULT_BOOTSTRAP_CORPUS), help="Path to bootstrap corpus JSON")
    parser.add_argument("--audit-csv", default=str(DEFAULT_AUDIT_CSV), help="Path to stored audit CSV")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write the review pack JSON")
    args = parser.parse_args()

    bootstrap_path = Path(args.bootstrap_corpus)
    audit_csv_path = Path(args.audit_csv)
    output_path = Path(args.output)

    if not bootstrap_path.exists():
        print(f"Bootstrap corpus not found: {bootstrap_path}")
        return 2

    bootstrap_corpus = _load_json(bootstrap_path)
    bootstrap_report = evaluate_bootstrap_corpus(bootstrap_corpus)

    if audit_csv_path.exists():
        audit_rows = load_audit_rows(audit_csv_path)
        prompt_corpus_report = build_prompt_corpus_report(audit_rows)
    else:
        audit_rows = []
        prompt_corpus_report = {
            "success": False,
            "audit_row_count": 0,
            "note": f"Audit CSV not found: {audit_csv_path}",
        }

    shadow_audit_rows = _build_shadow_audit(bootstrap_corpus)
    pack = {
        "success": True,
        "bootstrap_corpus_path": str(bootstrap_path),
        "audit_csv_path": str(audit_csv_path),
        "bootstrap_report": bootstrap_report,
        "prompt_corpus_report": prompt_corpus_report,
        "shadow_audit": {
            "shadow_mode": "disabled",
            "row_count": len(shadow_audit_rows),
            "rows": shadow_audit_rows,
        },
        "recommendation": (
            "Code review is a good next step once this pack is generated and the tests below stay green."
        ),
        "next_steps": [
            "Inspect the bootstrap coverage report for any degraded or unsupported families.",
            "Check the stored prompt-corpus report for real-prompt gaps.",
            "Review the shadow audit rows for canonical legacy output shape before enabling any LLM plan.",
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
