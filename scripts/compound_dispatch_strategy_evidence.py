#!/usr/bin/env python3
"""Compare N=3 compound-normalizer dispatch strategies.

This script is evidence-only. It does not modify live dispatch, runtime mode,
promotion state, CSV output, telemetry, or durable audit events.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from candidate_facts.aliases.filter_capability_catalog import load_filter_capability_catalog
from query_understanding.compound_prompt_normalizer_evidence import (
    load_corpus,
    normalize_prompt_text,
    repair_query_plan_payload,
    validate_query_plan_fixture,
    write_report,
)
from query_understanding.compound_prompt_normalizer_provider import (
    AvailabilityNormalizerProviderResult,
    COMPOUND_NORMALIZER_DEFAULT_MODEL,
    call_gemini_availability_normalizer,
    call_gemini_coc_country_normalizer,
    call_gemini_unified_compound_normalizer,
    call_gemini_vessel_tonnage_normalizer,
)


DEFAULT_CORPUS = Path("docs/eval-evidence/compound-dispatch-strategy-n3-corpus-2026-07-01.json")
PROMOTED_N3_FAMILIES = ("availability", "coc_country_match", "vessel_tonnage")
STRATEGIES = {"sequential_per_family", "parallel_per_family", "unified_multi_family"}


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


def _empty_query_plan() -> dict[str, Any]:
    return {
        "version": "v1",
        "constraints": [],
        "soft_signals": [],
        "unapplied": [],
        "needs_review": [],
    }


def _merge_query_plans(results_by_family: Mapping[str, AvailabilityNormalizerProviderResult]) -> dict[str, Any]:
    merged = _empty_query_plan()
    for family in PROMOTED_N3_FAMILIES:
        result = results_by_family.get(family)
        payload = result.parsed_payload if isinstance(result, AvailabilityNormalizerProviderResult) else None
        if not isinstance(payload, Mapping):
            continue
        for key in ("constraints", "soft_signals", "unapplied", "needs_review"):
            items = payload.get(key)
            if isinstance(items, list):
                merged[key].extend(item for item in items if isinstance(item, Mapping))
    return merged


def _provider_for_family(family: str) -> Callable[..., AvailabilityNormalizerProviderResult]:
    if family == "availability":
        return call_gemini_availability_normalizer
    if family == "vessel_tonnage":
        return call_gemini_vessel_tonnage_normalizer
    if family == "coc_country_match":
        return call_gemini_coc_country_normalizer
    raise ValueError(f"Unsupported family for N=3 dispatch strategy evidence: {family}")


def _call_family_provider(
    family: str,
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    api_key: str,
    model: str,
    timeout: int,
    catalog,
) -> AvailabilityNormalizerProviderResult:
    provider = _provider_for_family(family)
    return provider(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        api_key=api_key,
        model=model,
        timeout=timeout,
        catalog=catalog,
    )


def _timed_call_family_provider(
    family: str,
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    api_key: str,
    model: str,
    timeout: int,
    catalog,
) -> tuple[AvailabilityNormalizerProviderResult, float]:
    started = time.perf_counter()
    result = _call_family_provider(
        family,
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        api_key=api_key,
        model=model,
        timeout=timeout,
        catalog=catalog,
    )
    return result, (time.perf_counter() - started) * 1000.0


def _run_sequential(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    api_key: str,
    model: str,
    timeout: int,
    catalog,
) -> tuple[dict[str, Any], dict[str, AvailabilityNormalizerProviderResult], dict[str, float]]:
    results: dict[str, AvailabilityNormalizerProviderResult] = {}
    per_family_elapsed_ms: dict[str, float] = {}
    for family in PROMOTED_N3_FAMILIES:
        result, elapsed_ms = _timed_call_family_provider(
            family,
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=reference_date,
            api_key=api_key,
            model=model,
            timeout=timeout,
            catalog=catalog,
        )
        results[family] = result
        per_family_elapsed_ms[family] = elapsed_ms
    return _merge_query_plans(results), results, per_family_elapsed_ms


def _run_parallel(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    api_key: str,
    model: str,
    timeout: int,
    catalog,
) -> tuple[dict[str, Any], dict[str, AvailabilityNormalizerProviderResult], dict[str, float]]:
    results: dict[str, AvailabilityNormalizerProviderResult] = {}
    per_family_elapsed_ms: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=len(PROMOTED_N3_FAMILIES)) as executor:
        futures = {
            executor.submit(
                _timed_call_family_provider,
                family,
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key=api_key,
                model=model,
                timeout=timeout,
                catalog=catalog,
            ): family
            for family in PROMOTED_N3_FAMILIES
        }
        for future in as_completed(futures):
            family = futures[future]
            try:
                result, elapsed_ms = future.result()
                results[family] = result
                per_family_elapsed_ms[family] = elapsed_ms
            except Exception as exc:  # pragma: no cover - provider wrapper normally captures transport errors.
                results[family] = AvailabilityNormalizerProviderResult(
                    model_id=model,
                    prompt_template_version=f"compound_prompt_normalizer.{family}.v1",
                    raw_llm_output=None,
                    parsed_payload=None,
                    transport_error=f"{type(exc).__name__}: {exc}",
                )
                per_family_elapsed_ms[family] = 0.0
    return _merge_query_plans(results), results, per_family_elapsed_ms


def _run_unified(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    api_key: str,
    model: str,
    timeout: int,
    catalog,
) -> tuple[dict[str, Any], dict[str, AvailabilityNormalizerProviderResult], dict[str, float]]:
    started = time.perf_counter()
    result = call_gemini_unified_compound_normalizer(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        api_key=api_key,
        model=model,
        timeout=timeout,
        catalog=catalog,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    payload = result.parsed_payload if isinstance(result.parsed_payload, Mapping) else _empty_query_plan()
    return dict(payload), {"unified_multi_family": result}, {"unified_multi_family": elapsed_ms}


def _dry_run_payload(case: Mapping[str, Any], *, prompt_normalized: str, reference_date: str) -> dict[str, Any]:
    payload = _empty_query_plan()
    span = {"text": prompt_normalized, "start": 0, "end": len(prompt_normalized)}
    expected_constraints = case.get("expected_constraint_families")
    if isinstance(expected_constraints, list):
        for family in expected_constraints:
            if family == "availability":
                payload["constraints"].append(
                    {
                        "filter_family": "availability",
                        "parameters": {
                            "version": "v1",
                            "value_type": "status",
                            "status": "immediate",
                            "available_by_date": None,
                            "available_from_date": None,
                            "available_until_date": None,
                            "relative_days": None,
                            "resolved_reference_date": reference_date,
                            "display_value": prompt_normalized,
                        },
                        "source_span": dict(span),
                    }
                )
            elif family == "vessel_tonnage":
                payload["constraints"].append(
                    {
                        "filter_family": "vessel_tonnage",
                        "parameters": {
                            "version": "v1",
                            "value_type": "minimum",
                            "min_value": 50000,
                            "max_value": None,
                            "unit": "gt_grt",
                            "years_back": None,
                            "display_value": prompt_normalized,
                        },
                        "source_span": dict(span),
                    }
                )
            elif family == "coc_country_match":
                payload["constraints"].append(
                    {
                        "filter_family": "coc_country_match",
                        "parameters": {
                            "version": "v1",
                            "type": "coc_country_match",
                            "countries": ["india"],
                            "operator": "contains_any",
                            "display_value": prompt_normalized,
                        },
                        "source_span": dict(span),
                    }
                )
    expected_review = case.get("expected_review_families")
    if isinstance(expected_review, list):
        for family in expected_review:
            if family in PROMOTED_N3_FAMILIES:
                payload["needs_review"].append(
                    {
                        "span": dict(span),
                        "candidate_families": [family],
                        "reason": "dry-run expected review route",
                    }
                )
    return payload


def _dry_run_provider_results(strategy: str, payload: Mapping[str, Any], *, model: str) -> dict[str, AvailabilityNormalizerProviderResult]:
    if strategy == "unified_multi_family":
        return {
            "unified_multi_family": AvailabilityNormalizerProviderResult(
                model_id=model,
                prompt_template_version="compound_prompt_normalizer.unified_n3.dry_run",
                raw_llm_output=None,
                parsed_payload=payload,
            )
        }
    return {
        family: AvailabilityNormalizerProviderResult(
            model_id=model,
            prompt_template_version=f"compound_prompt_normalizer.{family}.dry_run",
            raw_llm_output=None,
            parsed_payload=payload,
        )
        for family in PROMOTED_N3_FAMILIES
    }


def _families_from_constraints(payload: Mapping[str, Any]) -> set[str]:
    constraints = payload.get("constraints")
    if not isinstance(constraints, list):
        return set()
    return {
        str(item.get("filter_family"))
        for item in constraints
        if isinstance(item, Mapping) and item.get("filter_family") in PROMOTED_N3_FAMILIES
    }


def _families_from_needs_review(payload: Mapping[str, Any]) -> set[str]:
    needs_review = payload.get("needs_review")
    families: set[str] = set()
    if not isinstance(needs_review, list):
        return families
    for item in needs_review:
        if not isinstance(item, Mapping):
            continue
        candidate_families = item.get("candidate_families")
        if not isinstance(candidate_families, list):
            continue
        families.update(str(family) for family in candidate_families if family in PROMOTED_N3_FAMILIES)
    return families


def _score_case(case: Mapping[str, Any], payload: Mapping[str, Any], *, prompt_normalized: str, catalog) -> dict[str, Any]:
    repaired_payload, repair_actions = repair_query_plan_payload(payload, prompt_normalized=prompt_normalized)
    validation = validate_query_plan_fixture(repaired_payload, prompt_normalized=prompt_normalized, catalog=catalog)
    expected_constraints = set(case.get("expected_constraint_families") if isinstance(case.get("expected_constraint_families"), list) else [])
    expected_review = set(case.get("expected_review_families") if isinstance(case.get("expected_review_families"), list) else [])
    actual_constraints = _families_from_constraints(repaired_payload)
    actual_review = _families_from_needs_review(repaired_payload)
    return {
        "schema_valid": validation.accepted,
        "validator_errors": list(validation.errors),
        "repair_actions": list(repair_actions),
        "expected_constraint_families": sorted(expected_constraints),
        "actual_constraint_families": sorted(actual_constraints),
        "missing_constraint_families": sorted(expected_constraints - actual_constraints),
        "unexpected_constraint_families": sorted(actual_constraints - expected_constraints),
        "expected_review_families": sorted(expected_review),
        "actual_review_families": sorted(actual_review),
        "missing_review_families": sorted(expected_review - actual_review),
        "unexpected_review_families": sorted(actual_review - expected_review),
        "constraint_family_match": actual_constraints == expected_constraints,
        "review_family_match": actual_review == expected_review,
    }


def evaluate_dispatch_strategy(
    corpus: Mapping[str, Any],
    *,
    strategy: str,
    api_key: str,
    model: str,
    timeout: int,
    dry_run: bool = False,
) -> Mapping[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {sorted(STRATEGIES)}")
    catalog = load_filter_capability_catalog()
    cases = corpus.get("cases") if isinstance(corpus.get("cases"), list) else []
    reference_date = str(corpus.get("reference_date") or "")
    case_results: list[Mapping[str, Any]] = []
    total_elapsed_ms = 0.0
    elapsed_values_ms: list[float] = []
    schema_valid = constraint_matches = review_matches = unsafe_widening = 0

    for case in cases:
        if not isinstance(case, Mapping):
            continue
        prompt = str(case.get("prompt") or "")
        prompt_normalized = normalize_prompt_text(prompt)
        started = time.perf_counter()
        if dry_run:
            payload = _dry_run_payload(case, prompt_normalized=prompt_normalized, reference_date=reference_date)
            provider_results = _dry_run_provider_results(strategy, payload, model=model)
            per_family_elapsed_ms = {
                family: 0.0
                for family in (("unified_multi_family",) if strategy == "unified_multi_family" else PROMOTED_N3_FAMILIES)
            }
        elif strategy == "sequential_per_family":
            payload, provider_results, per_family_elapsed_ms = _run_sequential(
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key=api_key,
                model=model,
                timeout=timeout,
                catalog=catalog,
            )
        elif strategy == "parallel_per_family":
            payload, provider_results, per_family_elapsed_ms = _run_parallel(
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key=api_key,
                model=model,
                timeout=timeout,
                catalog=catalog,
            )
        else:
            payload, provider_results, per_family_elapsed_ms = _run_unified(
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key=api_key,
                model=model,
                timeout=timeout,
                catalog=catalog,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        total_elapsed_ms += elapsed_ms
        elapsed_values_ms.append(elapsed_ms)
        score = _score_case(case, payload, prompt_normalized=prompt_normalized, catalog=catalog)
        schema_valid += 1 if score["schema_valid"] else 0
        constraint_matches += 1 if score["constraint_family_match"] else 0
        review_matches += 1 if score["review_family_match"] else 0
        unsafe_widening += 1 if score["unexpected_constraint_families"] else 0
        case_results.append(
            {
                "id": str(case.get("id") or ""),
                "class": str(case.get("class") or ""),
                "prompt_hash": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
                "elapsed_ms": round(elapsed_ms, 3),
                "per_family_elapsed_ms": {
                    family: round(value, 3)
                    for family, value in sorted(per_family_elapsed_ms.items())
                },
                "provider_call_count": len(provider_results),
                "transport_errors": {
                    family: result.transport_error
                    for family, result in provider_results.items()
                    if isinstance(result.transport_error, str) and result.transport_error
                },
                **score,
            }
        )

    total = len(case_results)
    sorted_elapsed = sorted(elapsed_values_ms)
    p50_elapsed_ms = None
    if sorted_elapsed:
        p50_elapsed_ms = sorted_elapsed[len(sorted_elapsed) // 2]
    return {
        "version": "compound_dispatch_strategy_evidence.v1",
        "strategy": strategy,
        "model_id": model,
        "corpus_id": corpus.get("corpus_id"),
        "corpus_version": corpus.get("version"),
        "reference_date": reference_date,
        "promoted_families": list(PROMOTED_N3_FAMILIES),
        "live_dispatch": False,
        "dry_run": dry_run,
        "summary": {
            "total_cases": total,
            "total_elapsed_ms": round(total_elapsed_ms, 3),
            "mean_elapsed_ms": round(total_elapsed_ms / total, 3) if total else None,
            "p50_elapsed_ms": round(p50_elapsed_ms, 3) if p50_elapsed_ms is not None else None,
            "schema_valid_rate": schema_valid / total if total else None,
            "unsafe_widening_count": unsafe_widening,
            "constraint_family_match_rate": constraint_matches / total if total else None,
            "review_family_match_rate": review_matches / total if total else None,
            "provider_calls_per_case": 1 if strategy == "unified_multi_family" else len(PROMOTED_N3_FAMILIES),
        },
        "adoption_thresholds": {
            "schema_valid_rate_min": 0.99,
            "constraint_family_match_rate_min": 1.0,
            "review_family_match_rate_min": 1.0,
            "latency_reduction_min_relative": 0.30,
            "latency_reduction_min_absolute_ms": 200,
            "latency_reduction_rule": "p50 total elapsed reduction must be >= 30% or >= 200ms absolute, whichever threshold is smaller",
            "unified_must_not_regress_against_parallel": True,
            "parallel_latency_should_improve_against_sequential": True,
        },
        "case_results": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate N=3 compound-normalizer dispatch strategies.")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Path to the N=3 dispatch strategy corpus JSON.")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    parser.add_argument("--output", default="", help="Optional path for the evidence report JSON.")
    parser.add_argument("--model", default=COMPOUND_NORMALIZER_DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--api-key-env", default="GEMINI_API_KEY")
    parser.add_argument("--config", default="", help="Optional config.ini path used as a Gemini key fallback.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exercise report generation with deterministic fixture-shaped payloads instead of calling Gemini.",
    )
    args = parser.parse_args()

    config_path = _config_path_arg(args.config)
    api_key = os.getenv(args.api_key_env) or os.getenv("GOOGLE_API_KEY") or _gemini_api_key_from_config(config_path)
    if not api_key and not args.dry_run:
        raise SystemExit(
            f"Missing API key. Set {args.api_key_env}, GOOGLE_API_KEY, or Credentials/Gemini_API_Key in {config_path}."
        )
    report = evaluate_dispatch_strategy(
        load_corpus(Path(args.corpus)),
        strategy=args.strategy,
        api_key=api_key or "dry-run",
        model=args.model,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    if args.output:
        write_report(report, Path(args.output))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
