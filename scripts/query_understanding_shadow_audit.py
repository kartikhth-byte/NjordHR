#!/usr/bin/env python3
"""Emit a disabled shadow audit for the bootstrap prompt corpus.

This script logs the canonical legacy query-plan records now so future LLM
outputs can be compared against the same corpus without changing production
search behavior.
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


DEFAULT_CORPUS_PATH = PROJECT_ROOT / "docs" / "AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "AI_Search_Results" / "query_understanding_shadow_audit_current.json"


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


def _load_corpus(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Emit disabled shadow-audit rows for the bootstrap prompt corpus.")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH), help="Path to bootstrap prompt corpus JSON")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write the JSON report")
    args = parser.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"Corpus file not found: {corpus_path}")
        return 2

    corpus = _load_corpus(corpus_path)
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

    rows = build_shadow_audit_rows(analyzer, prompts, llm_plan_provider=build_shadow_llm_query_plan)
    report = {
        "success": True,
        "shadow_mode": "disabled",
        "corpus_status": corpus.get("status"),
        "corpus_date": corpus.get("date"),
        "prompt_count": len(rows),
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
