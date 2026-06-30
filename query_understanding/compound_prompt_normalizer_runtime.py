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

    mode = llm_normalizer_mode()
    diagnostics: dict[str, Any] = {
        "mode": mode,
        "promoted_families": sorted(PROMOTED_FAMILIES),
        "dispatched": False,
        "provider_invoked": False,
        "validator_result": "skipped",
        "validator_errors": [],
        "repair_actions": [],
    }
    if mode == "deterministic":
        return None, diagnostics

    prompt_normalized = normalize_prompt_text(prompt)
    selected_reference_date = (
        reference_date.isoformat()
        if isinstance(reference_date, date)
        else str(reference_date or date.today().isoformat())
    )
    loaded_catalog = load_filter_capability_catalog()
    diagnostics["provider_invoked"] = True
    if provider is None:
        result = call_gemini_availability_normalizer(
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=selected_reference_date,
            catalog=loaded_catalog,
            api_key=api_key,
        )
    else:
        result = provider(
            prompt,
            prompt_normalized=prompt_normalized,
            reference_date=selected_reference_date,
            catalog=loaded_catalog,
        )
    diagnostics["model_id"] = getattr(result, "model_id", None)
    diagnostics["prompt_template_version"] = getattr(result, "prompt_template_version", None)
    diagnostics["transport_error"] = getattr(result, "transport_error", None)
    helper_tool_calls = getattr(result, "helper_tool_calls", ())
    diagnostics["helper_tool_version"] = getattr(result, "helper_tool_version", None)
    diagnostics["helper_tool_call_count"] = len(helper_tool_calls) if isinstance(helper_tool_calls, tuple) else 0
    diagnostics["helper_tool_calls"] = list(helper_tool_calls) if isinstance(helper_tool_calls, tuple) else []
    raw_payload = getattr(result, "parsed_payload", None)
    if not isinstance(raw_payload, Mapping):
        diagnostics["validator_result"] = "rejected"
        diagnostics["validator_errors"] = ["provider returned no parsed payload"]
        return None, diagnostics

    repaired_payload, repair_actions = repair_query_plan_payload(
        raw_payload,
        prompt_normalized=prompt_normalized,
    )
    diagnostics["repair_actions"] = list(repair_actions)
    validation = validate_query_plan_fixture(
        repaired_payload,
        prompt_normalized=prompt_normalized,
        catalog=loaded_catalog,
    )
    diagnostics["validator_result"] = "accepted" if validation.accepted else "rejected"
    diagnostics["validator_errors"] = list(validation.errors)
    if not validation.accepted:
        return None, diagnostics

    if mode != "live":
        return None, diagnostics
    if "availability" not in PROMOTED_FAMILIES:
        diagnostics["validator_errors"].append("availability is not in PROMOTED_FAMILIES")
        return None, diagnostics

    constraints = repaired_payload.get("constraints")
    if not isinstance(constraints, list):
        return None, diagnostics
    for item in constraints:
        if not isinstance(item, Mapping) or item.get("filter_family") != "availability":
            continue
        parameters = item.get("parameters")
        if not isinstance(parameters, Mapping):
            continue
        constraint = _availability_constraint_from_parameters(parameters)
        if constraint:
            diagnostics["dispatched"] = True
            return constraint, diagnostics
    return None, diagnostics
