"""Provider-scoped helper tools for the compound-prompt normalizer.

These helpers are deterministic normalization aids. They do not dispatch
constraints and do not call evaluator or search code.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Mapping

from candidate_facts.aliases.filter_capability_catalog import (
    FilterCapabilityCatalog,
    load_filter_capability_catalog,
    validate_catalog_parameters,
)


HELPER_TOOL_VERSION = "1.0.0"
LOCATE_PROMPT_SPAN_TOOL_ID = "locate_prompt_span.v1"
PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID = "parse_availability_date_phrase.v1"
CHECK_AVAILABILITY_PARAMETERS_TOOL_ID = "check_availability_parameters.v1"
CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID = "classify_availability_conflict.v1"
PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID = "parse_vessel_tonnage_phrase.v1"
CHECK_VESSEL_TONNAGE_PARAMETERS_TOOL_ID = "check_vessel_tonnage_parameters.v1"
CLASSIFY_VESSEL_TONNAGE_SCOPE_TOOL_ID = "classify_vessel_tonnage_scope.v1"

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tool_result(tool_id: str, accepted: bool, result: Mapping[str, Any] | None = None, errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "accepted": bool(accepted),
        "result": dict(result or {}) if accepted else {},
        "errors": [] if accepted else list(errors or ["rejected"]),
    }


def helper_tool_audit_record(tool_input: Mapping[str, Any], output: Mapping[str, Any]) -> dict[str, Any]:
    """Return the hash-only audit shape for one helper invocation."""

    return {
        "tool_id": str(output.get("tool_id") or ""),
        "input_hash": _json_hash(tool_input),
        "accepted": bool(output.get("accepted")),
        "result_hash": _json_hash(output.get("result") if isinstance(output.get("result"), Mapping) else {}),
        "errors": list(output.get("errors") if isinstance(output.get("errors"), list) else []),
    }


def locate_prompt_span(prompt_normalized: str, text: str) -> dict[str, Any]:
    needle = str(text or "").strip()
    if not needle:
        return _tool_result(LOCATE_PROMPT_SPAN_TOOL_ID, False, errors=["text is required"])
    start = str(prompt_normalized or "").find(needle)
    if start < 0:
        return _tool_result(LOCATE_PROMPT_SPAN_TOOL_ID, False, errors=["text not found"])
    if str(prompt_normalized or "").find(needle, start + 1) >= 0:
        return _tool_result(LOCATE_PROMPT_SPAN_TOOL_ID, False, errors=["text appears more than once"])
    return _tool_result(
        LOCATE_PROMPT_SPAN_TOOL_ID,
        True,
        {"span": {"text": needle, "start": start, "end": start + len(needle)}},
    )


def _parse_reference_date(reference_date: str | date | None) -> date | None:
    if isinstance(reference_date, date):
        return reference_date
    try:
        return date.fromisoformat(str(reference_date or ""))
    except ValueError:
        return None


def _date_from_parts(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_numeric_date(text: str) -> tuple[date | None, str | None]:
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if not match:
        return None, None
    first, second, year_raw = (int(part) for part in match.groups())
    year = year_raw + 2000 if year_raw < 100 else year_raw
    if first <= 12 and second <= 12:
        return None, "ambiguous numeric date"
    if first > 12:
        parsed = _date_from_parts(year, second, first)
    else:
        parsed = _date_from_parts(year, first, second)
    return parsed, None if parsed else "invalid calendar date"


def _parse_month_name_date(text: str) -> tuple[date | None, str | None]:
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[\s/-]+([A-Za-z]+)[\s,/-]+(\d{2,4})", text, re.IGNORECASE)
    if not match:
        match = re.fullmatch(r"([A-Za-z]+)[\s/-]+(\d{1,2})(?:st|nd|rd|th)?[\s,/-]+(\d{2,4})", text, re.IGNORECASE)
        if not match:
            return None, None
        month_raw, day_raw, year_raw = match.groups()
    else:
        day_raw, month_raw, year_raw = match.groups()
    month = _MONTHS.get(month_raw.lower())
    if not month:
        return None, "unknown month"
    year = int(year_raw)
    if year < 100:
        year += 2000
    parsed = _date_from_parts(year, month, int(day_raw))
    return parsed, None if parsed else "invalid calendar date"


def parse_availability_date_phrase(text: str, reference_date: str | date | None = None) -> dict[str, Any]:
    phrase = str(text or "").strip()
    if not phrase:
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=["text is required"])
    try:
        parsed = date.fromisoformat(phrase)
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, True, {"date": parsed.isoformat(), "kind": "absolute"})
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", phrase):
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=["invalid calendar date"])
    parsed, error = _parse_numeric_date(phrase)
    if parsed:
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, True, {"date": parsed.isoformat(), "kind": "absolute"})
    if error:
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=[error])
    parsed, error = _parse_month_name_date(phrase)
    if parsed:
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, True, {"date": parsed.isoformat(), "kind": "absolute"})
    if error:
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=[error])
    match = re.fullmatch(r"(?:within|after)\s+(\d{1,3})\s+days?", phrase, re.IGNORECASE)
    if match:
        anchor = _parse_reference_date(reference_date)
        if not anchor:
            return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=["reference date is required"])
        days = int(match.group(1))
        return _tool_result(
            PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID,
            True,
            {"date": (anchor + timedelta(days=days)).isoformat(), "kind": "relative_days", "relative_days": days},
        )
    if re.search(r"\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day)?s?\b", phrase, re.IGNORECASE):
        return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=["day-of-week availability is unsupported"])
    return _tool_result(PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID, False, errors=["unsupported date phrase"])


def check_availability_parameters(parameters: Mapping[str, Any], catalog: FilterCapabilityCatalog | None = None) -> dict[str, Any]:
    loaded = catalog or load_filter_capability_catalog()
    if not isinstance(parameters, Mapping):
        return _tool_result(CHECK_AVAILABILITY_PARAMETERS_TOOL_ID, False, errors=["parameters must be an object"])
    try:
        validate_catalog_parameters("availability", parameters, catalog=loaded)
    except ValueError as exc:
        return _tool_result(CHECK_AVAILABILITY_PARAMETERS_TOOL_ID, False, errors=[str(exc)])
    return _tool_result(CHECK_AVAILABILITY_PARAMETERS_TOOL_ID, True, {"parameters": dict(parameters)})


def classify_availability_conflict(text: str) -> dict[str, Any]:
    phrase = str(text or "").strip()
    lower = phrase.lower()
    has_immediate = bool(re.search(r"\b(?:immediate(?:ly)?|asap|available now|ready to join|join asap)\b", lower))
    has_delayed = bool(re.search(r"\b(?:not\s+available\s+until|available\s+after|available\s+from|can\s+join\s+from)\b", lower))
    has_day_of_week = bool(re.search(r"\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day)?s?\b", lower))
    if has_immediate and has_delayed:
        return _tool_result(
            CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID,
            True,
            {"route": "needs_review", "reason": "contradictory availability instructions"},
        )
    if has_day_of_week:
        return _tool_result(
            CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID,
            True,
            {"route": "unapplied", "reason": "out of scope"},
        )
    return _tool_result(CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID, True, {"route": "constraints"})


_TONNAGE_NUMBER = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?k?"
_TONNAGE_UNIT = r"(?:GT|GRT|DWT|deadweight|tonnage|vessel\s+tonnage|tons?)"


def _normalize_tonnage_unit(text: str) -> str:
    lower = str(text or "").lower()
    if re.search(r"\b(?:dwt|deadweight)\b", lower):
        return "dwt"
    if re.search(r"\b(?:gt|grt)\b", lower):
        return "gt_grt"
    if re.search(r"\b(?:tonnage|tons?)\b", lower):
        return "unspecified"
    return "unspecified"


def _parse_tonnage_number(text: str, *, value_type: str) -> int | None:
    raw = str(text or "").strip().lower().replace(",", "")
    if raw.startswith("-"):
        return None
    is_k = raw.endswith("k")
    if is_k:
        raw = raw[:-1]
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    if is_k:
        whole = int(value)
        parsed = whole * 1000
        if value_type == "maximum" and whole % 10 in {2, 7}:
            parsed += 500
        return parsed
    if not value.is_integer():
        return None
    return int(value)


def _extract_tonnage_years_back(text: str) -> int | None:
    match = re.search(r"\b(?:in\s+)?(?:the\s+)?(?:last|past|previous|within\s+last)\s+(\d{1,2})\s+years?\b", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_vessel_tonnage_phrase(text: str) -> dict[str, Any]:
    phrase = " ".join(str(text or "").split()).strip(" .,;")
    if not phrase:
        return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["text is required"])
    lower = phrase.lower()
    if re.search(r"\b(?:nrt|net\s+tonnage|net\s+registered\s+tonnage)\b", lower):
        return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["unsupported tonnage type"])
    if re.search(r"\b(?:kw|kilowatt|bhp|engine\s+power)\b", lower):
        return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["engine power is out of scope"])
    if re.search(r"-\s*\d", lower):
        return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["negative tonnage"])

    years_back = _extract_tonnage_years_back(phrase)
    range_match = re.search(
        rf"\b(?:vessel\s+tonnage\s+)?between\s+({_TONNAGE_NUMBER})\s+(?:and|-)\s+({_TONNAGE_NUMBER})(?:\s+({_TONNAGE_UNIT}))?",
        phrase,
        re.IGNORECASE,
    )
    if not range_match:
        range_match = re.search(
            rf"\bfrom\s+({_TONNAGE_NUMBER})\s+up\s+to\s+({_TONNAGE_NUMBER})(?:\s+({_TONNAGE_UNIT}))?",
            phrase,
            re.IGNORECASE,
        )
    if range_match:
        first = _parse_tonnage_number(range_match.group(1), value_type="range")
        second = _parse_tonnage_number(range_match.group(2), value_type="range")
        if first is None or second is None:
            return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["invalid tonnage number"])
        if first > second:
            return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["reversed tonnage range"])
        unit = _normalize_tonnage_unit(range_match.group(3) or phrase)
        return _tool_result(
            PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID,
            True,
            {"value_type": "range", "min_value": first, "max_value": second, "unit": unit, "years_back": years_back},
        )

    exact_match = re.search(rf"\b(?:exactly|approximately|around)\s+({_TONNAGE_NUMBER})(?:\s+({_TONNAGE_UNIT}))?", phrase, re.IGNORECASE)
    if exact_match:
        value = _parse_tonnage_number(exact_match.group(1), value_type="range")
        if value is None:
            return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["invalid tonnage number"])
        return _tool_result(
            PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID,
            True,
            {
                "value_type": "range",
                "min_value": value,
                "max_value": value,
                "unit": _normalize_tonnage_unit(exact_match.group(2) or phrase),
                "years_back": years_back,
            },
        )

    maximum_match = re.search(
        rf"\b(?:below|under|up\s+to|at\s+most|less\s+than|not\s+above)\s+({_TONNAGE_NUMBER})(?:\s+({_TONNAGE_UNIT}))?",
        phrase,
        re.IGNORECASE,
    )
    if maximum_match:
        value = _parse_tonnage_number(maximum_match.group(1), value_type="maximum")
        if value is None:
            return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["invalid tonnage number"])
        return _tool_result(
            PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID,
            True,
            {
                "value_type": "maximum",
                "min_value": None,
                "max_value": value,
                "unit": _normalize_tonnage_unit(maximum_match.group(2) or phrase),
                "years_back": years_back,
            },
        )

    minimum_match = re.search(
        rf"\b(?:above|over|at\s+least|minimum|min|more\s+than|greater\s+than|not\s+less\s+than)\s+({_TONNAGE_NUMBER})(?:\s+({_TONNAGE_UNIT}))?",
        phrase,
        re.IGNORECASE,
    )
    if not minimum_match:
        minimum_match = re.search(rf"\b({_TONNAGE_NUMBER})\s*\+\s*({_TONNAGE_UNIT})?", phrase, re.IGNORECASE)
    if not minimum_match:
        minimum_match = re.search(rf"\b({_TONNAGE_NUMBER})\s+({_TONNAGE_UNIT})\b", phrase, re.IGNORECASE)
    if minimum_match:
        value = _parse_tonnage_number(minimum_match.group(1), value_type="minimum")
        if value is None:
            return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["invalid tonnage number"])
        unit = _normalize_tonnage_unit((minimum_match.group(2) if len(minimum_match.groups()) >= 2 else None) or phrase)
        return _tool_result(
            PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID,
            True,
            {"value_type": "minimum", "min_value": value, "max_value": None, "unit": unit, "years_back": years_back},
        )

    return _tool_result(PARSE_VESSEL_TONNAGE_PHRASE_TOOL_ID, False, errors=["unsupported tonnage phrase"])


def check_vessel_tonnage_parameters(parameters: Mapping[str, Any], catalog: FilterCapabilityCatalog | None = None) -> dict[str, Any]:
    loaded = catalog or load_filter_capability_catalog()
    if not isinstance(parameters, Mapping):
        return _tool_result(CHECK_VESSEL_TONNAGE_PARAMETERS_TOOL_ID, False, errors=["parameters must be an object"])
    try:
        validate_catalog_parameters("vessel_tonnage", parameters, catalog=loaded)
    except ValueError as exc:
        return _tool_result(CHECK_VESSEL_TONNAGE_PARAMETERS_TOOL_ID, False, errors=[str(exc)])
    return _tool_result(CHECK_VESSEL_TONNAGE_PARAMETERS_TOOL_ID, True, {"parameters": dict(parameters)})


def classify_vessel_tonnage_scope(text: str) -> dict[str, Any]:
    phrase = str(text or "").strip()
    lower = phrase.lower()
    if re.search(r"\b(?:nrt|net\s+tonnage|net\s+registered\s+tonnage)\b", lower):
        return _tool_result(CLASSIFY_VESSEL_TONNAGE_SCOPE_TOOL_ID, True, {"route": "needs_review", "reason": "unsupported tonnage type"})
    if re.search(r"\b(?:-\s*\d+\s*(?:vessel\s+)?tonnage|between\s+\d[\d,]*\s+and\s+\d[\d,]*\s+tonnage)\b", lower):
        parsed = parse_vessel_tonnage_phrase(phrase)
        if not parsed.get("accepted"):
            return _tool_result(CLASSIFY_VESSEL_TONNAGE_SCOPE_TOOL_ID, True, {"route": "needs_review", "reason": (parsed.get("errors") or ["malformed tonnage"])[0]})
    if re.search(r"\b(?:age|years?\s+old|months?\s+experience|sea\s+service|kw|kilowatt|bhp|engine\s+power|contracts?)\b", lower) and not re.search(r"\b(?:tonnage|gt|grt|dwt|deadweight)\b", lower):
        return _tool_result(CLASSIFY_VESSEL_TONNAGE_SCOPE_TOOL_ID, True, {"route": "unapplied", "reason": "out of scope"})
    return _tool_result(CLASSIFY_VESSEL_TONNAGE_SCOPE_TOOL_ID, True, {"route": "constraints"})


def availability_helper_tool_context(
    prompt_normalized: str,
    *,
    reference_date: str | date | None,
    catalog: FilterCapabilityCatalog | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return LLM-visible helper outputs and hash-only audit records."""

    loaded = catalog or load_filter_capability_catalog()
    helper_outputs: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []

    def record(tool_input: Mapping[str, Any], output: dict[str, Any]) -> None:
        helper_outputs.append(output)
        audit_records.append(helper_tool_audit_record(tool_input, output))

    conflict_input = {"text": prompt_normalized}
    record(conflict_input, classify_availability_conflict(prompt_normalized))

    phrase_patterns = [
        r"\bavailable immediately\b",
        r"\bimmediately available\b",
        r"\bavailable now\b",
        r"\bready to join\b",
        r"\bjoin asap\b",
        r"\bavailable within \d{1,3} days?\b",
        r"\bready to join after \d{1,3} days?\b",
        r"\bavailable (?:by|before|from|after) [A-Za-z0-9,/\-\s]{4,24}",
    ]
    seen_phrases: set[str] = set()
    for pattern in phrase_patterns:
        for match in re.finditer(pattern, prompt_normalized, re.IGNORECASE):
            phrase = " ".join(match.group(0).split()).strip(" .,;")
            if not phrase or phrase.lower() in seen_phrases:
                continue
            seen_phrases.add(phrase.lower())
            locate_input = {"prompt_normalized": prompt_normalized, "text": phrase}
            record(locate_input, locate_prompt_span(prompt_normalized, phrase))
            date_match = re.search(r"(?:within|after)\s+\d{1,3}\s+days?|(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|(?:\d{1,2}(?:st|nd|rd|th)?[\s/-]+[A-Za-z]+[\s,/-]+\d{2,4})|(?:[A-Za-z]+[\s/-]+\d{1,2}(?:st|nd|rd|th)?[\s,/-]+\d{2,4})", phrase, re.IGNORECASE)
            if date_match:
                date_input = {"text": date_match.group(0), "reference_date": str(reference_date or "")}
                record(date_input, parse_availability_date_phrase(date_match.group(0), reference_date=reference_date))

    relative_match = re.search(r"\b(?:within|after)\s+(\d{1,3})\s+days?\b", prompt_normalized, re.IGNORECASE)
    if relative_match:
        parameters = {
            "version": "v1",
            "value_type": "relative_days",
            "status": None,
            "available_by_date": None,
            "available_from_date": None,
            "available_until_date": None,
            "relative_days": int(relative_match.group(1)),
            "resolved_reference_date": str(reference_date or ""),
            "display_value": relative_match.group(0),
        }
        param_input = {"parameters": parameters}
        record(param_input, check_availability_parameters(parameters, catalog=loaded))

    if len(helper_outputs) == 1:
        # Ensure every prompt records at least one span-locator attempt for the helper pilot audit.
        locate_input = {"prompt_normalized": prompt_normalized, "text": "available"}
        record(locate_input, locate_prompt_span(prompt_normalized, "available"))

    return helper_outputs, audit_records


def vessel_tonnage_helper_tool_context(
    prompt_normalized: str,
    *,
    reference_date: str | date | None = None,
    catalog: FilterCapabilityCatalog | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return LLM-visible vessel-tonnage helper outputs and hash-only audit records."""

    loaded = catalog or load_filter_capability_catalog()
    helper_outputs: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []

    def record(tool_input: Mapping[str, Any], output: dict[str, Any]) -> None:
        helper_outputs.append(output)
        audit_records.append(helper_tool_audit_record(tool_input, output))

    scope_input = {"text": prompt_normalized}
    record(scope_input, classify_vessel_tonnage_scope(prompt_normalized))

    phrase_patterns = [
        rf"\b(?:vessel\s+tonnage\s+)?between\s+{_TONNAGE_NUMBER}\s+(?:and|-)\s+{_TONNAGE_NUMBER}(?:\s+{_TONNAGE_UNIT})?",
        rf"\bfrom\s+{_TONNAGE_NUMBER}\s+up\s+to\s+{_TONNAGE_NUMBER}(?:\s+{_TONNAGE_UNIT})?",
        rf"\b(?:exactly|approximately|around)\s+{_TONNAGE_NUMBER}(?:\s+{_TONNAGE_UNIT})?(?:\s+experience|\s+vessels|\s+tonnage)?",
        rf"\b(?:below|under|up\s+to|at\s+most|less\s+than|not\s+above|above|over|at\s+least|minimum|min|more\s+than|greater\s+than|not\s+less\s+than)\s+{_TONNAGE_NUMBER}(?:\s+{_TONNAGE_UNIT})?(?:\s+in\s+last\s+\d{{1,2}}\s+years?)?",
        rf"\b{_TONNAGE_NUMBER}\s*\+\s*(?:{_TONNAGE_UNIT})?(?:\s+experience)?",
        rf"\b{_TONNAGE_NUMBER}\s+(?:GT|GRT|DWT|deadweight)\s+[A-Za-z][A-Za-z\s-]{{0,40}}",
        rf"-\s*\d[\d,]*\s+(?:vessel\s+)?tonnage",
        r"\bvessel\s+tonnage\s+above\s+[A-Za-z]+",
    ]
    seen_phrases: set[str] = set()
    for pattern in phrase_patterns:
        for match in re.finditer(pattern, prompt_normalized, re.IGNORECASE):
            phrase = " ".join(match.group(0).split()).strip(" .,;")
            if not phrase or phrase.lower() in seen_phrases:
                continue
            seen_phrases.add(phrase.lower())
            locate_input = {"prompt_normalized": prompt_normalized, "text": phrase}
            record(locate_input, locate_prompt_span(prompt_normalized, phrase))
            parse_input = {"text": phrase}
            parsed = parse_vessel_tonnage_phrase(phrase)
            record(parse_input, parsed)
            if parsed.get("accepted") and isinstance(parsed.get("result"), Mapping):
                parsed_result = parsed["result"]
                parameters = {
                    "version": "v1",
                    "value_type": parsed_result.get("value_type"),
                    "min_value": parsed_result.get("min_value"),
                    "max_value": parsed_result.get("max_value"),
                    "unit": parsed_result.get("unit"),
                    "years_back": parsed_result.get("years_back"),
                    "display_value": phrase,
                }
                param_input = {"parameters": parameters}
                record(param_input, check_vessel_tonnage_parameters(parameters, catalog=loaded))

    if len(helper_outputs) == 1:
        # Ensure every prompt records at least one span-locator attempt for the helper pilot audit.
        locate_input = {"prompt_normalized": prompt_normalized, "text": "tonnage"}
        record(locate_input, locate_prompt_span(prompt_normalized, "tonnage"))

    return helper_outputs, audit_records
