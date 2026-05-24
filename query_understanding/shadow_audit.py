"""Shadow audit helpers for prompt-corpus comparison logging."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Mapping

from candidate_facts.audit import build_candidate_resume_facts_audit_metadata
from .legacy_parser_adapter import LegacyParserAdapter
from .normalizer_compare import compare_query_plans, canonical_comparison_records
from .llm_normalizer import is_enabled, maybe_build_shadow_query_plan


def _unwrap_shadow_llm_result(result: Any) -> tuple[Mapping[str, Any] | None, Dict[str, Any]]:
    if isinstance(result, Mapping) and "plan" in result and "diagnostics" in result:
        plan = result.get("plan")
        diagnostics = result.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        return (plan if isinstance(plan, Mapping) else None), dict(diagnostics)
    if isinstance(result, Mapping):
        return result, {}
    return None, {}


def build_shadow_audit_entry(
    analyzer: Any,
    prompt: str,
    *,
    rank: str | None = None,
    prompt_id: str,
    expected_delta_families: Iterable[str] | None = None,
    llm_plan: Mapping[str, Any] | None = None,
    llm_plan_provider: Any | None = None,
    candidate_resume_facts_row: Mapping[str, Any] | None = None,
    candidate_resume_facts_resolution: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a shadow audit record without altering production flow."""

    adapter = LegacyParserAdapter(analyzer)
    legacy_plan = adapter.adapt(prompt, rank=rank, prompt_template_version="legacy.parser.v1", prompt_id=prompt_id)
    legacy_records = [asdict(record) for record in canonical_comparison_records(legacy_plan, prompt_id=prompt_id)]
    candidate_facts_audit = build_candidate_resume_facts_audit_metadata(
        candidate_resume_facts_row,
        resolution=candidate_resume_facts_resolution,
    )
    shadow_enabled = is_enabled()
    llm_plan_source = "disabled"
    shadow_llm_diagnostics: Dict[str, Any] = {}

    if llm_plan is None and shadow_enabled and llm_plan_provider is not None:
        llm_plan_result = maybe_build_shadow_query_plan(
            llm_plan_provider,
            analyzer=analyzer,
            prompt=prompt,
            rank=rank,
            prompt_id=prompt_id,
            legacy_plan=legacy_plan,
        )
        llm_plan, shadow_llm_diagnostics = _unwrap_shadow_llm_result(llm_plan_result)

    if llm_plan is None or not shadow_enabled:
        llm_plan_source = "disabled"
        return {
            "prompt_id": prompt_id,
            "prompt": prompt,
            "rank_context": rank,
            "shadow_mode": "disabled",
            "shadow_wiring": {
                "feature_flag_enabled": shadow_enabled,
                "llm_plan_provider_attached": llm_plan_provider is not None,
                "llm_plan_requested": shadow_enabled and llm_plan_provider is not None,
                "llm_plan_source": llm_plan_source,
                "llm_plan_fallback_used": False,
                "failure_reason": shadow_llm_diagnostics.get("reason"),
            },
            "shadow_llm_diagnostics": shadow_llm_diagnostics,
            "catalog_snapshot_id": legacy_plan.get("normalizer", {}).get("catalog_version"),
            "legacy_plan": legacy_plan,
            "legacy_comparison_records": legacy_records,
            "llm_plan": None,
            "comparison_results": [],
            "comparison_outcomes": [],
            "validation_status": legacy_plan.get("validation", {}).get("status"),
            "candidate_facts_audit": candidate_facts_audit,
        }

    comparison_results = compare_query_plans(
        legacy_plan,
        llm_plan,
        prompt_id=prompt_id,
        expected_delta_families=expected_delta_families,
    )
    llm_normalizer_name = str(llm_plan.get("normalizer", {}).get("name") or "")
    return {
        "prompt_id": prompt_id,
        "prompt": prompt,
        "rank_context": rank,
        "shadow_mode": "enabled",
        "shadow_wiring": {
            "feature_flag_enabled": shadow_enabled,
            "llm_plan_provider_attached": llm_plan_provider is not None,
            "llm_plan_requested": True,
            "llm_plan_source": "legacy_fallback" if llm_normalizer_name == "legacy" else "llm",
            "llm_plan_fallback_used": llm_normalizer_name == "legacy",
            "failure_reason": shadow_llm_diagnostics.get("reason"),
        },
        "shadow_llm_diagnostics": shadow_llm_diagnostics,
        "catalog_snapshot_id": legacy_plan.get("normalizer", {}).get("catalog_version"),
        "legacy_plan": legacy_plan,
        "legacy_comparison_records": legacy_records,
        "llm_plan": llm_plan,
        "comparison_results": [asdict(result) for result in comparison_results],
        "comparison_outcomes": [result.comparison_outcome for result in comparison_results],
        "validation_status": legacy_plan.get("validation", {}).get("status"),
        "candidate_facts_audit": candidate_facts_audit,
    }


def build_shadow_audit_rows(
    analyzer: Any,
    prompts: Iterable[Mapping[str, Any]],
    *,
    rank: str | None = None,
    expected_delta_families: Iterable[str] | None = None,
    llm_plan_provider: Any | None = None,
    candidate_resume_facts_row: Mapping[str, Any] | None = None,
    candidate_resume_facts_resolution: Mapping[str, Any] | None = None,
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
                expected_delta_families=expected_delta_families,
                llm_plan=None,
                llm_plan_provider=llm_plan_provider,
                candidate_resume_facts_row=candidate_resume_facts_row,
                candidate_resume_facts_resolution=candidate_resume_facts_resolution,
            )
        )
    return rows
