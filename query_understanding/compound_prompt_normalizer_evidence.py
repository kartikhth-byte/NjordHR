"""Evidence harness for the compound-prompt normalizer catalog.

This module validates shadow-normalizer payloads against the catalog contract.
Fixture mode does not call an LLM. LLM mode records provider output for audit
only. Neither mode dispatches constraints.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from candidate_facts.aliases.filter_capability_catalog import (
    FilterCapabilityCatalog,
    load_filter_capability_catalog,
    validate_catalog_parameters,
)
from query_understanding.compound_prompt_normalizer_provider import (
    AvailabilityNormalizerProviderResult,
)
from query_understanding.compound_prompt_normalizer_tools import (
    HELPER_TOOL_VERSION,
    availability_helper_tool_context,
)


QUERY_PLAN_VERSION = "v1"
PROMPT_NORMALIZATION_VERSION = "1.0.0"
UNAPPLIED_REASONS = {"no matching capability", "out of scope", "unclear intent"}
CLASS_A_MIN_AGREEMENT = 0.95
DEFAULT_CLASS_B_MIN_CORRECT = 0.70
MIN_PROMOTION_PROMPTS = 200
MIN_SCHEMA_VALID_RATE = 0.99
MAX_FALSE_POSITIVE_RATE = 0.02


@dataclass(frozen=True)
class PlanValidationResult:
    accepted: bool
    errors: tuple[str, ...]


def normalize_prompt_text(prompt: str) -> str:
    """Return the audit-normalized prompt used for span replay."""

    return " ".join(unicodedata.normalize("NFC", str(prompt or "")).split())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _require_mapping(value: Any, path: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _validate_span(span: Any, normalized_prompt: str, path: str, errors: list[str]) -> None:
    span_obj = _require_mapping(span, path, errors)
    text = span_obj.get("text")
    start = span_obj.get("start")
    end = span_obj.get("end")
    if not isinstance(text, str) or not text:
        errors.append(f"{path}.text must be a non-empty string")
        return
    if not isinstance(start, int) or isinstance(start, bool):
        errors.append(f"{path}.start must be an integer")
        return
    if not isinstance(end, int) or isinstance(end, bool):
        errors.append(f"{path}.end must be an integer")
        return
    if start < 0 or end > len(normalized_prompt) or start >= end:
        errors.append(f"{path} offsets must satisfy 0 <= start < end <= prompt length")
        return
    if normalized_prompt[start:end] != text:
        errors.append(f"{path} must replay against prompt_normalized")


def _catalog_family_ids(catalog: FilterCapabilityCatalog) -> set[str]:
    return set(catalog.families_by_id)


def _validate_soft_signal_hint(hint: Any, catalog: FilterCapabilityCatalog, path: str, errors: list[str]) -> None:
    if hint in (None, ""):
        return
    if not isinstance(hint, str):
        errors.append(f"{path}.hint must be a string")
        return
    match = re.fullmatch(r"([a-z0-9_]+)\.([A-Za-z0-9_]+)=(.+)", hint)
    if not match:
        errors.append(f"{path}.hint must be in family.field=value form")
        return
    family, field, raw_value = match.groups()
    row = catalog.families_by_id.get(family)
    if not row:
        errors.append(f"{path}.hint references unknown family: {family}")
        return
    schema = row.get("output_schema") if isinstance(row, Mapping) else {}
    properties = schema.get("properties") if isinstance(schema, Mapping) else {}
    if not isinstance(properties, Mapping) or field not in properties:
        errors.append(f"{path}.hint references unknown field: {family}.{field}")
        return
    field_schema = properties.get(field)
    if not isinstance(field_schema, Mapping):
        return
    if "enum" in field_schema and raw_value not in {str(value) for value in field_schema.get("enum", []) if value is not None}:
        errors.append(f"{path}.hint value is outside enum for {family}.{field}")
    bounds = row.get("plausibility_bounds") if isinstance(row, Mapping) else {}
    if isinstance(bounds, Mapping) and field in bounds:
        try:
            numeric_value = float(raw_value)
        except ValueError:
            errors.append(f"{path}.hint value must be numeric for {family}.{field}")
            return
        bound = bounds.get(field)
        if isinstance(bound, Mapping) and (numeric_value < bound.get("min") or numeric_value > bound.get("max")):
            errors.append(f"{path}.hint value is outside plausibility bounds for {family}.{field}")


def validate_query_plan_fixture(
    payload: Mapping[str, Any],
    *,
    prompt_normalized: str,
    catalog: FilterCapabilityCatalog | None = None,
) -> PlanValidationResult:
    """Validate a fixture payload against the normalizer wire contract."""

    loaded = catalog or load_filter_capability_catalog()
    errors: list[str] = []
    root = _require_mapping(payload, "query_plan", errors)
    family_ids = _catalog_family_ids(loaded)
    for key in ("version", "constraints", "soft_signals", "unapplied", "needs_review"):
        if key not in root:
            errors.append(f"query_plan.{key} is required")
    if root.get("version") != QUERY_PLAN_VERSION:
        errors.append("query_plan.version must equal 'v1'")

    for list_key in ("constraints", "soft_signals", "unapplied", "needs_review"):
        if list_key in root and not isinstance(root.get(list_key), list):
            errors.append(f"query_plan.{list_key} must be a list")

    for index, raw_constraint in enumerate(root.get("constraints") if isinstance(root.get("constraints"), list) else []):
        path = f"query_plan.constraints[{index}]"
        constraint = _require_mapping(raw_constraint, path, errors)
        family = constraint.get("filter_family")
        if family not in family_ids:
            errors.append(f"{path}.filter_family is unknown: {family}")
        parameters = constraint.get("parameters")
        if not isinstance(parameters, Mapping):
            errors.append(f"{path}.parameters must be an object")
        elif isinstance(family, str) and family in family_ids:
            try:
                validate_catalog_parameters(family, parameters, catalog=loaded)
            except ValueError as exc:
                errors.append(f"{path}.parameters invalid: {exc}")
        _validate_span(constraint.get("source_span"), prompt_normalized, f"{path}.source_span", errors)

    for index, raw_signal in enumerate(root.get("soft_signals") if isinstance(root.get("soft_signals"), list) else []):
        path = f"query_plan.soft_signals[{index}]"
        signal = _require_mapping(raw_signal, path, errors)
        _validate_span(signal.get("span"), prompt_normalized, f"{path}.span", errors)
        _validate_soft_signal_hint(signal.get("hint"), loaded, path, errors)

    for index, raw_item in enumerate(root.get("unapplied") if isinstance(root.get("unapplied"), list) else []):
        path = f"query_plan.unapplied[{index}]"
        item = _require_mapping(raw_item, path, errors)
        _validate_span(item.get("span"), prompt_normalized, f"{path}.span", errors)
        if item.get("reason") not in UNAPPLIED_REASONS:
            errors.append(f"{path}.reason must be one of {sorted(UNAPPLIED_REASONS)!r}")

    for index, raw_item in enumerate(root.get("needs_review") if isinstance(root.get("needs_review"), list) else []):
        path = f"query_plan.needs_review[{index}]"
        item = _require_mapping(raw_item, path, errors)
        _validate_span(item.get("span"), prompt_normalized, f"{path}.span", errors)
        families = item.get("candidate_families")
        if not isinstance(families, list) or not families:
            errors.append(f"{path}.candidate_families must be a non-empty list")
        else:
            for family_index, family in enumerate(families):
                if family not in family_ids:
                    errors.append(f"{path}.candidate_families[{family_index}] is unknown: {family}")
        if not isinstance(item.get("reason"), str) or not item.get("reason", "").strip():
            errors.append(f"{path}.reason must be a non-empty string")

    return PlanValidationResult(accepted=not errors, errors=tuple(errors))


def _availability_constraints(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _family_constraints(payload, "availability")


def _family_constraints(payload: Mapping[str, Any], family: str) -> list[Mapping[str, Any]]:
    constraints = payload.get("constraints") if isinstance(payload, Mapping) else []
    if not isinstance(constraints, list):
        return []
    result: list[Mapping[str, Any]] = []
    for item in constraints:
        if isinstance(item, Mapping) and item.get("filter_family") == family and isinstance(item.get("parameters"), Mapping):
            result.append(item)
    return result


def _case_expected_parameters(case: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = case.get(key)
    if isinstance(value, Mapping) and isinstance(value.get("parameters"), Mapping):
        return value.get("parameters")
    return None


def _parameters_equal(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if left is None or right is None:
        return False
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(right, sort_keys=True, separators=(",", ":"))


def _parameters_equal_except_display_value(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if left is None or right is None:
        return False
    left_copy = dict(left)
    right_copy = dict(right)
    left_copy.pop("display_value", None)
    right_copy.pop("display_value", None)
    return json.dumps(left_copy, sort_keys=True, separators=(",", ":")) == json.dumps(right_copy, sort_keys=True, separators=(",", ":"))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _span_replays(span: Mapping[str, Any], prompt_normalized: str) -> bool:
    text = span.get("text")
    start = span.get("start")
    end = span.get("end")
    return (
        isinstance(text, str)
        and isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 <= start < end <= len(prompt_normalized)
        and prompt_normalized[start:end] == text
    )


def _repair_span_offsets(
    item: dict[str, Any],
    span_key: str,
    *,
    prompt_normalized: str,
    path: str,
    repair_actions: list[str],
) -> None:
    if span_key not in item and all(key in item for key in ("text", "start", "end")):
        item[span_key] = {"text": item.pop("text"), "start": item.pop("start"), "end": item.pop("end")}
        repair_actions.append(f"{path}.{span_key}.wrapped_flat_span")
    span = item.get(span_key)
    if not isinstance(span, dict):
        return
    if _span_replays(span, prompt_normalized):
        return
    text = span.get("text")
    if not isinstance(text, str) or not text:
        return
    start = prompt_normalized.find(text)
    if start < 0 or prompt_normalized.find(text, start + 1) >= 0:
        return
    span["start"] = start
    span["end"] = start + len(text)
    repair_actions.append(f"{path}.{span_key}.offsets_replayed")


def repair_query_plan_payload(
    payload: Mapping[str, Any],
    *,
    prompt_normalized: str,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Repair mechanical span-shape issues without changing extracted intent."""

    if not isinstance(payload, Mapping):
        return {}, ()
    repaired = deepcopy(dict(payload))
    repair_actions: list[str] = []
    for list_key, span_key in (
        ("constraints", "source_span"),
        ("soft_signals", "span"),
        ("unapplied", "span"),
        ("needs_review", "span"),
    ):
        items = repaired.get(list_key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                _repair_span_offsets(
                    item,
                    span_key,
                    prompt_normalized=prompt_normalized,
                    path=f"query_plan.{list_key}[{index}]",
                    repair_actions=repair_actions,
                )
    return repaired, tuple(repair_actions)


def evaluate_availability_evidence_corpus(
    corpus: Mapping[str, Any],
    *,
    catalog: FilterCapabilityCatalog | None = None,
    class_b_min_correct: float = DEFAULT_CLASS_B_MIN_CORRECT,
) -> Mapping[str, Any]:
    """Evaluate a fixed availability normalizer evidence corpus."""

    return _evaluate_availability_payloads(
        corpus,
        catalog=catalog,
        class_b_min_correct=class_b_min_correct,
        mode="shadow_evidence_fixture",
        llm_invoked=False,
        class_a_rate_key="class_a_fixture_match_rate",
        class_a_gate_failure="class_a_fixture_match_rate_below_95_percent",
        class_b_recall_lift_status="measured_against_corpus_deterministic_baseline",
    )


def evaluate_vessel_tonnage_evidence_corpus(
    corpus: Mapping[str, Any],
    *,
    catalog: FilterCapabilityCatalog | None = None,
    class_b_min_correct: float = DEFAULT_CLASS_B_MIN_CORRECT,
) -> Mapping[str, Any]:
    """Evaluate a fixed vessel-tonnage normalizer evidence corpus."""

    return _evaluate_family_payloads(
        corpus,
        family="vessel_tonnage",
        catalog=catalog,
        class_b_min_correct=class_b_min_correct,
        mode="shadow_evidence_fixture",
        llm_invoked=False,
        class_a_rate_key="class_a_fixture_match_rate",
        class_a_gate_failure="class_a_fixture_match_rate_below_95_percent",
        class_b_recall_lift_status="measured_against_corpus_deterministic_baseline",
    )


def evaluate_availability_helper_tool_fixture_corpus(
    corpus: Mapping[str, Any],
    *,
    catalog: FilterCapabilityCatalog | None = None,
    class_b_min_correct: float = DEFAULT_CLASS_B_MIN_CORRECT,
) -> Mapping[str, Any]:
    """Evaluate fixture payloads while recording provider helper-tool audit fields."""

    loaded = catalog or load_filter_capability_catalog()
    payloads_by_case_id: dict[str, Mapping[str, Any]] = {}
    audit_records: list[Mapping[str, Any]] = []
    cases = corpus.get("cases") if isinstance(corpus.get("cases"), list) else []
    reference_date = str(corpus.get("reference_date") or "")
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id") or "")
        prompt_raw = str(case.get("prompt") or "")
        prompt_normalized = normalize_prompt_text(prompt_raw)
        payload = case.get("llm_query_plan") if isinstance(case.get("llm_query_plan"), Mapping) else {}
        payloads_by_case_id[case_id] = payload
        helper_outputs, helper_audit = availability_helper_tool_context(
            prompt_normalized,
            reference_date=reference_date,
            catalog=loaded,
        )
        validation = validate_query_plan_fixture(payload, prompt_normalized=prompt_normalized, catalog=loaded)
        audit_records.append(
            {
                "case_id": case_id,
                "prompt_hash": _sha256_text(prompt_raw),
                "prompt_normalized": prompt_normalized,
                "model_id": "fixture-helper-tools",
                "prompt_template_version": "fixture-helper-tools",
                "raw_llm_output": None,
                "raw_parsed_payload": payload,
                "parsed_payload": payload,
                "repair_actions": [],
                "transport_error": None,
                "validator_result": "accepted" if validation.accepted else "rejected",
                "validator_errors": list(validation.errors),
                "helper_tool_version": HELPER_TOOL_VERSION,
                "helper_tool_call_count": len(helper_audit),
                "helper_tool_calls": helper_audit,
                "helper_tool_context_count": len(helper_outputs),
            }
        )

    return _evaluate_availability_payloads(
        corpus,
        catalog=loaded,
        class_b_min_correct=class_b_min_correct,
        mode="shadow_helper_tool_fixture_evidence",
        llm_invoked=False,
        payloads_by_case_id=payloads_by_case_id,
        llm_audit_records=audit_records,
        class_a_rate_key="class_a_fixture_match_rate",
        class_a_gate_failure="class_a_fixture_match_rate_below_95_percent",
        class_b_recall_lift_status="measured_against_corpus_deterministic_baseline",
    )


def evaluate_availability_llm_corpus(
    corpus: Mapping[str, Any],
    *,
    provider,
    catalog: FilterCapabilityCatalog | None = None,
    class_b_min_correct: float = DEFAULT_CLASS_B_MIN_CORRECT,
) -> Mapping[str, Any]:
    """Evaluate an availability corpus by invoking a provider per prompt.

    The provider output is scored for evidence only. This function never
    dispatches constraints and never marks the family promoted.
    """

    return _evaluate_llm_corpus(
        corpus,
        family="availability",
        provider=provider,
        catalog=catalog,
        class_b_min_correct=class_b_min_correct,
    )


def evaluate_vessel_tonnage_llm_corpus(
    corpus: Mapping[str, Any],
    *,
    provider,
    catalog: FilterCapabilityCatalog | None = None,
    class_b_min_correct: float = DEFAULT_CLASS_B_MIN_CORRECT,
) -> Mapping[str, Any]:
    """Evaluate a vessel-tonnage corpus by invoking a provider per prompt."""

    return _evaluate_llm_corpus(
        corpus,
        family="vessel_tonnage",
        provider=provider,
        catalog=catalog,
        class_b_min_correct=class_b_min_correct,
    )


def _evaluate_llm_corpus(
    corpus: Mapping[str, Any],
    *,
    family: str,
    provider,
    catalog: FilterCapabilityCatalog | None,
    class_b_min_correct: float,
) -> Mapping[str, Any]:
    provider_payloads: dict[str, Mapping[str, Any]] = {}
    audit_records: list[Mapping[str, Any]] = []
    loaded = catalog or load_filter_capability_catalog()
    cases = corpus.get("cases") if isinstance(corpus.get("cases"), list) else []
    reference_date = str(corpus.get("reference_date") or "")
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id") or "")
        prompt_raw = str(case.get("prompt") or "")
        prompt_normalized = normalize_prompt_text(prompt_raw)
        result = provider(prompt_raw, prompt_normalized=prompt_normalized, reference_date=reference_date, catalog=loaded)
        if isinstance(result, AvailabilityNormalizerProviderResult):
            provider_result = result
        elif isinstance(result, Mapping):
            provider_result = AvailabilityNormalizerProviderResult(
                model_id=str(result.get("model_id") or ""),
                prompt_template_version=str(result.get("prompt_template_version") or ""),
                raw_llm_output=result.get("raw_llm_output") if isinstance(result.get("raw_llm_output"), str) else None,
                parsed_payload=result.get("parsed_payload") if isinstance(result.get("parsed_payload"), Mapping) else None,
                transport_error=result.get("transport_error") if isinstance(result.get("transport_error"), str) else None,
                helper_tool_version=result.get("helper_tool_version") if isinstance(result.get("helper_tool_version"), str) else None,
                helper_tool_calls=tuple(result.get("helper_tool_calls") if isinstance(result.get("helper_tool_calls"), list) else ()),
            )
        else:
            provider_result = AvailabilityNormalizerProviderResult(
                model_id="",
                prompt_template_version="",
                raw_llm_output=None,
                parsed_payload=None,
                transport_error="provider_returned_invalid_result",
            )
        raw_payload = provider_result.parsed_payload if isinstance(provider_result.parsed_payload, Mapping) else {}
        payload, repair_actions = repair_query_plan_payload(raw_payload, prompt_normalized=prompt_normalized)
        provider_payloads[case_id] = payload
        validation = validate_query_plan_fixture(payload, prompt_normalized=prompt_normalized, catalog=loaded)
        audit_records.append(
            {
                "case_id": case_id,
                "prompt_hash": _sha256_text(prompt_raw),
                "prompt_normalized": prompt_normalized,
                "model_id": provider_result.model_id,
                "prompt_template_version": provider_result.prompt_template_version,
                "raw_llm_output": provider_result.raw_llm_output,
                "raw_parsed_payload": provider_result.parsed_payload,
                "parsed_payload": payload,
                "repair_actions": list(repair_actions),
                "transport_error": provider_result.transport_error,
                "validator_result": "accepted" if validation.accepted else "rejected",
                "validator_errors": list(validation.errors),
                "helper_tool_version": provider_result.helper_tool_version,
                "helper_tool_call_count": len(provider_result.helper_tool_calls),
                "helper_tool_calls": list(provider_result.helper_tool_calls),
            }
        )

    return _evaluate_family_payloads(
        corpus,
        family=family,
        catalog=loaded,
        class_b_min_correct=class_b_min_correct,
        mode="shadow_llm_evidence",
        llm_invoked=True,
        payloads_by_case_id=provider_payloads,
        llm_audit_records=audit_records,
        class_a_rate_key="class_a_llm_match_rate",
        class_a_gate_failure="class_a_llm_match_rate_below_95_percent",
        class_b_recall_lift_status="measured_against_corpus_deterministic_baseline",
    )


def _evaluate_availability_payloads(
    corpus: Mapping[str, Any],
    *,
    catalog: FilterCapabilityCatalog | None,
    class_b_min_correct: float,
    mode: str,
    llm_invoked: bool,
    class_a_rate_key: str,
    class_a_gate_failure: str,
    class_b_recall_lift_status: str,
    payloads_by_case_id: Mapping[str, Mapping[str, Any]] | None = None,
    llm_audit_records: list[Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    return _evaluate_family_payloads(
        corpus,
        family="availability",
        catalog=catalog,
        class_b_min_correct=class_b_min_correct,
        mode=mode,
        llm_invoked=llm_invoked,
        class_a_rate_key=class_a_rate_key,
        class_a_gate_failure=class_a_gate_failure,
        class_b_recall_lift_status=class_b_recall_lift_status,
        payloads_by_case_id=payloads_by_case_id,
        llm_audit_records=llm_audit_records,
    )


def _evaluate_family_payloads(
    corpus: Mapping[str, Any],
    *,
    family: str,
    catalog: FilterCapabilityCatalog | None,
    class_b_min_correct: float,
    mode: str,
    llm_invoked: bool,
    class_a_rate_key: str,
    class_a_gate_failure: str,
    class_b_recall_lift_status: str,
    payloads_by_case_id: Mapping[str, Mapping[str, Any]] | None = None,
    llm_audit_records: list[Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    loaded = catalog or load_filter_capability_catalog()
    cases = corpus.get("cases") if isinstance(corpus.get("cases"), list) else []
    case_results: list[Mapping[str, Any]] = []
    class_counts = {"A": 0, "B": 0, "C": 0}
    schema_valid = 0
    unsafe_widening = 0
    false_positive_reviewed = 0
    false_positive_review_denominator = 0
    class_a_total = class_a_agree = 0
    class_b_total = class_b_correct = 0
    class_b_baseline_correct = 0
    class_c_total = class_c_safe = 0
    helper_tool_call_count = 0
    helper_tool_accepted_count = 0
    helper_tool_rejected_count = 0
    failure_reason_counts: dict[str, int] = {}
    quality_failure_class_by_case_id: dict[str, str] = {}
    unsafe_widening_by_case_id: dict[str, bool] = {}
    if llm_audit_records:
        for record in llm_audit_records:
            calls = record.get("helper_tool_calls") if isinstance(record, Mapping) else []
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                helper_tool_call_count += 1
                if call.get("accepted") is True:
                    helper_tool_accepted_count += 1
                else:
                    helper_tool_rejected_count += 1

    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id") or "")
        prompt_raw = str(case.get("prompt") or "")
        prompt_normalized = normalize_prompt_text(prompt_raw)
        if payloads_by_case_id is not None:
            payload = payloads_by_case_id.get(case_id) if isinstance(payloads_by_case_id.get(case_id), Mapping) else {}
        else:
            payload = case.get("llm_query_plan") if isinstance(case.get("llm_query_plan"), Mapping) else {}
        validation = validate_query_plan_fixture(payload, prompt_normalized=prompt_normalized, catalog=loaded)
        if validation.accepted:
            schema_valid += 1

        case_class = str(case.get("class") or "")
        if case_class in class_counts:
            class_counts[case_class] += 1
        constraints = _family_constraints(payload, family)
        emitted_constraint = bool(constraints)
        first_parameters = constraints[0].get("parameters") if constraints else None
        expected_route = str(case.get("expected_route") or "")
        safe_review_route = not emitted_constraint and (
            bool(payload.get("unapplied") if isinstance(payload, Mapping) else [])
            or bool(payload.get("needs_review") if isinstance(payload, Mapping) else [])
        )
        status = "not_scored"
        correct = False
        unsafe = False
        failure_reason = ""

        if case_class == "A":
            class_a_total += 1
            expected = _case_expected_parameters(case, "deterministic_baseline")
            correct = validation.accepted and emitted_constraint and _parameters_equal(first_parameters, expected)
            if correct:
                class_a_agree += 1
            status = "agreed" if correct else "disagreed"
            if not correct:
                if not validation.accepted:
                    failure_reason = "schema_invalid"
                elif not emitted_constraint:
                    failure_reason = "missing_constraint"
                elif _parameters_equal_except_display_value(first_parameters, expected):
                    failure_reason = "display_value_mismatch_only"
                else:
                    failure_reason = "parameter_mismatch"
            false_positive_review_denominator += 1
        elif case_class == "B":
            class_b_total += 1
            expected = _case_expected_parameters(case, "human_label")
            baseline = _case_expected_parameters(case, "deterministic_baseline")
            correct = validation.accepted and emitted_constraint and _parameters_equal(first_parameters, expected)
            if correct:
                class_b_correct += 1
            if _parameters_equal(baseline, expected):
                class_b_baseline_correct += 1
            status = "correct" if correct else "incorrect"
            if not correct:
                if not validation.accepted:
                    failure_reason = "schema_invalid"
                elif not emitted_constraint:
                    failure_reason = "missing_constraint"
                elif _parameters_equal_except_display_value(first_parameters, expected):
                    failure_reason = "display_value_mismatch_only"
                else:
                    failure_reason = "parameter_mismatch"
            false_positive_review_denominator += 1
        elif case_class == "C":
            class_c_total += 1
            correct = validation.accepted and safe_review_route and expected_route in {"needs_review", "unapplied"}
            if correct:
                class_c_safe += 1
            unsafe = validation.accepted and emitted_constraint
            if unsafe:
                unsafe_widening += 1
            status = "safe_route" if correct else "unsafe_or_invalid"
            if not correct:
                if not validation.accepted:
                    failure_reason = "schema_invalid"
                elif emitted_constraint:
                    failure_reason = "class_c_emitted_constraint"
                else:
                    failure_reason = "class_c_missing_review_route"

        if bool(case.get("reviewed_false_positive")):
            false_positive_reviewed += 1
        if failure_reason:
            failure_reason_counts[failure_reason] = failure_reason_counts.get(failure_reason, 0) + 1
        quality_failure_class_by_case_id[case_id] = failure_reason
        unsafe_widening_by_case_id[case_id] = unsafe

        case_results.append(
            {
                "id": case_id,
                "class": case_class,
                "prompt_hash": _sha256_text(prompt_raw),
                "prompt_normalized": prompt_normalized,
                "schema_valid": validation.accepted,
                "validator_errors": list(validation.errors),
                "emitted_constraint": emitted_constraint,
                "expected_route": expected_route,
                "status": status,
                "failure_reason": failure_reason,
                "quality_failure_class": failure_reason,
                "unsafe_widening": unsafe,
            }
        )

    total = len(cases)
    class_a_rate = _rate(class_a_agree, class_a_total)
    class_b_rate = _rate(class_b_correct, class_b_total)
    class_b_baseline_rate = _rate(class_b_baseline_correct, class_b_total)
    class_b_recall_lift = None if class_b_rate is None or class_b_baseline_rate is None else class_b_rate - class_b_baseline_rate
    class_c_rate = _rate(class_c_safe, class_c_total)
    schema_valid_rate = _rate(schema_valid, total) or 0.0
    reviewed_false_positive_rate = _rate(false_positive_reviewed, false_positive_review_denominator) or 0.0

    gate_failures: list[str] = []
    if total < MIN_PROMOTION_PROMPTS:
        gate_failures.append("corpus_size_below_200")
    if schema_valid_rate < MIN_SCHEMA_VALID_RATE:
        gate_failures.append("schema_valid_rate_below_99_percent")
    if unsafe_widening:
        gate_failures.append("unsafe_widening_present")
    if not llm_invoked:
        gate_failures.append("real_llm_run_required")
    if class_a_rate is None or class_a_rate < CLASS_A_MIN_AGREEMENT:
        gate_failures.append(class_a_gate_failure)
    if class_b_rate is None or class_b_rate < class_b_min_correct:
        gate_failures.append("class_b_human_label_fixture_rate_below_floor")
    if class_b_recall_lift is None:
        gate_failures.append("class_b_recall_lift_not_measured")
    elif class_b_recall_lift <= 0:
        gate_failures.append("class_b_recall_lift_not_positive")
    if class_c_rate is None or class_c_rate < 1.0:
        gate_failures.append("class_c_not_all_safe")
    if reviewed_false_positive_rate >= MAX_FALSE_POSITIVE_RATE:
        gate_failures.append("reviewed_false_positive_rate_not_below_2_percent")
    enriched_audit_records: list[Mapping[str, Any]] = []
    for record in llm_audit_records or []:
        if not isinstance(record, Mapping):
            continue
        case_id = str(record.get("case_id") or "")
        enriched = dict(record)
        enriched["quality_failure_class"] = quality_failure_class_by_case_id.get(case_id, "")
        enriched["unsafe_widening"] = bool(unsafe_widening_by_case_id.get(case_id))
        enriched_audit_records.append(enriched)

    return {
        "version": f"{family}_normalizer_evidence.v1",
        "family": family,
        "catalog_version": loaded.version,
        "prompt_normalization_version": PROMPT_NORMALIZATION_VERSION,
        "corpus_id": corpus.get("corpus_id"),
        "corpus_version": corpus.get("version"),
        "mode": mode,
        "llm_invoked": llm_invoked,
        "live_dispatch": False,
        "promoted_family": False,
        "summary": {
            "total_cases": total,
            "class_counts": class_counts,
            "schema_valid_count": schema_valid,
            "schema_valid_rate": schema_valid_rate,
            "unsafe_widening_count": unsafe_widening,
            class_a_rate_key: class_a_rate,
            "class_b_correct_rate_against_human_label": class_b_rate,
            "class_b_deterministic_baseline_correct_rate": class_b_baseline_rate,
            "class_b_recall_lift": class_b_recall_lift,
            "class_b_recall_lift_status": class_b_recall_lift_status,
            "class_c_safe_route_rate": class_c_rate,
            "reviewed_false_positive_rate": reviewed_false_positive_rate,
            "helper_tool_call_count": helper_tool_call_count,
            "helper_tool_accepted_count": helper_tool_accepted_count,
            "helper_tool_rejected_count": helper_tool_rejected_count,
            "failure_reason_counts": dict(failure_reason_counts),
            "quality_failure_class_counts": dict(failure_reason_counts),
        },
        "promotion_gate": {
            "passes": not gate_failures,
            "failures": gate_failures,
            "thresholds": {
                "min_prompts": MIN_PROMOTION_PROMPTS,
                "min_schema_valid_rate": MIN_SCHEMA_VALID_RATE,
                "min_class_a_agreement": CLASS_A_MIN_AGREEMENT,
                "min_class_b_correct_against_human_label": class_b_min_correct,
                "max_reviewed_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
                "requires_real_llm_run": True,
                "zero_unsafe_widening": True,
            },
        },
        "case_results": case_results,
        "llm_audit_records": enriched_audit_records,
    }


def load_corpus(path: Path) -> Mapping[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def evaluate_corpus_file(path: Path) -> Mapping[str, Any]:
    return evaluate_availability_evidence_corpus(load_corpus(path))


def write_report(report: Mapping[str, Any], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
