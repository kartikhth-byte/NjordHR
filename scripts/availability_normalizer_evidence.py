#!/usr/bin/env python3
"""Evaluate the availability shadow-normalizer evidence corpus."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_availability_helper_tool_fixture_corpus,
    evaluate_availability_llm_corpus,
    evaluate_corpus_file,
    load_corpus,
    write_report,
)
from query_understanding.compound_prompt_normalizer_provider import (
    COMPOUND_NORMALIZER_DEFAULT_MODEL,
    call_gemini_availability_normalizer,
)


DEFAULT_CORPUS = Path("docs/eval-evidence/availability-shadow-normalizer-corpus-2026-06-29.json")


def _config_path_arg(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    env_path = os.getenv("NJORDHR_CONFIG_PATH")
    return Path(env_path).expanduser() if env_path else PROJECT_ROOT / "config.ini"


def _gemini_api_key_from_config(path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    if parser.has_option("Credentials", "Gemini_API_Key"):
        return parser.get("Credentials", "Gemini_API_Key", fallback="").strip()
    if parser.has_option("Credentials", "gemini_api_key"):
        return parser.get("Credentials", "gemini_api_key", fallback="").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the availability shadow-normalizer evidence corpus.")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Path to the availability evidence corpus JSON.")
    parser.add_argument("--output", default="", help="Optional path for the evidence report JSON.")
    parser.add_argument(
        "--invoke-llm",
        action="store_true",
        help="Invoke the configured LLM provider for audit-only evidence. No constraints are dispatched.",
    )
    parser.add_argument("--model", default=COMPOUND_NORMALIZER_DEFAULT_MODEL, help="Provider model id for --invoke-llm.")
    parser.add_argument(
        "--use-helper-tools",
        action="store_true",
        help="Include provider-scoped availability helper-tool context in --invoke-llm prompts.",
    )
    parser.add_argument(
        "--api-key-env",
        default="GEMINI_API_KEY",
        help="Environment variable that contains the Gemini API key for --invoke-llm.",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional config.ini path used as a Gemini key fallback for --invoke-llm.",
    )
    args = parser.parse_args()

    if args.invoke_llm:
        config_path = _config_path_arg(args.config)
        api_key = os.getenv(args.api_key_env) or os.getenv("GOOGLE_API_KEY") or _gemini_api_key_from_config(config_path)
        if not api_key:
            raise SystemExit(
                f"Missing API key. Set {args.api_key_env}, GOOGLE_API_KEY, or Credentials/Gemini_API_Key in {config_path} before using --invoke-llm."
            )

        def provider(prompt: str, *, prompt_normalized: str, reference_date: str, catalog):
            return call_gemini_availability_normalizer(
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key=api_key,
                model=args.model,
                catalog=catalog,
                use_helper_tools=args.use_helper_tools,
            )

        report = evaluate_availability_llm_corpus(load_corpus(Path(args.corpus)), provider=provider)
    elif args.use_helper_tools:
        report = evaluate_availability_helper_tool_fixture_corpus(load_corpus(Path(args.corpus)))
    else:
        report = evaluate_corpus_file(Path(args.corpus))
    if args.output:
        write_report(report, Path(args.output))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
