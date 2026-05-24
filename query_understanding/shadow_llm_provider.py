"""Shadow-only Gemini provider for `query_plan.v1` generation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from .hard_filter_catalog import (
    CATALOG_VERSION,
    SUPPORTED_FAMILY_IDS,
    canonical_certificate_values,
    canonical_endorsement_values,
    canonical_engine_family_values,
    canonical_rank_values,
    canonical_ship_family_values,
    is_active_family,
    is_unsupported_family,
    legacy_applied_constraint_id,
    legacy_hard_constraint_key,
)
from .llm_normalizer import is_enabled
from .schema import normalize_query_plan_v1

SHADOW_LLM_PROMPT_TEMPLATE_VERSION = "query_understanding.shadow_llm.v1"
SHADOW_LLM_DEFAULT_MODEL = "gemini-2.0-flash-lite"
SHADOW_LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SHADOW_LLM_RESPONSE_SEED = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def _extract_json_payload(text: Any) -> dict[str, Any] | None:
    raw_text = _normalize_text(text)
    if not raw_text:
        return None

    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        raw_text = raw_text.strip()

    if not raw_text:
        return None

    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    candidate_text = match.group(0) if match else raw_text

    try:
        parsed = json.loads(candidate_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_shadow_llm_prompt(
    prompt: str,
    *,
    rank: str | None = None,
    catalog_version: str = CATALOG_VERSION,
) -> str:
    supported_families = ", ".join(sorted(SUPPORTED_FAMILY_IDS))
    return (
        "You are NjordHR's shadow query normalizer.\n"
        "Return only valid JSON for a single query_plan.v1 object. No markdown, no commentary.\n"
        "Preserve mandatory recruiter requirements as hard filters when supported.\n"
        "Put unsupported mandatory requirements into unapplied_constraints with reason "
        '"unsupported_filter_family" instead of smuggling them into semantic_query.\n'
        "Remove supported hard constraints from semantic_query. Keep only fuzzy suitability language there.\n"
        "If the prompt is ambiguous, prefer degraded over invalid unless the schema truly cannot be satisfied.\n"
        f"The catalog_version is {catalog_version}.\n"
        f"Supported families: {supported_families}.\n"
        f"Required schema_version: query_plan.v1.\n"
        "Output shape:\n"
        "{\n"
        '  "schema_version": "query_plan.v1",\n'
        '  "normalizer": {\n'
        '    "name": "llm",\n'
        '    "model": "string",\n'
        '    "prompt_template_version": "query_understanding.shadow_llm.v1",\n'
        '    "catalog_version": "string",\n'
        '    "created_at": "ISO-8601 timestamp"\n'
        "  },\n"
        '  "input": {\n'
        f'    "raw_prompt": {json.dumps(prompt)},\n'
        f'    "rank_context": {json.dumps(rank)},\n'
        '    "ui_filters": {"schema_version": "ui_filters.v1", "filters": []}\n'
        "  },\n"
        '  "applied_constraints": [],\n'
        '  "unapplied_constraints": [],\n'
        '  "semantic_query": "string",\n'
        '  "unrecognized_residual": [],\n'
        '  "warnings": [],\n'
        '  "validation": {"status": "valid", "errors": []}\n'
        "}\n"
        "Prompt:\n"
        f"{json.dumps({'raw_prompt': prompt, 'rank_context': rank, 'catalog_version': catalog_version}, ensure_ascii=False)}\n"
    )


def _config_value(config: Any, attr: str, fallback: Any = None) -> Any:
    if config is None:
        return fallback
    value = getattr(config, attr, fallback)
    return fallback if value is None else value


def _normalize_rank_value(analyzer: Any, raw_rank: Any) -> str | None:
    rank_text = str(raw_rank or "").strip()
    if not rank_text:
        return None
    normalize_rank = getattr(analyzer, "_normalize_rank", None)
    if callable(normalize_rank):
        try:
            normalized = normalize_rank(rank_text)
            if isinstance(normalized, (tuple, list)) and normalized:
                canonical_id = normalized[0]
                if isinstance(canonical_id, str) and canonical_id.strip():
                    return canonical_id.strip()
        except Exception:
            pass
    fallback = rank_text.lower().replace(" ", "_")
    return fallback if fallback else None


def _make_applied_constraint(
    family: str,
    constraint: Mapping[str, Any],
    *,
    source_text: str,
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "id": family,
        "mode": "required",
        "constraint": dict(constraint),
        "source_text": source_text,
        "confidence": confidence,
        "compatibility": {
            "legacy_hard_constraints_key": legacy_hard_constraint_key(family),
            "legacy_applied_constraint_id": legacy_applied_constraint_id(family),
        },
    }


def _canonicalize_family_name(value: Any) -> str:
    family = str(value or "").strip()
    return family


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _is_false_value(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() in {"false", "0", "no", "none"})


class ShadowLLMTranslationError(ValueError):
    """Raised when Gemini returns a shape that should be rejected in shadow mode."""


def _canonical_unapplied_family_id(family: str) -> str:
    if family == "sea_service":
        return "min_sea_service"
    return family


def _extract_parameters(item: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("parameters", "constraint", "payload", "value"):
        value = item.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return parsed if parsed > 0 else None


def _age_bounds_from_text(text: Any) -> tuple[int | None, int | None]:
    prompt = str(text or "").strip().lower()
    if not prompt:
        return None, None

    range_patterns = [
        r"between\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})\s+years?\s+old",
        r"between\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})",
        r"within\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})",
        r"within\s+the\s+age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})",
        r"age\s+range\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})",
        r"age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})\s+years?\s+old",
        r"ages?\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})",
        r"aged?\s+(\d{1,2})\s*(?:-|to|and)\s*(\d{1,2})",
    ]
    for pattern in range_patterns:
        match = re.search(pattern, prompt)
        if match:
            lower = int(match.group(1))
            upper = int(match.group(2))
            if lower > upper:
                lower, upper = upper, lower
            return lower, upper

    min_patterns = [
        r"at\s+least\s+(\d{1,2})\s+years?\s+old",
        r"older\s+than\s+(\d{1,2})",
        r"over\s+the\s+age\s+of\s+(\d{1,2})(?:\s+years?)?",
        r"over\s+(\d{1,2})",
        r"above\s+the\s+age\s+of\s+(\d{1,2})",
        r"above\s+(\d{1,2})",
        r"minimum\s+age\s+(?:of\s+)?(\d{1,2})",
        r"minimum\s+age\s+should\s+be\s+(\d{1,2})",
    ]
    for pattern in min_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = int(match.group(1))
            matched_phrase = match.group(0).lower()
            if any(term in matched_phrase for term in ("older than", "over", "above")):
                value += 1
            return value, None

    max_patterns = [
        r"up\s+to\s+(\d{1,2})\s+years?\s+old",
        r"younger\s+than\s+(\d{1,2})",
        r"below\s+the\s+age\s+of\s+(\d{1,2})",
        r"below\s+age\s+(\d{1,2})",
        r"less\s+than\s+(\d{1,2})\s+years?\s+old",
        r"not\s+more\s+than\s+(\d{1,2})\s+years?\s+old",
        r"under\s+(\d{1,2})",
        r"below\s+(\d{1,2})",
        r"maximum\s+age\s+(?:of\s+)?(\d{1,2})",
        r"maximum\s+age\s+should\s+be\s+(\d{1,2})",
    ]
    for pattern in max_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = int(match.group(1))
            matched_phrase = match.group(0).lower()
            if any(term in matched_phrase for term in ("younger than", "under", "below", "less than")):
                value -= 1
            return None, value

    return None, None


def _canonical_list(values: Any, allowed: set[str] | frozenset[str] | None = None) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    seen: set[str] = set()
    canonical: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if allowed is not None and text not in allowed:
            continue
        if text not in seen:
            seen.add(text)
            canonical.append(text)
    return canonical


def _strip_phrase(text: str, phrase: str) -> str:
    phrase = _normalize_text(phrase)
    if not phrase:
        return text
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    pattern = rf"(?<!\w){escaped}(?!\w)"
    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def _cleanup_semantic_residual(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned.strip(" ,.-")


def _normalized_certificate_source(text: Any) -> str:
    normalized = _normalize_text(str(text or "").lower())
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("-", " ")
    return " ".join(normalized.split())


def _has_certificate_specific_terms(text: Any) -> bool:
    normalized = _normalized_certificate_source(text)
    if not normalized:
        return False
    certificate_terms = {
        "certificate",
        "certificates",
        "coc",
        "competency",
        "endorsement",
        "endorsements",
        "yellow fever",
        "stcw",
    }
    return any(term in normalized for term in certificate_terms)


def _has_visa_specific_terms(text: Any) -> bool:
    normalized = _normalized_certificate_source(text)
    if not normalized:
        return False
    visa_terms = {
        "visa",
        "us visa",
        "valid visa",
        "c1 d",
        "c1d",
        "c1/d",
        "d visa",
    }
    return any(term in normalized for term in visa_terms)


def _certificate_requirement_consumed_by_repair(
    item: Mapping[str, Any],
    applied_constraints: list[dict[str, Any]],
) -> bool:
    detail_text = _first_string(item.get("details"), item.get("display_value"), item.get("value"))
    values_text = None
    values = item.get("values")
    if isinstance(values, list):
        values_text = _first_string(*values)
    elif values is not None:
        values_text = _first_string(values)
    source_text = _first_string(detail_text, values_text, item.get("source_text"))
    source = _normalized_certificate_source(source_text)
    if not source:
        return False

    if any(constraint.get("id") in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"} for constraint in applied_constraints):
        for constraint in applied_constraints:
            if constraint.get("id") not in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"}:
                continue
            constraint_source = _normalized_certificate_source(constraint.get("source_text") or (constraint.get("constraint") or {}).get("display_value"))
            if constraint_source and (constraint_source in source or source in constraint_source):
                return True
            constraint_payload = constraint.get("constraint") if isinstance(constraint.get("constraint"), Mapping) else {}
            known_fragments = []
            if isinstance(constraint_payload, Mapping):
                known_fragments.extend(
                    [
                        constraint_payload.get("type"),
                        constraint_payload.get("rank"),
                        constraint_payload.get("grade"),
                    ]
                )
                known_fragments.extend(constraint_payload.get("certificates_required") or [])
                known_fragments.extend(constraint_payload.get("endorsements_required") or [])
            for fragment in known_fragments:
                fragment_source = _normalized_certificate_source(fragment)
                if fragment_source and fragment_source in source:
                    return True

    if any(constraint.get("id") == "us_visa" for constraint in applied_constraints):
        source_text = _first_string(detail_text, values_text, item.get("source_text"))
        if source_text and not _has_certificate_specific_terms(source_text) and _has_visa_specific_terms(source_text):
            return True

    return False


def _family_to_canonical_items(
    family: str,
    item: Mapping[str, Any],
    *,
    analyzer: Any,
    raw_prompt: str,
    rank: str | None,
    canonical_rank: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (applied_items, unapplied_items, semantic_fragments) for a Gemini family item."""

    parameters = _extract_parameters(item)
    prompt_text = str(raw_prompt or "").strip()
    source_text = _first_string(item.get("source_text"), item.get("display_value"), parameters.get("display_value"), parameters.get("label"), raw_prompt) or raw_prompt
    confidence = _first_string(item.get("confidence"), parameters.get("confidence")) or "high"
    family = _canonicalize_family_name(family)

    if not family:
        return [], [], []

    if family in {"age_range"}:
        text_minimum_years, text_maximum_years = _age_bounds_from_text(
            _first_string(
                item.get("source_text"),
                item.get("details"),
                item.get("display_value"),
                parameters.get("display_value"),
                parameters.get("label"),
                raw_prompt,
            )
        )
        minimum_years = _as_positive_int(
            parameters.get("minimum_years")
            if parameters.get("minimum_years") is not None
            else parameters.get("minimum_age") if parameters.get("minimum_age") is not None else parameters.get("min_age")
            if parameters.get("minimum_age") is not None or parameters.get("min_age") is not None
            else item.get("minimum_years")
            if item.get("minimum_years") is not None
            else item.get("minimum_age")
            if item.get("minimum_age") is not None
            else item.get("min_age")
        )
        maximum_years = _as_positive_int(
            parameters.get("maximum_years")
            if parameters.get("maximum_years") is not None
            else parameters.get("maximum_age") if parameters.get("maximum_age") is not None else parameters.get("max_age")
            if parameters.get("maximum_age") is not None or parameters.get("max_age") is not None
            else item.get("maximum_years")
            if item.get("maximum_years") is not None
            else item.get("maximum_age")
            if item.get("maximum_age") is not None
            else item.get("max_age")
        )
        if text_minimum_years is not None:
            minimum_years = text_minimum_years
        if text_maximum_years is not None:
            maximum_years = text_maximum_years
        if minimum_years is None and maximum_years is None:
            return [], [], []
        fragments = []
        if minimum_years is not None:
            fragments.extend(
                [
                    f"older than {minimum_years}",
                    f"older than {minimum_years - 1}" if minimum_years > 0 else "",
                    f"over {minimum_years}",
                    f"over {minimum_years - 1}" if minimum_years > 0 else "",
                    f"above {minimum_years}",
                    f"above {minimum_years - 1}" if minimum_years > 0 else "",
                    f"more than {minimum_years}",
                    f"more than {minimum_years - 1}" if minimum_years > 0 else "",
                ]
            )
        if maximum_years is not None:
            fragments.extend(
                [
                    f"under {maximum_years}",
                    f"under {maximum_years + 1}",
                    f"younger than {maximum_years}",
                    f"younger than {maximum_years + 1}",
                    f"below {maximum_years}",
                    f"below {maximum_years + 1}",
                    f"less than {maximum_years}",
                    f"less than {maximum_years + 1}",
                ]
            )
        return [
            _make_applied_constraint(
                "age_range",
                {"type": "age_range", "minimum_years": minimum_years, "maximum_years": maximum_years},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], fragments

    if family == "rank_match":
        rank_value = _first_string(
            parameters.get("rank"),
            parameters.get("rank_normalized"),
            parameters.get("applied_rank_normalized"),
            canonical_rank,
        )
        if not rank_value:
            extract_rank = getattr(analyzer, "_extract_rank_constraint", None)
            if callable(extract_rank):
                try:
                    inferred_rank = extract_rank(prompt_text)
                except Exception:
                    inferred_rank = None
                if isinstance(inferred_rank, Mapping):
                    rank_value = _first_string(*(inferred_rank.get("applied_rank_normalized") or []), canonical_rank)
        if not rank_value:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_match",
                {"type": "rank_match", "rank": rank_value},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [rank_value.replace("_", " "), rank_value]

    if family == "coc_document_gate":
        required_value = _first_present(parameters.get("required"), parameters.get("must_have"), parameters.get("validity"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("coc_document_gate explicitly marked false")
        return [
            _make_applied_constraint(
                "coc_document_gate",
                {"type": "coc_document_gate", "required": True},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], ["valid coc", "coc required", "certificate of competency required", "certificate of competency"]

    if family == "coc_grade_match":
        grade = _first_string(parameters.get("grade"), parameters.get("coc_grade"), parameters.get("required_grade"))
        if not grade:
            extract_coc_grade = getattr(analyzer, "_extract_coc_grade_constraint", None)
            if callable(extract_coc_grade):
                try:
                    coc_grade = extract_coc_grade(prompt_text)
                except Exception:
                    coc_grade = None
                if isinstance(coc_grade, Mapping):
                    grades = coc_grade.get("required_grades") or []
                    grade = _first_string(*grades)
        if not grade:
            return [], [], []
        return [
            _make_applied_constraint(
                "coc_grade_match",
                {"type": "coc_grade_match", "grade": grade},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [grade.replace("_", " ")]

    if family == "stcw_basic":
        required_value = _first_present(parameters.get("required"), parameters.get("validity"), parameters.get("must_have"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("stcw_basic explicitly marked false")
        return [
            _make_applied_constraint(
                "stcw_basic",
                {"type": "stcw_basic", "required": True},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], ["stcw basic", "valid stcw basic", "stcw basic required", "basic stcw required", "valid stcw basic safety"]

    if family == "us_visa":
        required_value = _first_present(parameters.get("required"), parameters.get("validity"), parameters.get("must_have"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("us_visa explicitly marked false")
        months = _as_positive_int(
            parameters.get("minimum_months_remaining")
            if parameters.get("minimum_months_remaining") is not None
            else parameters.get("months_remaining")
            if parameters.get("months_remaining") is not None
            else parameters.get("minimum_months")
        )
        return [
            _make_applied_constraint(
                "us_visa",
                {"type": "us_visa", "required": True, "minimum_months_remaining": months},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], ["valid us visa", "us visa", "visa required", "valid visa"]

    if family == "passport_validity":
        validity_value = parameters.get("validity") or parameters.get("is_valid") or parameters.get("required")
        months = _as_positive_int(parameters.get("minimum_months_remaining") or parameters.get("months_remaining"))
        if validity_value in {"valid", True, "true", "True"} or months is not None:
            return [
                _make_applied_constraint(
                    "passport_validity",
                    {"type": "passport_validity", "must_be_valid": True, "minimum_months_remaining": months},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["valid passport", "passport required", "passport mandatory"]
        return [], [], []

    if family == "stcw_endorsement":
        endorsements = _canonical_list(
            parameters.get("endorsements_required")
            if parameters.get("endorsements_required") is not None
            else parameters.get("endorsements")
            if parameters.get("endorsements") is not None
            else parameters.get("endorsement")
            if parameters.get("endorsement") is not None
            else [],
            allowed=canonical_endorsement_values(),
        )
        certificates = _canonical_list(
            parameters.get("certificates_required")
            if parameters.get("certificates_required") is not None
            else parameters.get("certificates")
            if parameters.get("certificates") is not None
            else [],
            allowed=canonical_certificate_values(),
        )
        if not endorsements and not certificates:
            extract_endorsement = getattr(analyzer, "_extract_endorsement_constraint", None)
            if callable(extract_endorsement):
                try:
                    endorsement = extract_endorsement(prompt_text)
                except Exception:
                    endorsement = None
                if isinstance(endorsement, Mapping):
                    endorsements = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_endorsement_values())
                    certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values())
        if certificates:
            family_id = "rank_certificate_expectation" if _first_string(parameters.get("rank"), parameters.get("rank_normalized")) or rank else "certificate_requirement"
            payload = {"type": family_id, "certificates_required": certificates}
            if family_id == "rank_certificate_expectation":
                payload["rank"] = _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank)
                payload["endorsements_required"] = endorsements
            return [
                _make_applied_constraint(
                    family_id,
                    payload,
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [_humanized.replace("_", " ") for _humanized in certificates]
        if endorsements:
            return [
                _make_applied_constraint(
                    "stcw_endorsement",
                    {"type": "stcw_endorsement", "endorsements_required": endorsements},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [token.replace("_", " ") for token in endorsements] + [f"{token.replace('_', ' ')} endorsement" for token in endorsements]
        return [], [], []

    if family == "rank_certificate_expectation":
        certificates = _canonical_list(parameters.get("certificates_required") or parameters.get("certificates") or [], allowed=canonical_certificate_values())
        endorsements = _canonical_list(parameters.get("endorsements_required") or parameters.get("endorsements") or [], allowed=canonical_endorsement_values())
        if not certificates and not endorsements:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_certificate_expectation",
                {
                    "type": "rank_certificate_expectation",
                    "rank": _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank),
                    "certificates_required": certificates,
                    "endorsements_required": endorsements,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [token.replace("_", " ") for token in certificates + endorsements]

    if family == "certificate_requirement":
        certificates = _canonical_list(
            parameters.get("certificates_required")
            if parameters.get("certificates_required") is not None
            else parameters.get("certificates")
            if parameters.get("certificates") is not None
            else item.get("values")
            if item.get("values") is not None
            else item.get("value")
            if item.get("value") is not None
            else [],
            allowed=canonical_certificate_values(),
        )
        if not certificates:
            extract_endorsement = getattr(analyzer, "_extract_endorsement_constraint", None)
            if callable(extract_endorsement):
                try:
                    endorsement = extract_endorsement(prompt_text)
                except Exception:
                    endorsement = None
                if isinstance(endorsement, Mapping):
                    certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values())
        prompt_lower = str(raw_prompt or "").lower()
        source_lower = str(source_text or "").lower()
        if not certificates and ("certificate of competency" in prompt_lower or "coc" in prompt_lower or "certificate of competency" in source_lower or "coc" in source_lower):
            return [
                _make_applied_constraint(
                    "coc_document_gate",
                    {"type": "coc_document_gate", "required": True},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["valid coc", "coc required", "certificate of competency required", "certificate of competency"]
        if not certificates:
            return [], [], []
        return [
            _make_applied_constraint(
                "certificate_requirement",
                {"type": "certificate_requirement", "certificates_required": certificates},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [token.replace("_", " ") for token in certificates]

    if family == "recent_contract_vessel_experience":
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        if ship_family in canonical_ship_family_values():
            return [
                _make_applied_constraint(
                    "recent_contract_vessel_experience",
                    {
                        "type": "recent_contract_vessel_experience",
                        "ship_family": ship_family,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count or 1,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [ship_family]
        return [], [], []

    if family == "engine_experience":
        engine_family = _first_string(parameters.get("engine_family"), parameters.get("engine_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        if engine_family in canonical_engine_family_values():
            return [
                _make_applied_constraint(
                    "engine_experience",
                    {
                        "type": "engine_experience",
                        "engine_family": engine_family,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [engine_family.replace("_", " ")]
        return [], [], []

    if family == "engine_vessel_experience":
        engine_family = _first_string(parameters.get("engine_family"), parameters.get("engine_type"))
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        if engine_family in canonical_engine_family_values():
            return [
                _make_applied_constraint(
                    "engine_vessel_experience",
                    {
                        "type": "engine_vessel_experience",
                        "engine_family": engine_family,
                        "ship_family": ship_family if ship_family in canonical_ship_family_values() else None,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [engine_family.replace("_", " ")] + ([ship_family] if ship_family in canonical_ship_family_values() else [])
        return [], [], []

    if family == "company_continuity":
        minimum_contracts = _as_positive_int(parameters.get("minimum_contracts") or parameters.get("min_same_company_contract_count"))
        if not minimum_contracts:
            return [], [], []
        return [
            _make_applied_constraint(
                "company_continuity",
                {
                    "type": "company_continuity",
                    "minimum_contracts": minimum_contracts,
                    "same_company_required": True,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"{minimum_contracts} contracts", f"same company", f"same employer"]

    if family == "recency":
        maximum_months = _as_positive_int(parameters.get("maximum_months_since_last_contract") or parameters.get("max_months_since_sign_off"))
        must_be_currently_sailing = parameters.get("must_be_currently_sailing")
        if must_be_currently_sailing not in {True, False, None}:
            must_be_currently_sailing = None
        if maximum_months is None and must_be_currently_sailing is None:
            return [], [], []
        return [
            _make_applied_constraint(
                "recency",
                {
                    "type": "recency",
                    "maximum_months_since_last_contract": maximum_months,
                    "must_be_currently_sailing": must_be_currently_sailing,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"last {maximum_months} months" if maximum_months else "", "recent contract", "signed off"]

    if family == "rank_duration_experience":
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        if not minimum_months:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_duration_experience",
                {
                    "type": "rank_duration_experience",
                    "rank": _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank),
                    "minimum_months": minimum_months,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"{minimum_months} months", "experience as", "rank experience"]

    if family == "experience_ship_type":
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        if ship_family not in canonical_ship_family_values():
            return [], [], []
        return [
            _make_applied_constraint(
                "experience_ship_type",
                {
                    "type": "experience_ship_type",
                    "ship_family": ship_family,
                    "minimum_months": _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months")),
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [ship_family]

    if family == "availability":
        status = _first_string(parameters.get("status")) or "available"
        available_by = _first_string(parameters.get("available_by"), parameters.get("available_by_date"))
        if not available_by and callable(getattr(analyzer, "_extract_availability_constraint", None)):
            try:
                availability = analyzer._extract_availability_constraint(prompt_text)
            except Exception:
                availability = None
            if isinstance(availability, Mapping):
                value_type = availability.get("value_type")
                if value_type == "status":
                    status = "available"
                    available_by = None
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
                elif value_type == "date":
                    status = "available_by_date"
                    available_by = _first_string(availability.get("available_from_date")) or available_by
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
                elif value_type == "relative_phrase":
                    status = "available_by_date"
                    available_by = None
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
        return [
            _make_applied_constraint(
                "availability",
                {"type": "availability", "status": status, "available_by": available_by},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [status, available_by or ""]

    if family in {"vessel_type", "sea_service"} or is_unsupported_family(family):
        canonical_id = _canonical_unapplied_family_id(family)
        return [], [
            {
                "id": canonical_id,
                "mode": "required",
                "reason": "unsupported_filter_family",
                "source_text": source_text,
                "suggested_handling": "block_search",
                "confidence": confidence or "medium",
            }
        ], [source_text, canonical_id.replace("_", " ")]

    if not is_active_family(family):
        canonical_id = _canonical_unapplied_family_id(family)
        return [], [
            {
                "id": canonical_id,
                "mode": "required",
                "reason": "insufficient_schema",
                "source_text": source_text,
                "suggested_handling": "block_search",
                "confidence": confidence or "medium",
            }
        ], [source_text, canonical_id.replace("_", " ")]

    return [], [], []


def _translate_model_payload(
    parsed: Mapping[str, Any],
    *,
    analyzer: Any,
    raw_prompt: str,
    rank: str | None,
) -> dict[str, Any]:
    raw_input = parsed.get("input") if isinstance(parsed.get("input"), Mapping) else {}
    rank_context = raw_input.get("rank_context") if isinstance(raw_input, Mapping) else None
    canonical_rank = _normalize_rank_value(analyzer, rank or rank_context)
    prompt_text = str(raw_input.get("raw_prompt") or raw_prompt or "").strip()

    applied_constraints: list[dict[str, Any]] = []
    unapplied_constraints: list[dict[str, Any]] = []
    semantic_fragments: list[str] = []

    if isinstance(parsed.get("hard_constraints"), list) or isinstance(parsed.get("recruiter_requirements"), list):
        for item in parsed.get("hard_constraints") or []:
            if isinstance(item, Mapping):
                family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
                translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                    family,
                    item,
                    analyzer=analyzer,
                    raw_prompt=raw_prompt,
                    rank=rank,
                    canonical_rank=canonical_rank,
                )
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
        for item in parsed.get("recruiter_requirements") or []:
            if isinstance(item, Mapping):
                family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
                translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                    family,
                    item,
                    analyzer=analyzer,
                    raw_prompt=raw_prompt,
                    rank=rank,
                    canonical_rank=canonical_rank,
                )
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
    else:
        for item in parsed.get("applied_constraints") or []:
            if not isinstance(item, Mapping):
                continue
            family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
            translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                family,
                item,
                analyzer=analyzer,
                raw_prompt=raw_prompt,
                rank=rank,
                canonical_rank=canonical_rank,
            )
            if translated_applied or translated_unapplied:
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
                continue
            if isinstance(item.get("constraint"), Mapping) and family:
                applied_constraints.append(dict(item))

    for item in parsed.get("unapplied_constraints") or []:
        if not isinstance(item, Mapping):
            continue
        family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
        if not family:
            continue
        translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
            family,
            item,
            analyzer=analyzer,
            raw_prompt=raw_prompt,
            rank=rank,
            canonical_rank=canonical_rank,
        )
        if translated_applied or translated_unapplied:
            applied_constraints.extend(translated_applied)
            unapplied_constraints.extend(translated_unapplied)
            semantic_fragments.extend(translated_fragments)
            continue

        if is_active_family(family):
            if family == "certificate_requirement":
                source_text = _first_string(item.get("source_text"), item.get("details"), item.get("display_value"), raw_prompt) or raw_prompt
                canonical_unapplied = {
                    "id": "certificate_requirement",
                    "mode": item.get("mode") if item.get("mode") in {"required", "preferred"} else "required",
                    "reason": item.get("reason") if item.get("reason") in {"unsupported_filter_family", "unsupported_value", "ambiguous_value", "insufficient_schema", "validation_failed"} else "insufficient_schema",
                    "source_text": source_text,
                    "suggested_handling": item.get("suggested_handling") if item.get("suggested_handling") in {"block_search", "semantic_with_warning", "ignore_with_warning"} else "block_search",
                    "confidence": item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium",
                }
                unapplied_constraints.append(canonical_unapplied)
                semantic_fragments.append(source_text)
                semantic_fragments.append("certificate requirement")
                continue
            raise ShadowLLMTranslationError(f"active family {family} could not be translated from unapplied_constraints")

        allowed_keys = {"id", "mode", "reason", "source_text", "suggested_handling", "confidence"}
        if set(item.keys()).issubset(allowed_keys) and isinstance(item.get("id"), str) and item.get("id"):
            canonical_unapplied = dict(item)
            canonical_unapplied["id"] = _canonical_unapplied_family_id(str(canonical_unapplied["id"]))
            unapplied_constraints.append(canonical_unapplied)
            semantic_fragments.append(str(canonical_unapplied.get("source_text") or ""))
            semantic_fragments.append(str(canonical_unapplied.get("id") or "").replace("_", " "))
            continue

        source_text = _first_string(item.get("source_text"), item.get("details"), item.get("display_value"), raw_prompt) or raw_prompt
        canonical_id = _canonical_unapplied_family_id(family)
        mode = item.get("mode") if item.get("mode") in {"required", "preferred"} else "required"
        reason = item.get("reason") if item.get("reason") in {"unsupported_filter_family", "unsupported_value", "ambiguous_value", "insufficient_schema", "validation_failed"} else (
            "unsupported_filter_family" if is_unsupported_family(family) or family in {"vessel_type", "sea_service"} else "insufficient_schema"
        )
        suggested_handling = item.get("suggested_handling") if item.get("suggested_handling") in {"block_search", "semantic_with_warning", "ignore_with_warning"} else ("block_search" if mode == "required" else "semantic_with_warning")
        confidence = item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium"
        canonical_unapplied = {
            "id": canonical_id,
            "mode": mode,
            "reason": reason,
            "source_text": source_text,
            "suggested_handling": suggested_handling,
            "confidence": confidence,
        }
        unapplied_constraints.append(canonical_unapplied)
        semantic_fragments.append(source_text)
        semantic_fragments.append(canonical_id.replace("_", " "))

    if not any(constraint.get("id") == "rank_match" for constraint in applied_constraints) and not any(
        constraint.get("id") == "coc_grade_match" for constraint in applied_constraints
    ):
        extract_rank_constraint = getattr(analyzer, "_extract_rank_constraint", None)
        if callable(extract_rank_constraint):
            try:
                inferred_rank = extract_rank_constraint(prompt_text)
            except Exception:
                inferred_rank = None
            if isinstance(inferred_rank, Mapping):
                inferred_ranks = inferred_rank.get("applied_rank_normalized") or []
                if inferred_ranks:
                    rank_value = _first_string(*inferred_ranks, canonical_rank)
                    if rank_value:
                        applied_constraints.append(
                            _make_applied_constraint(
                                "rank_match",
                                {"type": "rank_match", "rank": rank_value},
                                source_text=prompt_text or raw_prompt,
                                confidence="high",
                            )
                        )
                        semantic_fragments.extend([rank_value.replace("_", " "), rank_value])

    if not any(constraint.get("id") == "us_visa" for constraint in applied_constraints):
        extract_us_visa_constraint = getattr(analyzer, "_extract_us_visa_constraint", None)
        if callable(extract_us_visa_constraint):
            try:
                visa = extract_us_visa_constraint(prompt_text)
            except Exception:
                visa = None
            if isinstance(visa, Mapping):
                required = _first_present(visa.get("required"), visa.get("must_be_valid"))
                if not _is_false_value(required):
                    accepted_types = visa.get("accepted_types") or []
                    display_value = _first_string(visa.get("requested_label"), visa.get("display_value"), prompt_text)
                    months_remaining = _as_positive_int(visa.get("minimum_months_remaining") or visa.get("months_remaining"))
                    applied_constraints.append(
                        _make_applied_constraint(
                            "us_visa",
                            {"type": "us_visa", "required": True, "minimum_months_remaining": months_remaining},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend(
                        ["valid us visa", "us visa", "visa required", "valid visa"]
                        + [str(visa_type).lower() for visa_type in accepted_types if isinstance(visa_type, str)]
                    )

    if not any(constraint.get("id") == "availability" for constraint in applied_constraints):
        extract_availability_constraint = getattr(analyzer, "_extract_availability_constraint", None)
        if callable(extract_availability_constraint):
            try:
                availability = extract_availability_constraint(prompt_text)
            except Exception:
                availability = None
            if isinstance(availability, Mapping):
                value_type = availability.get("value_type")
                display_value = _first_string(availability.get("display_value"), prompt_text)
                if value_type == "status":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {"type": "availability", "status": "available", "available_by": None},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "join immediately", "available immediately"])
                elif value_type == "date":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {
                                "type": "availability",
                                "status": "available_by_date",
                                "available_by": availability.get("available_from_date"),
                            },
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "available from"])
                elif value_type == "relative_phrase":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {"type": "availability", "status": "available_by_date", "available_by": None},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "joinable in", "available by date"])

    if not any(constraint.get("id") in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"} for constraint in applied_constraints):
        extract_endorsement_constraint = getattr(analyzer, "_extract_endorsement_constraint", None)
        if callable(extract_endorsement_constraint):
            try:
                endorsement = extract_endorsement_constraint(prompt_text)
            except Exception:
                endorsement = None
            if isinstance(endorsement, Mapping):
                endorsements = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_endorsement_values())
                certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values())
                display_value = _first_string(endorsement.get("display_value"), prompt_text)
                if certificates:
                    applied_constraints.append(
                        _make_applied_constraint(
                            "certificate_requirement",
                            {"type": "certificate_requirement", "certificates_required": certificates},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([token.replace("_", " ") for token in certificates])
                if endorsements:
                    applied_constraints.append(
                        _make_applied_constraint(
                            "stcw_endorsement",
                            {"type": "stcw_endorsement", "endorsements_required": endorsements},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([token.replace("_", " ") for token in endorsements] + [f"{token.replace('_', ' ')} endorsement" for token in endorsements])

    if not any(constraint.get("id") == "coc_grade_match" for constraint in applied_constraints):
        extract_coc_grade_constraint = getattr(analyzer, "_extract_coc_grade_constraint", None)
        if callable(extract_coc_grade_constraint):
            try:
                coc_grade = extract_coc_grade_constraint(prompt_text)
            except Exception:
                coc_grade = None
            if isinstance(coc_grade, Mapping):
                grades = coc_grade.get("required_grades") or []
                grade_value = _first_string(*grades)
                if grade_value:
                    applied_constraints.append(
                        _make_applied_constraint(
                            "coc_grade_match",
                            {"type": "coc_grade_match", "grade": grade_value},
                            source_text=_first_string(coc_grade.get("display_value"), prompt_text) or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([grade_value.replace("_", " ")])

    if any(
        constraint.get("id") in {"us_visa", "certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"}
        for constraint in applied_constraints
    ):
        unapplied_constraints = [
            item
            for item in unapplied_constraints
            if item.get("id") != "certificate_requirement" or not _certificate_requirement_consumed_by_repair(item, applied_constraints)
        ]

    semantic_query = parsed.get("semantic_query")
    if isinstance(semantic_query, Mapping):
        fuzzy = semantic_query.get("fuzzy_suitability")
        if isinstance(fuzzy, list):
            semantic_query = " ".join(str(part).strip() for part in fuzzy if str(part).strip())
        else:
            semantic_query = ""
    elif isinstance(semantic_query, list):
        semantic_query = " ".join(str(part).strip() for part in semantic_query if str(part).strip())
    else:
        semantic_query = str(semantic_query or "").strip()
    for fragment in semantic_fragments:
        semantic_query = _strip_phrase(str(semantic_query or ""), fragment)
    semantic_query = _cleanup_semantic_residual(semantic_query)
    if semantic_fragments and semantic_query.lower() in {"valid", "required", "mandatory", "must"}:
        semantic_query = ""

    return {
        "schema_version": "query_plan.v1",
        "normalizer": {
            "name": "llm",
            "model": str(parsed.get("normalizer", {}).get("model") or _config_value(getattr(analyzer, "config", None), "reasoning_model", SHADOW_LLM_DEFAULT_MODEL)),
            "prompt_template_version": SHADOW_LLM_PROMPT_TEMPLATE_VERSION,
            "catalog_version": str(parsed.get("normalizer", {}).get("catalog_version") or parsed.get("catalog_version") or CATALOG_VERSION),
            "created_at": str(parsed.get("normalizer", {}).get("created_at") or _utc_now_iso()),
        },
        "input": {
            "raw_prompt": prompt_text,
            "rank_context": rank_context if isinstance(rank_context, str) or rank_context is None else str(rank_context),
            "ui_filters": {
                "schema_version": "ui_filters.v1",
                "filters": [],
            },
        },
        "applied_constraints": applied_constraints,
        "unapplied_constraints": unapplied_constraints,
        "semantic_query": semantic_query,
        "unrecognized_residual": list(parsed.get("unrecognized_residual") or []),
        "warnings": list(parsed.get("warnings") or []),
        "validation": {"status": "valid", "errors": []},
    }


def _result(plan: Mapping[str, Any] | None, diagnostics: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"plan": plan, "diagnostics": dict(diagnostics)}


def build_shadow_llm_query_plan(
    analyzer: Any,
    *,
    prompt: str,
    rank: str | None = None,
    prompt_id: str | None = None,
    legacy_plan: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Call Gemini in shadow mode and normalize the returned JSON plan."""

    if not is_enabled():
        return _result(None, {"status": "disabled", "reason": "feature_flag_disabled"})

    prompt_text = str(prompt or "").strip()
    canonical_rank = _normalize_rank_value(analyzer, rank)
    config = getattr(analyzer, "config", None)
    api_key = _config_value(config, "gemini_api_key")
    model = _config_value(config, "reasoning_model", SHADOW_LLM_DEFAULT_MODEL)
    timeout = getattr(analyzer, "LLM_REQUEST_TIMEOUT_SECONDS", getattr(getattr(analyzer, "__class__", object), "LLM_REQUEST_TIMEOUT_SECONDS", 45))

    if not api_key or not model:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "missing_api_credentials" if not api_key else "missing_model",
                "model": model,
                "has_api_key": bool(api_key),
            },
        )

    request_payload = {
        "contents": [{"parts": [{"text": build_shadow_llm_prompt(prompt, rank=rank)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "candidateCount": 1,
            "seed": SHADOW_LLM_RESPONSE_SEED,
        },
    }
    api_url = SHADOW_LLM_API_URL.format(model=model)
    headers = {"Content-Type": "application/json", "x-goog-api-key": str(api_key)}

    try:
        response = requests.post(api_url, headers=headers, json=request_payload, timeout=timeout)
        response.raise_for_status()
        body = response.json() if hasattr(response, "json") else {}
        result_text = body.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
        parsed = _extract_json_payload(result_text)
        if not parsed:
            return _result(
                legacy_plan,
                {
                    "status": "fallback",
                    "reason": "invalid_model_json",
                    "model": model,
                    "http_status": getattr(response, "status_code", None),
                    "response_excerpt": str(result_text or "")[:500],
                },
            )

        candidate_plan = normalize_query_plan_v1(
            _translate_model_payload(parsed, analyzer=analyzer, raw_prompt=prompt, rank=rank),
            mode="production",
        )
        if candidate_plan.get("validation", {}).get("status") == "invalid":
            return _result(
                legacy_plan,
                {
                    "status": "fallback",
                    "reason": "schema_invalid",
                    "model": model,
                    "http_status": getattr(response, "status_code", None),
                    "response_excerpt": str(result_text or "")[:500],
                },
            )
        candidate_plan["normalizer"]["name"] = "llm"
        candidate_plan["normalizer"]["model"] = str(model)
        candidate_plan["normalizer"]["prompt_template_version"] = SHADOW_LLM_PROMPT_TEMPLATE_VERSION
        candidate_plan["normalizer"]["catalog_version"] = candidate_plan["normalizer"].get("catalog_version") or CATALOG_VERSION
        candidate_plan["normalizer"]["created_at"] = candidate_plan["normalizer"].get("created_at") or _utc_now_iso()
        applied_constraints = list(candidate_plan.get("applied_constraints") or [])
        unapplied_constraints = list(candidate_plan.get("unapplied_constraints") or [])
        if not any(constraint.get("id") == "rank_match" for constraint in applied_constraints) and not any(
            constraint.get("id") == "coc_grade_match" for constraint in applied_constraints
        ):
            extract_rank_constraint = getattr(analyzer, "_extract_rank_constraint", None)
            if callable(extract_rank_constraint):
                try:
                    inferred_rank = extract_rank_constraint(prompt_text)
                except Exception:
                    inferred_rank = None
                if isinstance(inferred_rank, Mapping):
                    inferred_ranks = inferred_rank.get("applied_rank_normalized") or []
                    rank_value = _first_string(*inferred_ranks, canonical_rank)
                    if rank_value:
                        unapplied_constraints = [item for item in unapplied_constraints if item.get("id") != "rank_match"]
                        applied_constraints.append(
                            _make_applied_constraint(
                                "rank_match",
                                {"type": "rank_match", "rank": rank_value},
                                source_text=prompt_text or raw_prompt,
                                confidence="high",
                            )
                        )
        coc_grade_values = [
            _first_string(
                (constraint.get("constraint") or {}).get("grade"),
                (constraint.get("constraint") or {}).get("coc_grade"),
                (constraint.get("constraint") or {}).get("required_grade"),
            )
            for constraint in applied_constraints
            if constraint.get("id") == "coc_grade_match"
        ]
        coc_grade_values = [value for value in coc_grade_values if value]
        if coc_grade_values:
            grade_phrases = {value.replace("_", " ").lower() for value in coc_grade_values}
            applied_constraints = [
                constraint
                for constraint in applied_constraints
                if not (
                    constraint.get("id") == "rank_match"
                    and any(phrase and phrase in str(constraint.get("source_text") or "").lower() for phrase in grade_phrases)
                )
            ]
        candidate_plan["applied_constraints"] = applied_constraints
        candidate_plan["unapplied_constraints"] = unapplied_constraints
        candidate_plan = normalize_query_plan_v1(candidate_plan, mode="production")
        if candidate_plan.get("validation", {}).get("status") == "invalid":
            return _result(
                legacy_plan,
                {
                    "status": "fallback",
                    "reason": "schema_invalid",
                    "model": model,
                    "http_status": getattr(response, "status_code", None),
                    "response_excerpt": str(result_text or "")[:500],
                },
            )
        candidate_plan["normalizer"]["name"] = "llm"
        candidate_plan["normalizer"]["model"] = str(model)
        candidate_plan["normalizer"]["prompt_template_version"] = SHADOW_LLM_PROMPT_TEMPLATE_VERSION
        candidate_plan["normalizer"]["catalog_version"] = candidate_plan["normalizer"].get("catalog_version") or CATALOG_VERSION
        candidate_plan["normalizer"]["created_at"] = candidate_plan["normalizer"].get("created_at") or _utc_now_iso()
        return _result(
            candidate_plan,
            {
                "status": "success",
                "reason": "ok",
                "model": model,
                "http_status": getattr(response, "status_code", None),
            },
        )
    except ShadowLLMTranslationError as exc:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "schema_invalid",
                "model": model,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
    except Exception as exc:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "request_exception",
                "model": model,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
