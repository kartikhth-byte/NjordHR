"""`query_plan.v1` schema validation and normalization."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .hard_filter_catalog import (
    ACTIVE_FAMILY_IDS,
    CATALOG_VERSION,
    SUPPORTED_FAMILY_IDS,
    UNAPPLIED_FAMILY_IDS,
    canonical_certificate_values,
    canonical_endorsement_values,
    canonical_engine_family_values,
    canonical_rank_values,
    canonical_ship_family_values,
    canonical_ui_filter_type,
    get_family_spec,
    is_active_family,
    is_unsupported_family,
    legacy_applied_constraint_id,
    legacy_hard_constraint_key,
)

QUERY_PLAN_SCHEMA_VERSION = "query_plan.v1"
SUPPORTED_VISA_GROUPS = {"usa", "australia", "schengen"}
VALID_STATUSES = {"valid", "invalid", "degraded"}
VALID_CONSTRAINT_MODES = {"required", "preferred"}
VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
VALID_UNAPPLIED_REASONS = {
    "unsupported_filter_family",
    "unsupported_value",
    "ambiguous_value",
    "insufficient_schema",
    "validation_failed",
}
VALID_SUGGESTED_HANDLING = {"block_search", "semantic_with_warning", "ignore_with_warning"}
VALID_WARNING_SEVERITIES = {"info", "warning", "error"}
MANDATORY_REQUIREMENT_TERMS = r"(?:passport|visa|coc|stcw\s+basic|certificate(?:\s+of\s+competency)?|endorsement)"
MANDATORY_CUE_TERMS = r"(?:must|need(?:s)?(?:\s+to)?|require(?:s|d)?|mandatory|compulsory|should(?:\s+have)?|have\s+to|has\s+to)"
MANDATORY_NEGATION_PATTERNS = (
    rf"\b(?:not|never|no(?:\s+longer)?)\b(?:\W+\w+){{0,3}}?\W+\b(?:required|mandatory|needed|need(?:ed)?|necessary)\b",
    rf"\b(?:required|mandatory|needed|need(?:ed)?|necessary)\b(?:\W+\w+){{0,3}}?\W+\b(?:not|never|no(?:\s+longer)?)\b",
    rf"\b{MANDATORY_REQUIREMENT_TERMS}\b(?:\W+\w+){{0,4}}?\W+\b(?:not|never|no(?:\s+longer)?)\b(?:\W+\w+){{0,3}}?\W+\b(?:required|mandatory|needed|need(?:ed)?|necessary)\b",
    rf"\b(?:not|never|no(?:\s+longer)?)\b(?:\W+\w+){{0,4}}?\W+\b{MANDATORY_REQUIREMENT_TERMS}\b",
)
MANDATORY_FORWARDS_PATTERN = rf"\b{MANDATORY_CUE_TERMS}\b(?:\W+\w+){{0,6}}?\W+\b(?:valid\s+)?{MANDATORY_REQUIREMENT_TERMS}\b"
MANDATORY_BACKWARDS_PATTERN = rf"\b(?:valid\s+)?{MANDATORY_REQUIREMENT_TERMS}\b(?:\W+\w+){{0,6}}?\W+\b{MANDATORY_CUE_TERMS}\b"
MANDATORY_STRONG_PATTERNS = (
    rf"\bvalid\s+{MANDATORY_REQUIREMENT_TERMS}\b",
    rf"\b{MANDATORY_REQUIREMENT_TERMS}\s+(?:required|mandatory|needed|need(?:ed)?|necessary|valid(?:ity)?)\b",
    r"\bpassport\s+valid(?:ity)?\b",
    r"\bus\s+visa\b",
)

FATAL_ERROR_CODES = {
    "missing_required_top_level_fields",
    "unknown_top_level_keys",
    "invalid_normalizer",
    "unknown_normalizer_keys",
    "invalid_normalizer_name",
    "invalid_normalizer_model",
    "invalid_prompt_template_version",
    "invalid_catalog_version",
    "invalid_created_at",
    "invalid_input",
    "unknown_input_keys",
    "invalid_raw_prompt",
    "invalid_rank_context",
    "invalid_ui_filters",
    "unknown_ui_filters_keys",
    "invalid_ui_filters_schema_version",
    "invalid_ui_filters_list",
    "invalid_ui_filter_item",
    "unknown_ui_filter_keys",
    "invalid_ui_filter_id",
    "unknown_ui_filter_id",
    "invalid_ui_filter_mode",
    "invalid_ui_filter_source",
    "invalid_ui_filter_value",
    "invalid_applied_constraints",
    "invalid_logical_groups",
    "invalid_logical_group_item",
    "unknown_logical_group_keys",
    "invalid_logical_group_id",
    "invalid_logical_group_type",
    "invalid_logical_group_mode",
    "invalid_logical_group_source_text",
    "invalid_logical_group_confidence",
    "invalid_logical_group_children",
    "invalid_logical_group_child",
    "logical_group_child_demoted",
    "invalid_unapplied_constraints",
    "invalid_unrecognized_residual",
    "invalid_warnings",
    "invalid_applied_constraint_item",
    "invalid_unapplied_constraint_item",
    "invalid_residual_item",
    "invalid_warning_item",
    "invalid_semantic_query",
    "invalid_schema_version",
    "mandatory_marker_in_semantic_query",
    "unknown_constraint_keys",
    "unknown_unapplied_constraint_keys",
    "unknown_compatibility_keys",
    "invalid_constraint_id",
    "invalid_constraint_mode",
    "invalid_source_text",
    "invalid_confidence",
    "invalid_compatibility",
    "invalid_compatibility_field",
    "invalid_unapplied_id",
    "invalid_unapplied_mode",
    "invalid_unapplied_reason",
    "invalid_unapplied_source_text",
    "invalid_suggested_handling",
    "invalid_unapplied_confidence",
    "invalid_residual_text",
    "invalid_residual_handling",
    "invalid_warning_code",
    "invalid_warning_message",
    "invalid_warning_severity",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_string_or_none(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_list_of_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _canonicalize_list(value: Sequence[Any]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _validate_positive_int_or_null(value: Any, *, path: str, field_name: str) -> List[Dict[str, str]]:
    if value is None:
        return []
    if not _is_int(value):
        return [_error(f"invalid_{field_name}", path, f"{field_name} must be an integer or null")]
    if value <= 0:
        return [_error(f"invalid_{field_name}", path, f"{field_name} must be positive")]
    return []


def _normalize_experience_filter_item(
    item: Mapping[str, Any],
    *,
    family_id: str,
    value_field: str,
    valid_values: Iterable[str],
    path: str,
) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    allowed_keys = {value_field, "minimum_months", "years_back", "contract_count"}
    unknown = sorted(set(item.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_experience_filter_item_keys", path, f"Unknown keys: {', '.join(unknown)}"))

    canonical_value = item.get(value_field)
    if not isinstance(canonical_value, str) or canonical_value not in set(valid_values):
        errors.append(_error(f"invalid_{value_field}", f"{path}.{value_field}", f"{value_field} must be canonical"))

    minimum_months = item.get("minimum_months")
    if not (_is_int(minimum_months) or minimum_months is None):
        errors.append(_error("invalid_minimum_months", f"{path}.minimum_months", "minimum_months must be an integer or null"))
    elif isinstance(minimum_months, int) and minimum_months < 0:
        errors.append(_error("invalid_minimum_months", f"{path}.minimum_months", "minimum_months must be zero or positive"))

    years_back = item.get("years_back")
    contract_count = item.get("contract_count")
    errors.extend(_validate_positive_int_or_null(years_back, path=f"{path}.years_back", field_name="years_back"))
    errors.extend(_validate_positive_int_or_null(contract_count, path=f"{path}.contract_count", field_name="contract_count"))
    if years_back is not None and contract_count is not None:
        errors.append(_error(f"{family_id}_recency_ambiguous", path, "years_back and contract_count are mutually exclusive per item"))

    if errors:
        return None, errors
    return {
        value_field: canonical_value,
        "minimum_months": minimum_months,
        "years_back": years_back,
        "contract_count": contract_count,
    }, errors


def _dedupe_experience_filter_items(
    items: Sequence[Mapping[str, Any]],
    *,
    value_field: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    deduped: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []
    seen = set()
    removed_count = 0
    for item in items:
        key = (
            item.get(value_field),
            item.get("minimum_months"),
            item.get("years_back"),
            item.get("contract_count"),
        )
        if key in seen:
            removed_count += 1
            continue
        seen.add(key)
        deduped.append(dict(item))
    if removed_count:
        warnings.append(_warning("duplicate_filter_rows", f"Duplicate filter ignored; removed_count={removed_count}", "info"))
    return deduped, warnings


def _validate_experience_filter_payload(
    payload: Mapping[str, Any],
    *,
    family_id: str,
    value_field: str,
    valid_values: Iterable[str],
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    match_mode = payload.get("match_mode")
    if match_mode != "any_of":
        errors.append(_error(f"{family_id}_unsupported_match_mode", "constraint.match_mode", "match_mode must be any_of"))
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append(_error("invalid_experience_filter_items", "constraint.items", "items must be a non-empty list"))
        items = []

    normalized_items: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(_error("invalid_experience_filter_item", f"constraint.items[{index}]", "item must be an object"))
            continue
        normalized_item, item_errors = _normalize_experience_filter_item(
            item,
            family_id=family_id,
            value_field=value_field,
            valid_values=valid_values,
            path=f"constraint.items[{index}]",
        )
        errors.extend(item_errors)
        if normalized_item is not None:
            normalized_items.append(normalized_item)

    normalized_items, dedupe_warnings = _dedupe_experience_filter_items(normalized_items, value_field=value_field)
    warnings.extend(dedupe_warnings)
    result: Dict[str, Any] = {"type": family_id, "match_mode": "any_of", "items": normalized_items}
    if warnings:
        result["_validation_warnings"] = warnings
    return result, errors


def _error(code: str, path: str, message: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _warning(code: str, message: str, severity: str = "warning") -> Dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _has_positive_mandatory_marker(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in MANDATORY_STRONG_PATTERNS):
        return True
    return bool(
        re.search(MANDATORY_FORWARDS_PATTERN, lowered, flags=re.IGNORECASE)
        or re.search(MANDATORY_BACKWARDS_PATTERN, lowered, flags=re.IGNORECASE)
    )


def _has_mandatory_negation(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in MANDATORY_NEGATION_PATTERNS)


def _strip_mandatory_negations(text: str) -> str:
    cleaned = str(text or "")
    for pattern in MANDATORY_NEGATION_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _contains_mandatory_marker(text: str) -> bool:
    cleaned_text = _strip_mandatory_negations(text)
    return _has_positive_mandatory_marker(cleaned_text)


def _copy_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    return deepcopy(dict(plan))


def _default_validation(status: str, errors: List[Dict[str, str]] | None = None) -> Dict[str, Any]:
    return {"status": status, "errors": errors or []}


def _validate_top_level(plan: Mapping[str, Any], mode: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    required_keys = {
        "schema_version",
        "normalizer",
        "input",
        "applied_constraints",
        "unapplied_constraints",
        "semantic_query",
        "unrecognized_residual",
        "warnings",
        "validation",
    }
    optional_keys = {"logical_groups"}
    missing = sorted(required_keys - set(plan.keys()))
    if missing:
        errors.append(_error("missing_required_top_level_fields", "root", f"Missing required keys: {', '.join(missing)}"))

    unknown = sorted(set(plan.keys()) - required_keys - optional_keys)
    if unknown:
        message = f"Unknown top-level keys: {', '.join(unknown)}"
        if mode == "production":
            errors.append(_error("unknown_top_level_keys", "root", message))
        else:
            warnings.append(_warning("unknown_top_level_keys", message))
    return errors, warnings


def _validate_normalizer(normalizer: Any) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(normalizer, Mapping):
        return None, [_error("invalid_normalizer", "normalizer", "normalizer must be an object")]

    allowed_keys = {"name", "model", "prompt_template_version", "catalog_version", "created_at"}
    unknown = sorted(set(normalizer.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_normalizer_keys", "normalizer", f"Unknown keys: {', '.join(unknown)}"))

    if normalizer.get("name") not in {"llm", "legacy", "hybrid"}:
        errors.append(_error("invalid_normalizer_name", "normalizer.name", "normalizer.name must be llm, legacy, or hybrid"))
    if not _is_string_or_none(normalizer.get("model")):
        errors.append(_error("invalid_normalizer_model", "normalizer.model", "normalizer.model must be a string or null"))
    if not isinstance(normalizer.get("prompt_template_version"), str) or not normalizer.get("prompt_template_version"):
        errors.append(_error("invalid_prompt_template_version", "normalizer.prompt_template_version", "prompt_template_version must be a non-empty string"))
    if not isinstance(normalizer.get("catalog_version"), str) or not normalizer.get("catalog_version"):
        errors.append(_error("invalid_catalog_version", "normalizer.catalog_version", "catalog_version must be a non-empty string"))
    if not isinstance(normalizer.get("created_at"), str) or not normalizer.get("created_at"):
        errors.append(_error("invalid_created_at", "normalizer.created_at", "created_at must be an ISO-8601 string"))
    return dict(normalizer), errors


def _validate_ui_filters(ui_filters: Any, mode: str) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(ui_filters, Mapping):
        return None, [_error("invalid_ui_filters", "input.ui_filters", "ui_filters must be an object")]

    allowed_keys = {"schema_version", "filters"}
    unknown = sorted(set(ui_filters.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_ui_filters_keys", "input.ui_filters", f"Unknown keys: {', '.join(unknown)}"))

    if ui_filters.get("schema_version") != "ui_filters.v1":
        errors.append(_error("invalid_ui_filters_schema_version", "input.ui_filters.schema_version", "ui_filters.schema_version must be ui_filters.v1"))

    filters = ui_filters.get("filters")
    if not isinstance(filters, list):
        errors.append(_error("invalid_ui_filters_list", "input.ui_filters.filters", "ui_filters.filters must be a list"))
        return dict(ui_filters), errors

    for index, item in enumerate(filters):
        path = f"input.ui_filters.filters[{index}]"
        if not isinstance(item, Mapping):
            errors.append(_error("invalid_ui_filter_item", path, "UI filter entry must be an object"))
            continue
        allowed_item_keys = {"id", "value", "mode", "source"}
        unknown_item_keys = sorted(set(item.keys()) - allowed_item_keys)
        if unknown_item_keys:
            errors.append(_error("unknown_ui_filter_keys", path, f"Unknown keys: {', '.join(unknown_item_keys)}"))

        filter_id = item.get("id")
        if not isinstance(filter_id, str) or not filter_id:
            errors.append(_error("invalid_ui_filter_id", f"{path}.id", "UI filter id must be a non-empty string"))
        elif canonical_ui_filter_type(filter_id) is None:
            message = f"Unsupported UI filter id: {filter_id}"
            if mode == "production":
                errors.append(_error("unknown_ui_filter_id", f"{path}.id", message))
            else:
                errors.append(_error("unknown_ui_filter_id", f"{path}.id", message))

        if item.get("mode") not in {"required", "preferred"}:
            errors.append(_error("invalid_ui_filter_mode", f"{path}.mode", "UI filter mode must be required or preferred"))
        if item.get("source") != "ui":
            errors.append(_error("invalid_ui_filter_source", f"{path}.source", "UI filter source must be ui"))
        if not (isinstance(item.get("value"), (str, Mapping, list, int, float, bool)) or item.get("value") is None):
            errors.append(_error("invalid_ui_filter_value", f"{path}.value", "UI filter value must be a primitive, object, list, or null"))

    return dict(ui_filters), errors


def _validate_payload_family(family_id: str, payload: Any) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(payload, Mapping):
        return None, [_error("invalid_constraint_payload", f"constraint[{family_id}]", "constraint must be an object")]

    if family_id == "age_range":
        if payload.get("type") != "age_range":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be age_range"))
        minimum_years = payload.get("minimum_years")
        maximum_years = payload.get("maximum_years")
        if not (_is_int(minimum_years) or minimum_years is None):
            errors.append(_error("invalid_minimum_years", "constraint.minimum_years", "minimum_years must be an integer or null"))
        if not (_is_int(maximum_years) or maximum_years is None):
            errors.append(_error("invalid_maximum_years", "constraint.maximum_years", "maximum_years must be an integer or null"))
        if minimum_years is None and maximum_years is None:
            errors.append(_error("missing_age_bound", "constraint", "age_range must include at least one bound"))
        if _is_int(minimum_years) and _is_int(maximum_years) and minimum_years > maximum_years:
            errors.append(_error("invalid_age_range_order", "constraint", "minimum_years cannot exceed maximum_years"))
        return {"type": "age_range", "minimum_years": minimum_years, "maximum_years": maximum_years}, errors

    if family_id == "rank_match":
        if payload.get("type") != "rank_match":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be rank_match"))
        rank = payload.get("rank")
        if not isinstance(rank, str) or rank not in canonical_rank_values():
            errors.append(_error("invalid_rank", "constraint.rank", "rank must be a canonical rank"))
        return {"type": "rank_match", "rank": rank}, errors

    if family_id == "coc_document_gate":
        if payload.get("type") != "coc_document_gate":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be coc_document_gate"))
        if payload.get("required") is not True:
            errors.append(_error("invalid_required", "constraint.required", "required must be true"))
        return {"type": "coc_document_gate", "required": True}, errors

    if family_id == "coc_country_match":
        if payload.get("type") != "coc_country_match":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be coc_country_match"))
        countries = payload.get("countries")
        if not isinstance(countries, list) or not countries:
            errors.append(_error("invalid_coc_countries", "constraint.countries", "countries must be a non-empty list"))
            normalized_countries = []
        else:
            normalized_countries = []
            for index, country in enumerate(countries):
                if not isinstance(country, str) or not country.strip():
                    errors.append(_error("invalid_coc_country", f"constraint.countries[{index}]", "country must be a non-empty string"))
                    continue
                normalized_countries.append(" ".join(country.lower().split()))
        return {
            "type": "coc_country_match",
            "countries": normalized_countries,
            "operator": payload.get("operator") if payload.get("operator") in {"contains_any", "equals"} else "contains_any",
        }, errors

    if family_id == "coc_grade_match":
        if payload.get("type") != "coc_grade_match":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be coc_grade_match"))
        grade = payload.get("grade")
        if not isinstance(grade, str) or grade not in canonical_rank_values():
            errors.append(_error("invalid_coc_grade", "constraint.grade", "grade must be a canonical CoC grade"))
        return {"type": "coc_grade_match", "grade": grade}, errors

    if family_id == "stcw_basic":
        if payload.get("type") != "stcw_basic":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be stcw_basic"))
        if payload.get("required") is not True:
            errors.append(_error("invalid_required", "constraint.required", "required must be true"))
        return {"type": "stcw_basic", "required": True}, errors

    if family_id == "us_visa":
        if payload.get("type") != "us_visa":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be us_visa"))
        if payload.get("required") is not True:
            errors.append(_error("invalid_required", "constraint.required", "required must be true"))
        months = payload.get("minimum_months_remaining")
        if not (_is_int(months) or months is None):
            errors.append(_error("invalid_months", "constraint.minimum_months_remaining", "minimum_months_remaining must be an integer or null"))
        visa_group = payload.get("visa_group")
        if visa_group is not None:
            if not isinstance(visa_group, str) or not visa_group.strip() or visa_group.strip().lower() not in SUPPORTED_VISA_GROUPS:
                errors.append(_error("invalid_visa_group", "constraint.visa_group", f"visa_group must be one of {sorted(SUPPORTED_VISA_GROUPS)} or null"))
        accepted_types = payload.get("accepted_types")
        if accepted_types is not None:
            if not isinstance(accepted_types, list) or not all(isinstance(item, str) and item.strip() for item in accepted_types):
                errors.append(_error("invalid_accepted_types", "constraint.accepted_types", "accepted_types must be a list of non-empty strings or null"))
            else:
                accepted_types = [item.strip() for item in accepted_types if item.strip()]
        normalized_visa_group = visa_group.strip().lower() if isinstance(visa_group, str) and visa_group.strip() else None
        return {
            "type": "us_visa",
            "required": True,
            "minimum_months_remaining": months,
            "visa_group": normalized_visa_group,
            "accepted_types": accepted_types,
        }, errors

    if family_id == "passport_validity":
        if payload.get("type") != "passport_validity":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be passport_validity"))
        must_be_valid = payload.get("must_be_valid")
        if must_be_valid is not True:
            errors.append(_error("invalid_required", "constraint.must_be_valid", "must_be_valid must be true"))
        months = payload.get("minimum_months_remaining")
        if months is not None:
            if not _is_int(months):
                errors.append(_error("invalid_months", "constraint.minimum_months_remaining", "minimum_months_remaining must be an integer or null"))
            elif months <= 0:
                errors.append(_error("invalid_months", "constraint.minimum_months_remaining", "minimum_months_remaining must be positive"))
        return {"type": "passport_validity", "must_be_valid": True, "minimum_months_remaining": months}, errors

    if family_id == "recent_contract_vessel_experience":
        if payload.get("type") != "recent_contract_vessel_experience":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be recent_contract_vessel_experience"))
        ship_family = payload.get("ship_family")
        minimum_months = payload.get("minimum_months")
        recent_contract_count = payload.get("recent_contract_count")
        if not isinstance(ship_family, str) or ship_family not in canonical_ship_family_values():
            errors.append(_error("invalid_ship_family", "constraint.ship_family", "ship_family must be canonical"))
        if not (_is_int(minimum_months) or minimum_months is None):
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be an integer or null"))
        if not _is_int(recent_contract_count):
            errors.append(_error("invalid_recent_contract_count", "constraint.recent_contract_count", "recent_contract_count must be an integer"))
        elif recent_contract_count <= 0:
            errors.append(_error("invalid_recent_contract_count", "constraint.recent_contract_count", "recent_contract_count must be positive"))
        return {
            "type": "recent_contract_vessel_experience",
            "ship_family": ship_family,
            "minimum_months": minimum_months,
            "recent_contract_count": recent_contract_count,
        }, errors

    if family_id == "engine_experience":
        if payload.get("type") != "engine_experience":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be engine_experience"))
        if "items" in payload:
            normalized_payload, item_errors = _validate_experience_filter_payload(
                payload,
                family_id="engine_experience",
                value_field="engine_family",
                valid_values=canonical_engine_family_values(),
            )
            errors.extend(item_errors)
            return normalized_payload, errors
        engine_family = payload.get("engine_family")
        minimum_months = payload.get("minimum_months")
        recent_contract_count = payload.get("recent_contract_count")
        if not isinstance(engine_family, str) or engine_family not in canonical_engine_family_values():
            errors.append(_error("invalid_engine_family", "constraint.engine_family", "engine_family must be canonical"))
        if not (_is_int(minimum_months) or minimum_months is None):
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be an integer or null"))
        if not (_is_int(recent_contract_count) or recent_contract_count is None):
            errors.append(_error("invalid_recent_contract_count", "constraint.recent_contract_count", "recent_contract_count must be an integer or null"))
        return {
            "type": "engine_experience",
            "engine_family": engine_family,
            "minimum_months": minimum_months,
            "recent_contract_count": recent_contract_count,
        }, errors

    if family_id == "engine_vessel_experience":
        if payload.get("type") != "engine_vessel_experience":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be engine_vessel_experience"))
        engine_family = payload.get("engine_family")
        ship_family = payload.get("ship_family")
        minimum_months = payload.get("minimum_months")
        recent_contract_count = payload.get("recent_contract_count")
        if not isinstance(engine_family, str) or engine_family not in canonical_engine_family_values():
            errors.append(_error("invalid_engine_family", "constraint.engine_family", "engine_family must be canonical"))
        if ship_family is not None and (not isinstance(ship_family, str) or ship_family not in canonical_ship_family_values()):
            errors.append(_error("invalid_ship_family", "constraint.ship_family", "ship_family must be canonical or null"))
        if not (_is_int(minimum_months) or minimum_months is None):
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be an integer or null"))
        if not (_is_int(recent_contract_count) or recent_contract_count is None):
            errors.append(_error("invalid_recent_contract_count", "constraint.recent_contract_count", "recent_contract_count must be an integer or null"))
        return {
            "type": "engine_vessel_experience",
            "engine_family": engine_family,
            "ship_family": ship_family,
            "minimum_months": minimum_months,
            "recent_contract_count": recent_contract_count,
        }, errors

    if family_id == "company_continuity":
        if payload.get("type") != "company_continuity":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be company_continuity"))
        minimum_contracts = payload.get("minimum_contracts")
        same_company_required = payload.get("same_company_required")
        if not _is_int(minimum_contracts):
            errors.append(_error("invalid_minimum_contracts", "constraint.minimum_contracts", "minimum_contracts must be an integer"))
        elif minimum_contracts <= 0:
            errors.append(_error("invalid_minimum_contracts", "constraint.minimum_contracts", "minimum_contracts must be positive"))
        if same_company_required is not True:
            errors.append(_error("invalid_same_company_required", "constraint.same_company_required", "same_company_required must be true"))
        return {
            "type": "company_continuity",
            "minimum_contracts": minimum_contracts,
            "same_company_required": True,
        }, errors

    if family_id == "recency":
        if payload.get("type") != "recency":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be recency"))
        max_months = payload.get("maximum_months_since_last_contract")
        must_be_current = payload.get("must_be_currently_sailing")
        if not (_is_int(max_months) or max_months is None):
            errors.append(_error("invalid_maximum_months", "constraint.maximum_months_since_last_contract", "maximum_months_since_last_contract must be an integer or null"))
        if not (_is_bool(must_be_current) or must_be_current is None):
            errors.append(_error("invalid_current_flag", "constraint.must_be_currently_sailing", "must_be_currently_sailing must be a boolean or null"))
        return {
            "type": "recency",
            "maximum_months_since_last_contract": max_months,
            "must_be_currently_sailing": must_be_current,
        }, errors

    if family_id == "rank_duration_experience":
        if payload.get("type") != "rank_duration_experience":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be rank_duration_experience"))
        rank = payload.get("rank")
        minimum_months = payload.get("minimum_months")
        if rank is not None and (not isinstance(rank, str) or rank not in canonical_rank_values()):
            errors.append(_error("invalid_rank", "constraint.rank", "rank must be canonical or null"))
        if not _is_int(minimum_months):
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be an integer"))
        elif minimum_months <= 0:
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be positive"))
        return {"type": "rank_duration_experience", "rank": rank, "minimum_months": minimum_months}, errors

    if family_id == "stcw_endorsement":
        if payload.get("type") != "stcw_endorsement":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be stcw_endorsement"))
        endorsements = payload.get("endorsements_required")
        if not _is_list_of_strings(endorsements):
            errors.append(_error("invalid_endorsements", "constraint.endorsements_required", "endorsements_required must be a list of strings"))
            endorsements = []
        invalid = [item for item in endorsements if item not in canonical_endorsement_values()]
        if invalid:
            errors.append(_error("invalid_endorsements", "constraint.endorsements_required", f"Unknown endorsement ids: {', '.join(invalid)}"))
        return {"type": "stcw_endorsement", "endorsements_required": _canonicalize_list(endorsements)}, errors

    if family_id == "rank_certificate_expectation":
        if payload.get("type") != "rank_certificate_expectation":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be rank_certificate_expectation"))
        rank = payload.get("rank")
        if rank is not None and (not isinstance(rank, str) or rank not in canonical_rank_values()):
            errors.append(_error("invalid_rank", "constraint.rank", "rank must be canonical or null"))
        certificates = payload.get("certificates_required")
        endorsements = payload.get("endorsements_required")
        if not _is_list_of_strings(certificates):
            errors.append(_error("invalid_certificates", "constraint.certificates_required", "certificates_required must be a list of strings"))
            certificates = []
        if not _is_list_of_strings(endorsements):
            errors.append(_error("invalid_endorsements", "constraint.endorsements_required", "endorsements_required must be a list of strings"))
            endorsements = []
        invalid_certs = [item for item in certificates if item not in canonical_certificate_values()]
        invalid_endorsements = [item for item in endorsements if item not in canonical_endorsement_values()]
        if invalid_certs:
            errors.append(_error("invalid_certificates", "constraint.certificates_required", f"Unknown certificate ids: {', '.join(invalid_certs)}"))
        if invalid_endorsements:
            errors.append(_error("invalid_endorsements", "constraint.endorsements_required", f"Unknown endorsement ids: {', '.join(invalid_endorsements)}"))
        return {
            "type": "rank_certificate_expectation",
            "rank": rank,
            "certificates_required": _canonicalize_list(certificates),
            "endorsements_required": _canonicalize_list(endorsements),
        }, errors

    if family_id == "certificate_requirement":
        if payload.get("type") != "certificate_requirement":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be certificate_requirement"))
        certificates = payload.get("certificates_required")
        if not _is_list_of_strings(certificates):
            errors.append(_error("invalid_certificates", "constraint.certificates_required", "certificates_required must be a list of strings"))
            certificates = []
        invalid_certs = [item for item in certificates if item not in canonical_certificate_values()]
        if invalid_certs:
            errors.append(_error("invalid_certificates", "constraint.certificates_required", f"Unknown certificate ids: {', '.join(invalid_certs)}"))
        return {"type": "certificate_requirement", "certificates_required": _canonicalize_list(certificates)}, errors

    if family_id == "experience_ship_type":
        if payload.get("type") != "experience_ship_type":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be experience_ship_type"))
        if "items" in payload:
            normalized_payload, item_errors = _validate_experience_filter_payload(
                payload,
                family_id="experience_ship_type",
                value_field="ship_family",
                valid_values=canonical_ship_family_values(),
            )
            errors.extend(item_errors)
            return normalized_payload, errors
        ship_family = payload.get("ship_family")
        minimum_months = payload.get("minimum_months")
        if not isinstance(ship_family, str) or ship_family not in canonical_ship_family_values():
            errors.append(_error("invalid_ship_family", "constraint.ship_family", "ship_family must be canonical"))
        if not (_is_int(minimum_months) or minimum_months is None):
            errors.append(_error("invalid_minimum_months", "constraint.minimum_months", "minimum_months must be an integer or null"))
        return {"type": "experience_ship_type", "ship_family": ship_family, "minimum_months": minimum_months}, errors

    if family_id == "vessel_tonnage":
        if payload.get("type") != "vessel_tonnage":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be vessel_tonnage"))
        min_value = payload.get("min_value")
        max_value = payload.get("max_value")
        unit = payload.get("unit")
        years_back = payload.get("years_back")
        contract_count = payload.get("contract_count")
        if not (_is_int(min_value) or min_value is None):
            errors.append(_error("invalid_min_value", "constraint.min_value", "min_value must be an integer or null"))
        elif isinstance(min_value, int) and min_value <= 0:
            errors.append(_error("invalid_min_value", "constraint.min_value", "min_value must be positive"))
        if not (_is_int(max_value) or max_value is None):
            errors.append(_error("invalid_max_value", "constraint.max_value", "max_value must be an integer or null"))
        elif isinstance(max_value, int) and max_value <= 0:
            errors.append(_error("invalid_max_value", "constraint.max_value", "max_value must be positive"))
        if min_value is None and max_value is None:
            errors.append(_error("missing_vessel_tonnage_bound", "constraint", "vessel_tonnage must include at least one bound"))
        if _is_int(min_value) and _is_int(max_value) and min_value > max_value:
            errors.append(_error("invalid_vessel_tonnage_range", "constraint", "min_value cannot exceed max_value"))
        if unit not in {"any", "unspecified", "gt_grt", "dwt"}:
            errors.append(_error("invalid_vessel_tonnage_unit", "constraint.unit", "unit must be any, unspecified, gt_grt, or dwt"))
        errors.extend(_validate_positive_int_or_null(years_back, path="constraint.years_back", field_name="years_back"))
        if contract_count is not None:
            errors.append(_error("invalid_vessel_tonnage_contract_count", "constraint.contract_count", "contract_count is not supported for vessel_tonnage in v1"))
        normalized = {
            "type": "vessel_tonnage",
            "min_value": min_value,
            "max_value": max_value,
            "unit": unit if unit in {"any", "unspecified", "gt_grt", "dwt"} else "any",
        }
        if "years_back" in payload:
            normalized["years_back"] = years_back
        return normalized, errors

    if family_id == "availability":
        if payload.get("type") != "availability":
            errors.append(_error("invalid_payload_type", "constraint.type", "type must be availability"))
        status = payload.get("status")
        available_by = payload.get("available_by")
        if status not in {"available", "available_by_date"}:
            errors.append(_error("invalid_status", "constraint.status", "status must be available or available_by_date"))
        if available_by is not None and not isinstance(available_by, str):
            errors.append(_error("invalid_available_by", "constraint.available_by", "available_by must be a string or null"))
        return {"type": "availability", "status": status, "available_by": available_by}, errors

    errors.append(_error("unsupported_constraint_family", "constraint", f"Unsupported family {family_id}"))
    return None, errors


def _normalize_applied_constraint_item(item: Mapping[str, Any], mode: str) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None, List[Dict[str, str]], List[Dict[str, str]]]:
    """Return (applied_item, unapplied_item, errors, warnings)."""

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    allowed_keys = {"id", "mode", "constraint", "source_text", "confidence", "compatibility"}
    unknown = sorted(set(item.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_constraint_keys", "applied_constraint", f"Unknown keys: {', '.join(unknown)}"))
        if mode == "production":
            return None, None, errors, warnings
    family_id = item.get("id")
    if not isinstance(family_id, str) or not family_id:
        errors.append(_error("invalid_constraint_id", "applied_constraint.id", "id must be a non-empty string"))
        return None, None, errors, warnings

    if item.get("mode") not in VALID_CONSTRAINT_MODES:
        errors.append(_error("invalid_constraint_mode", "applied_constraint.mode", "mode must be required or preferred"))
    if not isinstance(item.get("source_text"), str):
        errors.append(_error("invalid_source_text", "applied_constraint.source_text", "source_text must be a string"))
    if item.get("confidence") not in VALID_CONFIDENCE_VALUES:
        errors.append(_error("invalid_confidence", "applied_constraint.confidence", "confidence must be high, medium, or low"))

    compatibility = item.get("compatibility")
    if not isinstance(compatibility, Mapping):
        errors.append(_error("invalid_compatibility", "applied_constraint.compatibility", "compatibility must be an object"))
    else:
        comp_keys = {"legacy_hard_constraints_key", "legacy_applied_constraint_id"}
        unknown_comp = sorted(set(compatibility.keys()) - comp_keys)
        if unknown_comp:
            errors.append(_error("unknown_compatibility_keys", "applied_constraint.compatibility", f"Unknown keys: {', '.join(unknown_comp)}"))
        for field_name in comp_keys:
            if not _is_string_or_none(compatibility.get(field_name)):
                errors.append(_error("invalid_compatibility_field", f"applied_constraint.compatibility.{field_name}", f"{field_name} must be a string or null"))

    if is_unsupported_family(family_id):
        reason = "unsupported_filter_family"
        suggested_handling = "block_search" if item.get("mode") == "required" else "semantic_with_warning"
        unapplied_item = {
            "id": family_id,
            "mode": item.get("mode"),
            "reason": reason,
            "source_text": item.get("source_text", ""),
            "suggested_handling": suggested_handling,
            "confidence": item.get("confidence", "low"),
        }
        warnings.append(_warning("unsupported_family_demoted", f"Family {family_id} is unsupported and was demoted to unapplied_constraints"))
        return None, unapplied_item, errors, warnings

    if not is_active_family(family_id):
        reason = "insufficient_schema"
        suggested_handling = "block_search" if item.get("mode") == "required" else "semantic_with_warning"
        unapplied_item = {
            "id": family_id,
            "mode": item.get("mode"),
            "reason": reason,
            "source_text": item.get("source_text", ""),
            "suggested_handling": suggested_handling,
            "confidence": item.get("confidence", "low"),
        }
        warnings.append(_warning("unknown_family_demoted", f"Family {family_id} is not in the active catalogue and was demoted to unapplied_constraints"))
        return None, unapplied_item, errors, warnings

    normalized_payload, payload_errors = _validate_payload_family(family_id, item.get("constraint"))
    if isinstance(normalized_payload, MutableMapping):
        payload_warnings = normalized_payload.pop("_validation_warnings", [])
        if isinstance(payload_warnings, list):
            warnings.extend(warning for warning in payload_warnings if isinstance(warning, Mapping))
    errors.extend(payload_errors)
    if payload_errors:
        reason = "validation_failed"
        suggested_handling = "block_search" if item.get("mode") == "required" else "semantic_with_warning"
        unapplied_item = {
            "id": family_id,
            "mode": item.get("mode"),
            "reason": reason,
            "source_text": item.get("source_text", ""),
            "suggested_handling": suggested_handling,
            "confidence": item.get("confidence", "low"),
        }
        warnings.append(_warning("constraint_payload_demoted", f"Family {family_id} payload failed validation and was demoted to unapplied_constraints"))
        return None, unapplied_item, errors, warnings

    applied_item = {
        "id": family_id,
        "mode": item.get("mode"),
        "constraint": normalized_payload,
        "source_text": item.get("source_text", ""),
        "confidence": item.get("confidence", "low"),
        "compatibility": {
            "legacy_hard_constraints_key": compatibility.get("legacy_hard_constraints_key") if isinstance(compatibility, Mapping) else None,
            "legacy_applied_constraint_id": compatibility.get("legacy_applied_constraint_id") if isinstance(compatibility, Mapping) else None,
        },
    }
    return applied_item, None, errors, warnings


def _normalize_logical_group_item(item: Mapping[str, Any], mode: str) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    allowed_keys = {"id", "type", "mode", "source_text", "confidence", "children"}
    unknown = sorted(set(item.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_logical_group_keys", "logical_group", f"Unknown keys: {', '.join(unknown)}"))
        if mode == "production":
            return None, errors, warnings
    if not isinstance(item.get("id"), str) or not item.get("id"):
        errors.append(_error("invalid_logical_group_id", "logical_group.id", "id must be a non-empty string"))
    if item.get("type") != "any_of":
        errors.append(_error("invalid_logical_group_type", "logical_group.type", "type must be any_of"))
    if item.get("mode") not in VALID_CONSTRAINT_MODES:
        errors.append(_error("invalid_logical_group_mode", "logical_group.mode", "mode must be required or preferred"))
    if not isinstance(item.get("source_text"), str):
        errors.append(_error("invalid_logical_group_source_text", "logical_group.source_text", "source_text must be a string"))
    if item.get("confidence") not in VALID_CONFIDENCE_VALUES:
        errors.append(_error("invalid_logical_group_confidence", "logical_group.confidence", "confidence must be high, medium, or low"))
    children = item.get("children")
    if not isinstance(children, list) or len(children) < 2:
        errors.append(_error("invalid_logical_group_children", "logical_group.children", "children must contain at least two constraints"))
        children = []

    normalized_children: List[Dict[str, Any]] = []
    for index, child in enumerate(children):
        if not isinstance(child, Mapping):
            errors.append(_error("invalid_logical_group_child", f"logical_group.children[{index}]", "child must be an object"))
            continue
        applied_item, demoted_item, child_errors, child_warnings = _normalize_applied_constraint_item(child, mode)
        errors.extend(child_errors)
        warnings.extend(child_warnings)
        if demoted_item is not None:
            errors.append(_error("logical_group_child_demoted", f"logical_group.children[{index}]", "logical-group children must be active applied constraints"))
        if applied_item is not None:
            normalized_children.append(applied_item)

    if len(normalized_children) < 2:
        errors.append(_error("invalid_logical_group_children", "logical_group.children", "logical group must have at least two valid children"))
    if errors:
        return None, errors, warnings
    return {
        "id": item.get("id"),
        "type": "any_of",
        "mode": item.get("mode"),
        "source_text": item.get("source_text", ""),
        "confidence": item.get("confidence", "low"),
        "children": normalized_children,
    }, errors, warnings


def _validate_unapplied_constraint_item(item: Mapping[str, Any], mode: str) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    allowed_keys = {"id", "mode", "reason", "source_text", "suggested_handling", "confidence"}
    unknown = sorted(set(item.keys()) - allowed_keys)
    if unknown:
        errors.append(_error("unknown_unapplied_constraint_keys", "unapplied_constraint", f"Unknown keys: {', '.join(unknown)}"))
        if mode == "production":
            return None, errors
    if not isinstance(item.get("id"), str) or not item.get("id"):
        errors.append(_error("invalid_unapplied_id", "unapplied_constraint.id", "id must be a non-empty string"))
    if item.get("mode") not in VALID_CONSTRAINT_MODES:
        errors.append(_error("invalid_unapplied_mode", "unapplied_constraint.mode", "mode must be required or preferred"))
    if item.get("reason") not in VALID_UNAPPLIED_REASONS:
        errors.append(_error("invalid_unapplied_reason", "unapplied_constraint.reason", "Unsupported reason"))
    if not isinstance(item.get("source_text"), str):
        errors.append(_error("invalid_unapplied_source_text", "unapplied_constraint.source_text", "source_text must be a string"))
    if item.get("suggested_handling") not in VALID_SUGGESTED_HANDLING:
        errors.append(_error("invalid_suggested_handling", "unapplied_constraint.suggested_handling", "Invalid suggested_handling"))
    if item.get("confidence") not in VALID_CONFIDENCE_VALUES:
        errors.append(_error("invalid_unapplied_confidence", "unapplied_constraint.confidence", "confidence must be high, medium, or low"))
    if errors:
        return None, errors
    return dict(item), errors


def _validate_residual_item(item: Mapping[str, Any]) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(item.get("text"), str):
        errors.append(_error("invalid_residual_text", "unrecognized_residual.text", "text must be a string"))
    if item.get("suggested_handling") not in {"semantic", "block_search", "ignore_with_warning"}:
        errors.append(_error("invalid_residual_handling", "unrecognized_residual.suggested_handling", "Invalid suggested_handling"))
    if errors:
        return None, errors
    return dict(item), errors


def _validate_warning_item(item: Mapping[str, Any]) -> Tuple[Dict[str, Any] | None, List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(item.get("code"), str) or not item.get("code"):
        errors.append(_error("invalid_warning_code", "warnings[].code", "code must be a non-empty string"))
    if not isinstance(item.get("message"), str) or not item.get("message"):
        errors.append(_error("invalid_warning_message", "warnings[].message", "message must be a non-empty string"))
    if item.get("severity") not in VALID_WARNING_SEVERITIES:
        errors.append(_error("invalid_warning_severity", "warnings[].severity", "severity must be info, warning, or error"))
    if errors:
        return None, errors
    return dict(item), errors


def normalize_query_plan_v1(plan: Mapping[str, Any], *, mode: str = "production") -> Dict[str, Any]:
    """Normalize a query plan into a validated query_plan.v1 envelope."""

    normalized = _copy_plan(plan)
    validation_errors: List[Dict[str, str]] = []
    validation_warnings: List[Dict[str, str]] = []

    if not isinstance(plan, Mapping):
        return {
            "schema_version": QUERY_PLAN_SCHEMA_VERSION,
            "normalizer": {"name": "legacy", "model": None, "prompt_template_version": "invalid", "catalog_version": CATALOG_VERSION, "created_at": _utc_now_iso()},
            "input": {"raw_prompt": "", "rank_context": None, "ui_filters": {"schema_version": "ui_filters.v1", "filters": []}},
            "applied_constraints": [],
            "logical_groups": [],
            "unapplied_constraints": [],
            "semantic_query": "",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": _default_validation("invalid", [_error("invalid_plan_type", "root", "plan must be an object")]),
        }

    top_level_errors, top_level_warnings = _validate_top_level(plan, mode)
    validation_errors.extend(top_level_errors)
    validation_warnings.extend(top_level_warnings)

    normalized_plan: Dict[str, Any] = {
        "schema_version": plan.get("schema_version", QUERY_PLAN_SCHEMA_VERSION),
        "normalizer": {},
        "input": {},
        "applied_constraints": [],
        "logical_groups": [],
        "unapplied_constraints": [],
        "semantic_query": "" if plan.get("semantic_query") is None else plan.get("semantic_query", ""),
        "unrecognized_residual": [],
        "warnings": [],
        "validation": _default_validation("invalid", []),
    }

    normalizer, normalizer_errors = _validate_normalizer(plan.get("normalizer"))
    if normalizer is not None:
        normalized_plan["normalizer"] = normalizer
    validation_errors.extend(normalizer_errors)

    input_obj = plan.get("input")
    if not isinstance(input_obj, Mapping):
        validation_errors.append(_error("invalid_input", "input", "input must be an object"))
        input_obj = {}
    else:
        allowed_input_keys = {"raw_prompt", "rank_context", "ui_filters"}
        unknown_input = sorted(set(input_obj.keys()) - allowed_input_keys)
        if unknown_input:
            message = f"Unknown input keys: {', '.join(unknown_input)}"
            if mode == "production":
                validation_errors.append(_error("unknown_input_keys", "input", message))
            else:
                validation_warnings.append(_warning("unknown_input_keys", message))

    normalized_plan["input"]["raw_prompt"] = input_obj.get("raw_prompt", "") if isinstance(input_obj.get("raw_prompt", ""), str) else ""
    if not isinstance(input_obj.get("raw_prompt", ""), str):
        validation_errors.append(_error("invalid_raw_prompt", "input.raw_prompt", "raw_prompt must be a string"))

    rank_context = input_obj.get("rank_context", None)
    if not _is_string_or_none(rank_context):
        validation_errors.append(_error("invalid_rank_context", "input.rank_context", "rank_context must be a string or null"))
        rank_context = None
    normalized_plan["input"]["rank_context"] = rank_context

    ui_filters, ui_errors = _validate_ui_filters(input_obj.get("ui_filters"), mode)
    if ui_filters is not None:
        normalized_plan["input"]["ui_filters"] = ui_filters
    validation_errors.extend(ui_errors)

    applied_constraints = plan.get("applied_constraints")
    logical_groups = plan.get("logical_groups", [])
    unapplied_constraints = plan.get("unapplied_constraints")
    residuals = plan.get("unrecognized_residual")
    warnings = plan.get("warnings")

    if not isinstance(applied_constraints, list):
        validation_errors.append(_error("invalid_applied_constraints", "applied_constraints", "applied_constraints must be a list"))
        applied_constraints = []
    if not isinstance(logical_groups, list):
        validation_errors.append(_error("invalid_logical_groups", "logical_groups", "logical_groups must be a list"))
        logical_groups = []
    if not isinstance(unapplied_constraints, list):
        validation_errors.append(_error("invalid_unapplied_constraints", "unapplied_constraints", "unapplied_constraints must be a list"))
        unapplied_constraints = []
    if not isinstance(residuals, list):
        validation_errors.append(_error("invalid_unrecognized_residual", "unrecognized_residual", "unrecognized_residual must be a list"))
        residuals = []
    if not isinstance(warnings, list):
        validation_errors.append(_error("invalid_warnings", "warnings", "warnings must be a list"))
        warnings = []

    for index, item in enumerate(applied_constraints):
        if not isinstance(item, Mapping):
            validation_errors.append(_error("invalid_applied_constraint_item", f"applied_constraints[{index}]", "applied constraint must be an object"))
            continue
        applied_item, demoted_item, item_errors, item_warnings = _normalize_applied_constraint_item(item, mode)
        validation_errors.extend(item_errors)
        validation_warnings.extend(item_warnings)
        if applied_item is not None:
            normalized_plan["applied_constraints"].append(applied_item)
        if demoted_item is not None:
            normalized_plan["unapplied_constraints"].append(demoted_item)

    for index, item in enumerate(logical_groups):
        if not isinstance(item, Mapping):
            validation_errors.append(_error("invalid_logical_group_item", f"logical_groups[{index}]", "logical group must be an object"))
            continue
        normalized_item, item_errors, item_warnings = _normalize_logical_group_item(item, mode)
        validation_errors.extend(item_errors)
        validation_warnings.extend(item_warnings)
        if normalized_item is not None:
            normalized_plan["logical_groups"].append(normalized_item)

    for index, item in enumerate(unapplied_constraints):
        if not isinstance(item, Mapping):
            validation_errors.append(_error("invalid_unapplied_constraint_item", f"unapplied_constraints[{index}]", "unapplied constraint must be an object"))
            continue
        normalized_item, item_errors = _validate_unapplied_constraint_item(item, mode)
        validation_errors.extend(item_errors)
        if normalized_item is not None:
            normalized_plan["unapplied_constraints"].append(normalized_item)

    for index, item in enumerate(residuals):
        if not isinstance(item, Mapping):
            validation_errors.append(_error("invalid_residual_item", f"unrecognized_residual[{index}]", "residual item must be an object"))
            continue
        normalized_item, item_errors = _validate_residual_item(item)
        validation_errors.extend(item_errors)
        if normalized_item is not None:
            normalized_plan["unrecognized_residual"].append(normalized_item)

    for index, item in enumerate(warnings):
        if not isinstance(item, Mapping):
            validation_errors.append(_error("invalid_warning_item", f"warnings[{index}]", "warning item must be an object"))
            continue
        normalized_item, item_errors = _validate_warning_item(item)
        validation_errors.extend(item_errors)
        if normalized_item is not None:
            normalized_plan["warnings"].append(normalized_item)

    raw_prompt = normalized_plan["input"].get("raw_prompt", "")
    semantic_query = normalized_plan.get("semantic_query", "")
    if not _is_string_or_none(semantic_query):
        validation_errors.append(_error("invalid_semantic_query", "semantic_query", "semantic_query must be a string"))
        semantic_query = ""
    if _contains_mandatory_marker(semantic_query):
        validation_errors.append(_error("mandatory_marker_in_semantic_query", "semantic_query", "mandatory fragments must not remain in semantic_query"))
    normalized_plan["semantic_query"] = semantic_query

    if normalized_plan["schema_version"] != QUERY_PLAN_SCHEMA_VERSION:
        validation_errors.append(_error("invalid_schema_version", "schema_version", f"schema_version must be {QUERY_PLAN_SCHEMA_VERSION}"))

    fatal_errors = [item for item in validation_errors if item.get("code") in FATAL_ERROR_CODES]

    if fatal_errors:
        validation_status = "invalid"
    elif validation_errors or validation_warnings or normalized_plan["unapplied_constraints"] or normalized_plan["unrecognized_residual"]:
        validation_status = "degraded"
    else:
        validation_status = "valid"

    normalized_plan["validation"] = {
        "status": validation_status,
        "errors": validation_errors,
    }

    if validation_warnings:
        normalized_plan["warnings"].extend(validation_warnings)

    return normalized_plan


def validate_query_plan_v1(plan: Mapping[str, Any], *, mode: str = "production") -> Dict[str, Any]:
    """Return a validated query-plan envelope.

    The returned object always includes a fresh `validation` block.
    """

    return normalize_query_plan_v1(plan, mode=mode)
