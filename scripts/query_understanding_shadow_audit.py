#!/usr/bin/env python3
"""Emit a disabled shadow audit for one or more prompt corpora.

This script logs the canonical legacy query-plan records now so future LLM
outputs can be compared against the same corpus without changing production
search behavior.
"""

import argparse
import configparser
import json
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_analyzer import AIResumeAnalyzer, AdvancedPDFProcessor, ConfigManager
from query_understanding.hard_filter_catalog import UNAPPLIED_FAMILY_IDS
from query_understanding.shadow_audit import build_shadow_audit_entry, build_shadow_audit_rows
from query_understanding.llm_normalizer import is_enabled
from query_understanding.shadow_llm_provider import build_shadow_llm_query_plan


DEFAULT_CORPUS_PATH = PROJECT_ROOT / "docs" / "AI_SEARCH_V3_4_BOOTSTRAP_PROMPT_CORPUS_2026-04-08.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "AI_Search_Results" / "query_understanding_shadow_audit_current.json"


class _RegistryStub:
    def generate_resume_id(self, file_path):
        return Path(file_path).stem


def _build_analyzer():
    parser = configparser.ConfigParser()
    config_path = os.environ.get("NJORDHR_CONFIG_PATH")
    parser.read(Path(config_path).expanduser() if config_path else PROJECT_ROOT / "config.ini")
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    analyzer.config = ConfigManager(parser)
    analyzer.registry = _RegistryStub()
    analyzer.pdf_processor = AdvancedPDFProcessor()
    analyzer._configured_ship_type_labels_cache = None
    return analyzer


def _load_corpus(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_corpora(corpora: list[tuple[Path, dict]]) -> dict:
    merged_families: dict[str, list[dict]] = {}
    family_counts: dict[str, int] = {}
    source_corpora = []
    purpose_items: list[str] = []
    seen_prompt_keys: dict[tuple[str, str], str] = {}
    duplicate_prompt_warnings: list[dict[str, str]] = []
    prompt_count = 0
    for corpus_path, corpus in corpora:
        corpus_prompt_count = 0
        corpus_purpose = corpus.get("purpose") or []
        if isinstance(corpus_purpose, str):
            corpus_purpose = [corpus_purpose]
        if isinstance(corpus_purpose, list):
            for item in corpus_purpose:
                text = str(item or "").strip()
                if text and text not in purpose_items:
                    purpose_items.append(text)
        for family, entries in (corpus.get("families") or {}).items():
            family_entries = merged_families.setdefault(family, [])
            family_entries.extend(entries)
            family_counts[family] = family_counts.get(family, 0) + len(entries)
            corpus_prompt_count += len(entries)
            prompt_count += len(entries)
            for entry in entries:
                prompt_text = str(entry.get("prompt") or "").strip()
                if not prompt_text:
                    continue
                prompt_key = (family, prompt_text.lower())
                if prompt_key in seen_prompt_keys:
                    duplicate_prompt_warnings.append(
                        {
                            "family": family,
                            "prompt": prompt_text,
                            "first_seen_in": seen_prompt_keys[prompt_key],
                            "duplicate_in": str(corpus_path),
                        }
                    )
                else:
                    seen_prompt_keys[prompt_key] = str(corpus_path)
        source_corpora.append(
            {
                "path": str(corpus_path),
                "status": corpus.get("status"),
                "date": corpus.get("date"),
                "prompt_count": corpus_prompt_count,
            }
        )
    return {
        "status": "combined_shadow_audit_corpus",
        "date": "combined",
        "purpose": purpose_items,
        "source_corpora": source_corpora,
        "families": merged_families,
        "prompt_count": prompt_count,
        "family_counts": family_counts,
        "duplicate_prompt_warnings": duplicate_prompt_warnings,
    }


def _build_prompts_from_corpora(
    loaded_corpora: list[tuple[Path, dict]],
    family_filter: set[str] | None = None,
) -> list[dict]:
    family_filter = set(family_filter or [])
    prompts = []
    for corpus_path, corpus_data in loaded_corpora:
        corpus_label = corpus_path.stem
        for family, entries in (corpus_data.get("families") or {}).items():
            for index, entry in enumerate(entries, start=1):
                expected = str(entry.get("expected_primary_family") or "")
                if family_filter and expected not in family_filter and family not in family_filter:
                    continue
                prompts.append(
                    {
                        "prompt_id": f"{corpus_label}:{family}:{index}",
                        "prompt": entry.get("prompt"),
                        "family": family,
                        "expected_primary_family": expected,
                    }
                )
    return prompts


def main():
    parser = argparse.ArgumentParser(description="Emit disabled shadow-audit rows for one or more prompt corpora.")
    parser.add_argument(
        "--corpus",
        action="append",
        dest="corpora",
        help="Path to a prompt corpus JSON. May be repeated; defaults to the bootstrap corpus when omitted.",
    )
    parser.add_argument(
        "--extra-corpus",
        action="append",
        default=[],
        help="Additional prompt corpus JSON to merge before auditing.",
    )
    parser.add_argument(
        "--combined-corpus-output",
        default="",
        help="Optional path to write the merged corpus JSON used for the audit.",
    )
    parser.add_argument(
        "--family-filter",
        action="append",
        default=[],
        help="Restrict the audit to one or more expected_primary_family values. "
        "Repeatable. Empty = all families. Examples: --family-filter age_range "
        "--family-filter us_visa.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional pause between LLM requests. Useful when provider APIs return 429 rate-limit errors.",
    )
    parser.add_argument(
        "--max-prompts",
        type=int,
        default=0,
        help="Optional cap on prompts evaluated from the selected corpus/filter. Useful for rate-limit probes.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write the JSON report")
    args = parser.parse_args()

    corpus_args = list(args.corpora or [str(DEFAULT_CORPUS_PATH)])
    corpus_args.extend(args.extra_corpus or [])
    corpus_paths = [Path(item) for item in corpus_args]
    for corpus_path in corpus_paths:
        if not corpus_path.exists():
            print(f"Corpus file not found: {corpus_path}")
            return 2

    loaded_corpora = [(corpus_path, _load_corpus(corpus_path)) for corpus_path in corpus_paths]
    corpus = _merge_corpora(loaded_corpora) if len(loaded_corpora) > 1 else loaded_corpora[0][1]
    if args.combined_corpus_output:
        combined_path = Path(args.combined_corpus_output)
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        combined_path.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote combined corpus to {combined_path}")
    if corpus.get("duplicate_prompt_warnings"):
        print(
            f"WARNING: merged corpus contains {len(corpus['duplicate_prompt_warnings'])} duplicate prompt(s).",
            file=sys.stderr,
        )

    analyzer = _build_analyzer()
    family_filter = set(args.family_filter or [])
    prompts = _build_prompts_from_corpora(loaded_corpora, family_filter=family_filter)
    if args.max_prompts > 0:
        prompts = prompts[: args.max_prompts]
    print(
        f"[shadow-audit] prompts to evaluate: {len(prompts)}"
        + (f" (filtered to: {sorted(family_filter)})" if family_filter else "")
    )

    if args.sleep_seconds > 0:
        rows = []
        for index, prompt_entry in enumerate(prompts, start=1):
            if index > 1:
                time.sleep(args.sleep_seconds)
            print(f"[shadow-audit] evaluating prompt {index}/{len(prompts)}")
            rows.append(
                build_shadow_audit_entry(
                    analyzer,
                    str(prompt_entry.get("prompt") or ""),
                    prompt_id=str(prompt_entry.get("prompt_id") or f"prompt-{index}"),
                    expected_delta_families=UNAPPLIED_FAMILY_IDS,
                    llm_plan_provider=build_shadow_llm_query_plan,
                )
            )
    else:
        rows = build_shadow_audit_rows(
            analyzer,
            prompts,
            expected_delta_families=UNAPPLIED_FAMILY_IDS,
            llm_plan_provider=build_shadow_llm_query_plan,
        )
    report = {
        "success": True,
        "shadow_mode": "enabled" if is_enabled() else "disabled",
        "corpus_status": corpus.get("status"),
        "corpus_date": corpus.get("date"),
        "source_corpora": corpus.get("source_corpora", []),
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
