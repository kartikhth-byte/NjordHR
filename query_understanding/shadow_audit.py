"""Shadow audit helpers for prompt-corpus comparison logging."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Mapping

from .legacy_parser_adapter import LegacyParserAdapter
from .normalizer_compare import compare_query_plans, canonical_comparison_records
from .llm_normalizer import is_enabled, maybe_build_shadow_query_plan


def build_shadow_audit_entry(
    analyzer: Any,
    prompt: str,
    *,
    rank: str | None = None,
    prompt_id: str,
    llm_plan: Mapping[str, Any] | None = None,
    llm_plan_provider: Any | None = None,
) -> Dict[str, Any]:
    """Build a shadow audit record without altering production flow."""

    adapter = LegacyParserAdapter(analyzer)
    legacy_plan = adapter.adapt(prompt, rank=rank, prompt_template_version="legacy.parser.v1", prompt_id=prompt_id)
    legacy_records = [asdict(record) for record in canonical_comparison_records(legacy_plan, prompt_id=prompt_id)]
    shadow_enabled = is_enabled()

    if llm_plan is None and shadow_enabled and llm_plan_provider is not None:
        llm_plan = maybe_build_shadow_query_plan(
            llm_plan_provider,
            analyzer=analyzer,
            prompt=prompt,
            rank=rank,
            prompt_id=prompt_id,
            legacy_plan=legacy_plan,
        )

    if llm_plan is None or not shadow_enabled:
        return {
            "prompt_id": prompt_id,
            "prompt": prompt,
            "rank_context": rank,
            "shadow_mode": "disabled",
            "shadow_wiring": {
                "feature_flag_enabled": shadow_enabled,
                "llm_plan_provider_attached": llm_plan_provider is not None,
                "llm_plan_requested": shadow_enabled and llm_plan_provider is not None,
            },
            "catalog_snapshot_id": legacy_plan.get("normalizer", {}).get("catalog_version"),
            "legacy_plan": legacy_plan,
            "legacy_comparison_records": legacy_records,
            "llm_plan": None,
            "comparison_results": [],
            "comparison_outcomes": [],
            "validation_status": legacy_plan.get("validation", {}).get("status"),
        }

    comparison_results = compare_query_plans(legacy_plan, llm_plan, prompt_id=prompt_id)
    return {
        "prompt_id": prompt_id,
        "prompt": prompt,
        "rank_context": rank,
        "shadow_mode": "enabled",
        "shadow_wiring": {
            "feature_flag_enabled": shadow_enabled,
            "llm_plan_provider_attached": llm_plan_provider is not None,
            "llm_plan_requested": True,
        },
        "catalog_snapshot_id": legacy_plan.get("normalizer", {}).get("catalog_version"),
        "legacy_plan": legacy_plan,
        "legacy_comparison_records": legacy_records,
        "llm_plan": llm_plan,
        "comparison_results": [asdict(result) for result in comparison_results],
        "comparison_outcomes": [result.comparison_outcome for result in comparison_results],
        "validation_status": legacy_plan.get("validation", {}).get("status"),
    }


def build_shadow_audit_rows(
    analyzer: Any,
    prompts: Iterable[Mapping[str, Any]],
    *,
    rank: str | None = None,
    llm_plan_provider: Any | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, entry in enumerate(prompts, start=1):
        prompt = str(entry.get("prompt") or "")
        prompt_id = str(entry.get("prompt_id") or f"prompt-{index}")
        rows.append(
            build_shadow_audit_entry(
                analyzer,
                prompt,
                rank=rank,
                prompt_id=prompt_id,
                llm_plan=None,
                llm_plan_provider=llm_plan_provider,
            )
        )
    return rows
