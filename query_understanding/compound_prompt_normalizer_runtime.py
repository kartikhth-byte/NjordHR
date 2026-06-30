"""Runtime dispatcher for promoted compound-prompt normalizer families."""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Callable, Mapping

from candidate_facts.aliases.filter_capability_catalog import (
    PROMOTED_FAMILIES,
    load_filter_capability_catalog,
)
from query_understanding.compound_prompt_normalizer_evidence import (
    normalize_prompt_text,
    repair_query_plan_payload,
    validate_query_plan_fixture,
)
from query_understanding.compound_prompt_normalizer_provider import (
    call_gemini_availability_normalizer,
    call_gemini_vessel_tonnage_normalizer,
)


LLM_NORMALIZER_MODE_ENV = "NJORDHR_LLM_NORMALIZER_MODE"
NORMALIZER_MODES = {"deterministic", "shadow", "live"}


def llm_normalizer_mode() -> str:
    """Return the runtime mode for the compound-prompt normalizer."""

    raw = os.getenv(LLM_NORMALIZER_MODE_ENV, "live")
    normalized = str(raw or "live").strip().lower()
    return normalized if normalized in NORMALIZER_MODES else "deterministic"


def _availability_constraint_from_parameters(parameters: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value_type = str(parameters.get("value_type") or "").strip()
    display_value = str(parameters.get("display_value") or "").strip()
    resolved_reference_date = parameters.get("resolved_reference_date")
    constraint: dict[str, Any] = {
        "value_type": value_type,
        "display_value": display_value,
    }
    if isinstance(resolved_reference_date, str) and resolved_reference_date:
        constraint["resolved_reference_date"] = resolved_reference_date

    if value_type == "status" and parameters.get("status") == "immediate":
        constraint["status"] = "immediately"
        return constraint
    if value_type == "by_date":
        constraint["available_by_date"] = parameters.get("available_by_date")
        return constraint if constraint["available_by_date"] else None
    if value_type == "from_date":
        constraint["available_from_date"] = parameters.get("available_from_date")
        return constraint if constraint["available_from_date"] else None
    if value_type == "relative_days":
        constraint["relative_days"] = parameters.get("relative_days")
        return constraint if isinstance(constraint["relative_days"], int) else None
    if value_type == "window":
        constraint["available_from_date"] = parameters.get("available_from_date")
        constraint["available_until_date"] = parameters.get("available_until_date")
        return constraint if constraint["available_from_date"] and constraint["available_until_date"] else None
    return None


def _vessel_tonnage_constraint_from_parameters(parameters: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value_type = str(parameters.get("value_type") or "").strip()
    min_value = parameters.get("min_value")
    max_value = parameters.get("max_value")
    unit = str(parameters.get("unit") or "any").strip() or "any"
    constraint: dict[str, Any] = {
        "min_value": min_value if isinstance(min_value, int) and not isinstance(min_value, bool) else None,
        "max_value": max_value if isinstance(max_value, int) and not isinstance(max_value, bool) else None,
        "unit": unit,
    }
    years_back = parameters.get("years_back")
    if isinstance(years_back, int) and not isinstance(years_back, bool):
        constraint["years_back"] = years_back

    if value_type == "minimum" and constraint["min_value"] is not None:
        constraint["max_value"] = None
        return constraint
    if value_type == "maximum" and constraint["max_value"] is not None:
        constraint["min_value"] = None
        return constraint
    if value_type == "range" and constraint["min_value"] is not None and constraint["max_value"] is not None:
        return constraint
    return None


def _constraint_from_parameters(family: str, parameters: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if family == "availability":
        return _availability_constraint_from_parameters(parameters)
    if family == "vessel_tonnage":
        return _vessel_tonnage_constraint_from_parameters(parameters)
    return None


def _provider_result_for_family(
    family: str,
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str,
    catalog,
    api_key: str | None,
):
    if family == "availability":
        return call_gemini_availability_normalizer(
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=reference_date,
            catalog=catalog,
            api_key=api_key,
        )
    if family == "vessel_tonnage":
        return call_gemini_vessel_tonnage_normalizer(
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=reference_date,
            catalog=catalog,
            api_key=api_key,
        )
    return None


def _base_diagnostics(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "promoted_families": sorted(PROMOTED_FAMILIES),
        "dispatched": False,
        "provider_invoked": False,
        "validator_result": "skipped",
        "validator_errors": [],
        "repair_actions": [],
        "family_seen": False,
    }


def _diagnostics_from_result(mode: str, result, *, provider_invoked: bool) -> dict[str, Any]:
    diagnostics = _base_diagnostics(mode)
    diagnostics["provider_invoked"] = provider_invoked
    diagnostics["model_id"] = getattr(result, "model_id", None)
    diagnostics["prompt_template_version"] = getattr(result, "prompt_template_version", None)
    diagnostics["transport_error"] = getattr(result, "transport_error", None)
    helper_tool_calls = getattr(result, "helper_tool_calls", ())
    diagnostics["helper_tool_version"] = getattr(result, "helper_tool_version", None)
    diagnostics["helper_tool_call_count"] = len(helper_tool_calls) if isinstance(helper_tool_calls, tuple) else 0
    diagnostics["helper_tool_calls"] = list(helper_tool_calls) if isinstance(helper_tool_calls, tuple) else []
    return diagnostics


def _constraints_from_provider_result(
    prompt_normalized: str,
    result,
    *,
    mode: str,
    catalog,
    provider_invoked: bool,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, dict[str, Any]]]:
    constraints_by_family: dict[str, Mapping[str, Any]] = {}
    diagnostics_by_family: dict[str, dict[str, Any]] = {}
    base_diagnostics = _diagnostics_from_result(mode, result, provider_invoked=provider_invoked)
    raw_payload = getattr(result, "parsed_payload", None)
    if not isinstance(raw_payload, Mapping):
        base_diagnostics["validator_result"] = "rejected"
        base_diagnostics["validator_errors"] = ["provider returned no parsed payload"]
        for family in sorted(PROMOTED_FAMILIES):
            diagnostics_by_family[family] = dict(base_diagnostics)
        return constraints_by_family, diagnostics_by_family

    repaired_payload, repair_actions = repair_query_plan_payload(
        raw_payload,
        prompt_normalized=prompt_normalized,
    )
    base_diagnostics["repair_actions"] = list(repair_actions)
    validation = validate_query_plan_fixture(
        repaired_payload,
        prompt_normalized=prompt_normalized,
        catalog=catalog,
    )
    base_diagnostics["validator_result"] = "accepted" if validation.accepted else "rejected"
    base_diagnostics["validator_errors"] = list(validation.errors)
    for family in sorted(PROMOTED_FAMILIES):
        diagnostics_by_family[family] = dict(base_diagnostics)
    if not validation.accepted or mode != "live":
        return constraints_by_family, diagnostics_by_family

    constraints = repaired_payload.get("constraints")
    if isinstance(constraints, list):
        for item in constraints:
            if not isinstance(item, Mapping):
                continue
            family = item.get("filter_family")
            if family not in PROMOTED_FAMILIES:
                continue
            family_key = str(family)
            diagnostics_by_family.setdefault(family_key, dict(base_diagnostics))["family_seen"] = True
            parameters = item.get("parameters")
            if not isinstance(parameters, Mapping):
                continue
            constraint = _constraint_from_parameters(family_key, parameters)
            if constraint:
                constraints_by_family[family_key] = constraint
                diagnostics_by_family[family_key]["dispatched"] = True
    needs_review = repaired_payload.get("needs_review")
    if isinstance(needs_review, list):
        for item in needs_review:
            if not isinstance(item, Mapping):
                continue
            candidate_families = item.get("candidate_families")
            if not isinstance(candidate_families, list):
                continue
            for family in candidate_families:
                if family in PROMOTED_FAMILIES:
                    diagnostics_by_family.setdefault(str(family), dict(base_diagnostics))["family_seen"] = True
    return constraints_by_family, diagnostics_by_family


def promoted_constraints_from_prompt(
    prompt: str,
    *,
    reference_date: date | str | None = None,
    provider: Callable[..., Any] | None = None,
    api_key: str | None = None,
) -> tuple[Mapping[str, Mapping[str, Any]], Mapping[str, Mapping[str, Any]]]:
    """Return promoted live constraints from the compound normalizer."""

    mode = llm_normalizer_mode()
    diagnostics: dict[str, dict[str, Any]] = {}
    if mode == "deterministic":
        for family in sorted(PROMOTED_FAMILIES):
            diagnostics[family] = _base_diagnostics(mode)
        return {}, diagnostics

    prompt_normalized = normalize_prompt_text(prompt)
    selected_reference_date = (
        reference_date.isoformat()
        if isinstance(reference_date, date)
        else str(reference_date or date.today().isoformat())
    )
    loaded_catalog = load_filter_capability_catalog()
    if provider is not None:
        result = provider(
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=selected_reference_date,
            catalog=loaded_catalog,
        )
        return _constraints_from_provider_result(
            prompt_normalized,
            result,
            mode=mode,
            catalog=loaded_catalog,
            provider_invoked=True,
        )

    constraints_by_family: dict[str, Mapping[str, Any]] = {}
    diagnostics_by_family: dict[str, Mapping[str, Any]] = {}
    for family in sorted(PROMOTED_FAMILIES):
        result = _provider_result_for_family(
            family,
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=selected_reference_date,
            catalog=loaded_catalog,
            api_key=api_key,
        )
        if result is None:
            family_diagnostics = _base_diagnostics(mode)
            family_diagnostics["validator_result"] = "rejected"
            family_diagnostics["validator_errors"] = [f"{family} has no provider"]
            diagnostics_by_family[family] = family_diagnostics
            continue
        family_constraints, family_diagnostics = _constraints_from_provider_result(
            prompt_normalized,
            result,
            mode=mode,
            catalog=loaded_catalog,
            provider_invoked=True,
        )
        if family in family_constraints:
            constraints_by_family[family] = family_constraints[family]
        diagnostics_by_family[family] = dict(family_diagnostics.get(family) or _diagnostics_from_result(mode, result, provider_invoked=True))
    return constraints_by_family, diagnostics_by_family


def promoted_availability_constraint_from_prompt(
    prompt: str,
    *,
    reference_date: date | str | None = None,
    provider: Callable[..., Any] | None = None,
    api_key: str | None = None,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any]]:
    """Return a promoted availability constraint from the LLM normalizer.

    The dispatcher is active only in live mode and only when ``availability`` is
    present in ``PROMOTED_FAMILIES``. Shadow mode invokes the provider for audit
    diagnostics but returns no live constraint.
    """

    constraints, diagnostics = promoted_constraints_from_prompt(
        prompt,
        reference_date=reference_date,
        provider=provider,
        api_key=api_key,
    )
    return constraints.get("availability"), dict(diagnostics.get("availability") or _base_diagnostics(llm_normalizer_mode()))
