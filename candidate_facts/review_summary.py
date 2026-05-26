"""Field-level summary helpers for candidate facts review."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Sequence


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _get_nested(mapping: Mapping[str, Any] | Any, *path: str) -> Any:
    current: Any = mapping
    for part in path:
        if not _is_mapping(current):
            return None
        current = current.get(part)
    return current


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_confidence_level(confidence: Any) -> str | None:
    if confidence is None:
        return None
    if isinstance(confidence, str):
        lowered = confidence.strip().lower()
        if lowered in {"high", "medium", "low"}:
            return lowered
        return lowered or None
    if isinstance(confidence, (int, float)):
        if confidence >= 0.85:
            return "high"
        if confidence >= 0.6:
            return "medium"
        return "low"
    return str(confidence).strip().lower() or None


def _presence_for_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return "unobserved_unknown"
    return "observed_true"


def _display_value(value: Any) -> Any:
    if isinstance(value, list):
        return ", ".join(_normalize_text(item) for item in value if _normalize_text(item)) or "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None:
        return "-"
    return value


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _utc_today() -> date:
    return date.today()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _format_date(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _collect_evidence_index(candidate_facts: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    evidence_index: Dict[str, Dict[str, Any]] = {}
    for evidence in _as_list(candidate_facts.get("evidence")):
        if not _is_mapping(evidence):
            continue
        evidence_id = _normalize_text(evidence.get("evidence_id"))
        if evidence_id:
            evidence_index[evidence_id] = dict(evidence)
    return evidence_index


def _reference_evidence(candidate_facts: Mapping[str, Any], evidence_ids: Sequence[str] | None) -> list[Dict[str, Any]]:
    evidence_index = _collect_evidence_index(candidate_facts)
    references: list[Dict[str, Any]] = []
    for evidence_id in evidence_ids or []:
        evidence = evidence_index.get(str(evidence_id))
        if not evidence:
            continue
        reference = {
            "evidence_id": evidence.get("evidence_id"),
            "source_kind": evidence.get("source_kind"),
            "source_id": evidence.get("source_id"),
        }
        for snippet_key in ("snippet", "text", "source_text", "source_excerpt"):
            snippet_value = evidence.get(snippet_key)
            if snippet_value not in (None, ""):
                reference["snippet"] = snippet_value
                break
        references.append(reference)
    return references


def _fact_snippet(fact: Mapping[str, Any] | None) -> str:
    if not _is_mapping(fact):
        return ""
    for key in ("snippet", "source_excerpt", "text", "source_text"):
        value = fact.get(key)
        if _normalize_text(value):
            return _normalize_text(value)
    extraction = fact.get("extraction")
    if _is_mapping(extraction):
        for key in ("snippet", "source_excerpt", "text", "source_text"):
            value = extraction.get(key)
            if _normalize_text(value):
                return _normalize_text(value)
    meta = fact.get("fact_meta")
    if _is_mapping(meta):
        context = meta.get("context")
        if _is_mapping(context):
            for key in ("snippet", "source_excerpt", "text", "source_text"):
                value = context.get(key)
                if _normalize_text(value):
                    return _normalize_text(value)
    return ""


def _compact_excerpt(value: Any, *, max_chars: int = 120) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    first_line = _normalize_text(first_line)
    if len(first_line) <= max_chars:
        return first_line
    shortened = first_line[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:|-")
    return shortened or first_line[:max_chars]


def _resolve_excerpt(*values: Any) -> str:
    for value in values:
        text = _compact_excerpt(value)
        if text:
            return text
    return ""


def _extract_fact_meta(candidate_facts: Mapping[str, Any], field_path: str) -> Mapping[str, Any] | None:
    fact_meta = candidate_facts.get("fact_meta")
    if not _is_mapping(fact_meta):
        return None
    meta = fact_meta.get(field_path)
    return meta if _is_mapping(meta) else None


def _normalize_value_from_fact(fact: Mapping[str, Any] | None, key: str = "value") -> Any:
    if not _is_mapping(fact):
        return None
    value = fact.get(key)
    if value in (None, ""):
        return None
    return value


def _evidence_ids_from_fact(fact: Mapping[str, Any] | None) -> list[str]:
    if not _is_mapping(fact):
        return []
    evidence_ids = fact.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    return [str(evidence_id) for evidence_id in evidence_ids if _normalize_text(evidence_id)]


def _evidence_ids_for_field(
    fact: Mapping[str, Any] | None,
    *,
    fallback_ids: Sequence[str] | None = None,
    value: Any = None,
) -> list[str]:
    primary_ids = _evidence_ids_from_fact(fact)
    if primary_ids:
        return primary_ids
    if value in (None, "", [], {}):
        return []
    return [str(evidence_id) for evidence_id in (fallback_ids or []) if _normalize_text(evidence_id)]


def _status_from_fact_meta(meta: Mapping[str, Any] | None, value: Any, *, derived: bool = False) -> str:
    if _is_mapping(meta):
        status = _normalize_text(meta.get("status")).upper()
        if status:
            return status
    if value in (None, "", [], {}):
        return "MISSING"
    return "DERIVED" if derived else "PARSED"


def _warning_level(status: str, confidence_level: str | None, value: Any) -> str:
    if status in {"INVALID", "CONFLICT"}:
        return "conflict"
    if value in (None, "", [], {}):
        return "missing"
    if confidence_level == "low":
        return "low_confidence"
    if status in {"UNKNOWN", "SOURCE_EXCLUDED"}:
        return "warning"
    return "ok"


def _build_row(
    *,
    candidate_facts: Mapping[str, Any],
    field_path: str,
    label: str,
    value: Any,
    evidence_ids: Sequence[str] | None = None,
    confidence: Any = None,
    status: str | None = None,
    source_label: str | None = None,
    extraction_method: str | None = None,
    source_excerpt: str | None = None,
    affects_match: bool = False,
    derived: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    meta = _extract_fact_meta(candidate_facts, field_path)
    raw_confidence = confidence if confidence is not None else (meta.get("confidence") if _is_mapping(meta) else None)
    confidence_level = _normalize_confidence_level(raw_confidence)
    normalized_value = value
    normalized_presence = _presence_for_value(normalized_value)
    normalized_status = _normalize_text(status or (meta.get("status") if _is_mapping(meta) else None)).upper()
    if not normalized_status:
        normalized_status = _status_from_fact_meta(meta, normalized_value, derived=derived)
    if confidence_level is None:
        confidence_level = "low" if normalized_presence == "unobserved_unknown" else ("medium" if derived else "high")
    warnings = _warning_level(normalized_status, confidence_level, normalized_value)
    references = _reference_evidence(candidate_facts, list(evidence_ids or []))
    row = {
        "field_path": field_path,
        "label": label,
        "value": normalized_value,
        "display_value": _display_value(normalized_value),
        "presence": normalized_presence,
        "status": normalized_status,
        "confidence": raw_confidence,
        "confidence_level": confidence_level,
        "warning_level": warnings,
        "affects_match": bool(affects_match),
        "evidence_ids": list(dict.fromkeys(str(evidence_id) for evidence_id in (evidence_ids or []) if _normalize_text(evidence_id))),
        "source_references": references,
        "source_reference": references[0] if references else None,
        "source_label": _normalize_text(source_label or (meta.get("source_label") if _is_mapping(meta) else "")),
        "extraction_method": _normalize_text(extraction_method or (meta.get("extraction_method") if _is_mapping(meta) else "")),
        "source_excerpt": _normalize_text(source_excerpt or _fact_snippet(meta)),
    }
    if extra:
        row.update(dict(extra))
    return row


def _fact_from_bucket(candidate_facts: Mapping[str, Any], bucket: str, predicate) -> Mapping[str, Any] | None:
    items = candidate_facts.get(bucket)
    if not isinstance(items, list):
        return None
    for item in items:
        if _is_mapping(item) and predicate(item):
            return item
    return None


def _candidate_name_fact(candidate_facts: Mapping[str, Any]) -> Mapping[str, Any] | None:
    identity = candidate_facts.get("identity") if _is_mapping(candidate_facts.get("identity")) else {}
    candidate_name = identity.get("candidate_name") if _is_mapping(identity) else None
    if _is_mapping(candidate_name):
        return candidate_name
    legacy_identity = candidate_facts.get("identity") if _is_mapping(candidate_facts.get("identity")) else {}
    if _is_mapping(legacy_identity) and legacy_identity.get("full_name"):
        return {"value": legacy_identity.get("full_name")}
    return None


def _dob_value(candidate_facts: Mapping[str, Any]) -> Any:
    identity = candidate_facts.get("identity") if _is_mapping(candidate_facts.get("identity")) else {}
    dob_fact = identity.get("dob") if _is_mapping(identity) else None
    if _is_mapping(dob_fact):
        return dob_fact.get("value")
    personal = candidate_facts.get("personal") if _is_mapping(candidate_facts.get("personal")) else {}
    if _is_mapping(personal):
        return personal.get("dob")
    return None


def _age_years_value(candidate_facts: Mapping[str, Any]) -> Any:
    derived = candidate_facts.get("derived") if _is_mapping(candidate_facts.get("derived")) else {}
    if _is_mapping(derived) and derived.get("age_years") is not None:
        return derived.get("age_years")
    personal = candidate_facts.get("personal") if _is_mapping(candidate_facts.get("personal")) else {}
    if _is_mapping(personal) and personal.get("stated_age") is not None:
        return personal.get("stated_age")
    dob_value = _dob_value(candidate_facts)
    dob = _parse_date(dob_value)
    if dob is None:
        return None
    today = _utc_today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age


def _rank_value(candidate_facts: Mapping[str, Any]) -> Any:
    rank = candidate_facts.get("rank") if _is_mapping(candidate_facts.get("rank")) else {}
    if _is_mapping(rank) and rank.get("value") not in (None, ""):
        return rank.get("value")
    role = candidate_facts.get("role") if _is_mapping(candidate_facts.get("role")) else {}
    if _is_mapping(role):
        for key in ("applied_rank_normalized", "current_rank_normalized", "current_rank_raw", "applied_rank_raw"):
            if role.get(key) not in (None, ""):
                return role.get(key)
    return None


def _passport_document(candidate_facts: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return _fact_from_bucket(candidate_facts, "documents", lambda item: _normalize_text(item.get("document_type")).lower() == "passport")


def _coc_certificate(candidate_facts: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return _fact_from_bucket(candidate_facts, "certificates", lambda item: _normalize_text(item.get("certificate_type")).lower() == "coc")


def _passport_expiry_value(candidate_facts: Mapping[str, Any]) -> Any:
    logistics = candidate_facts.get("logistics") if _is_mapping(candidate_facts.get("logistics")) else {}
    if _is_mapping(logistics) and logistics.get("passport_expiry_date") not in (None, ""):
        return logistics.get("passport_expiry_date")
    passport = _passport_document(candidate_facts)
    if _is_mapping(passport) and passport.get("expiry_date") not in (None, ""):
        return passport.get("expiry_date")
    return None


def _passport_valid_value(candidate_facts: Mapping[str, Any]) -> Any:
    logistics = candidate_facts.get("logistics") if _is_mapping(candidate_facts.get("logistics")) else {}
    if _is_mapping(logistics) and logistics.get("passport_valid") is not None:
        return bool(logistics.get("passport_valid"))
    passport_expiry = _passport_expiry_value(candidate_facts)
    expiry_date = _parse_date(passport_expiry)
    if expiry_date is None:
        return None
    return expiry_date >= _utc_today()


def _coc_field_value(candidate_facts: Mapping[str, Any], key: str) -> Any:
    coc = _coc_certificate(candidate_facts)
    if _is_mapping(coc) and coc.get(key) not in (None, ""):
        return coc.get(key)
    certifications = candidate_facts.get("certifications") if _is_mapping(candidate_facts.get("certifications")) else {}
    if _is_mapping(certifications):
        coc_legacy = certifications.get("coc")
        if _is_mapping(coc_legacy) and coc_legacy.get(key) not in (None, ""):
            return coc_legacy.get(key)
    return None


def _experience_vessel_types(candidate_facts: Mapping[str, Any]) -> list[Any]:
    derived = candidate_facts.get("experience") if _is_mapping(candidate_facts.get("experience")) else {}
    collected: list[Any] = []
    if _is_mapping(derived):
        for key in ("vessel_types", "ship_types"):
            collected.extend(_as_list(derived.get(key)))
    for contract in _as_list(candidate_facts.get("contracts")):
        if not _is_mapping(contract):
            continue
        for key in ("ship_family", "vessel_type"):
            if contract.get(key) not in (None, ""):
                collected.append(contract.get(key))
    for vessel in _as_list(candidate_facts.get("vessel_experience")):
        if _is_mapping(vessel):
            for key in ("ship_family", "vessel_type", "canonical_value"):
                if vessel.get(key) not in (None, ""):
                    collected.append(vessel.get(key))
    seen: set[str] = set()
    ordered: list[Any] = []
    for item in collected:
        text = _normalize_text(item)
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(item)
    return ordered


def _rank_experience_rows(candidate_facts: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = [row for row in _as_list(candidate_facts.get("rank_experience")) if _is_mapping(row)]
    if rows:
        return rows
    contracts = [row for row in _as_list(candidate_facts.get("contracts")) if _is_mapping(row)]
    if not contracts:
        return []
    current_rank = _rank_value(candidate_facts)
    if not current_rank:
        return []
    matching_rows: list[Mapping[str, Any]] = []
    for contract in contracts:
        contract_rank = contract.get("rank") or contract.get("display_value")
        if _normalize_text(contract_rank).lower() == _normalize_text(current_rank).lower():
            matching_rows.append(contract)
    return matching_rows


def _sorted_contracts(candidate_facts: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    contracts = [row for row in _as_list(candidate_facts.get("contracts")) if _is_mapping(row)]

    def _sort_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
        order = row.get("contract_order")
        order_value = int(order) if isinstance(order, int) else 999999
        start_value = _normalize_text(row.get("start_date"))
        return (order_value, start_value, _normalize_text(row.get("fact_id")))

    return sorted(contracts, key=_sort_key)


def _longest_same_company_run(contracts: Sequence[Mapping[str, Any]]) -> int | None:
    longest = 0
    current = 0
    previous_company = None
    seen_company = False
    for contract in contracts:
        company = _normalize_text(contract.get("company"))
        if not company:
            current = 0
            previous_company = None
            continue
        seen_company = True
        if company.lower() == previous_company:
            current += 1
        else:
            current = 1
            previous_company = company.lower()
        longest = max(longest, current)
    return longest if seen_company else None


def _contract_gap_over_six_months(contracts: Sequence[Mapping[str, Any]]) -> bool | None:
    dated_rows: list[tuple[date, date]] = []
    for contract in contracts:
        start_date = _parse_date(contract.get("start_date"))
        end_date = _parse_date(contract.get("end_date"))
        if start_date and end_date:
            dated_rows.append((start_date, end_date))
    if len(dated_rows) < 2:
        return None
    dated_rows.sort(key=lambda item: item[0])
    max_gap_days = 0
    previous_end = dated_rows[0][1]
    for start_date, end_date in dated_rows[1:]:
        gap = (start_date - previous_end).days
        max_gap_days = max(max_gap_days, gap)
        previous_end = max(previous_end, end_date)
    return max_gap_days > 183


def _current_rank_months_total(candidate_facts: Mapping[str, Any]) -> int | float | None:
    derived = candidate_facts.get("derived") if _is_mapping(candidate_facts.get("derived")) else {}
    if _is_mapping(derived) and derived.get("current_rank_months_total") is not None:
        return derived.get("current_rank_months_total")

    current_rank = _rank_value(candidate_facts)
    if not current_rank:
        return None
    total = 0.0
    matched = False
    for row in _rank_experience_rows(candidate_facts):
        row_rank = _normalize_text(row.get("rank") or row.get("canonical_value"))
        if row_rank and row_rank.lower() == _normalize_text(current_rank).lower():
            duration = row.get("duration_months")
            if isinstance(duration, (int, float)):
                total += float(duration)
                matched = True
    if matched:
        return int(total) if float(total).is_integer() else total

    for contract in _sorted_contracts(candidate_facts):
        row_rank = _normalize_text(contract.get("rank"))
        if row_rank and row_rank.lower() == _normalize_text(current_rank).lower():
            duration = contract.get("duration_months")
            if isinstance(duration, (int, float)):
                total += float(duration)
                matched = True
    if matched:
        return int(total) if float(total).is_integer() else total
    return None


def _build_row_with_status(
    *,
    candidate_facts: Mapping[str, Any],
    field_path: str,
    label: str,
    value: Any,
    evidence_ids: Sequence[str] | None = None,
    confidence: Any = None,
    status: str | None = None,
    source_label: str | None = None,
    extraction_method: str | None = None,
    source_excerpt: str | None = None,
    affects_match: bool = False,
    derived: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return _build_row(
        candidate_facts=candidate_facts,
        field_path=field_path,
        label=label,
        value=value,
        evidence_ids=evidence_ids,
        confidence=confidence,
        status=status,
        source_label=source_label,
        extraction_method=extraction_method,
        source_excerpt=source_excerpt,
        affects_match=affects_match,
        derived=derived,
        extra=extra,
    )


def build_candidate_facts_review_summary(candidate_facts: Mapping[str, Any]) -> Dict[str, Any]:
    facts = candidate_facts if _is_mapping(candidate_facts) else {}

    candidate_name_fact = _candidate_name_fact(facts)
    dob_fact = _extract_fact_meta(facts, "personal.dob") or _extract_fact_meta(facts, "identity.dob")
    passport_fact = _passport_document(facts)
    coc_fact = _coc_certificate(facts)
    contracts = _sorted_contracts(facts)
    rank_experience_rows = _rank_experience_rows(facts)
    top_level_evidence_ids = [
        str(evidence.get("evidence_id"))
        for evidence in _as_list(facts.get("evidence"))
        if _is_mapping(evidence) and _normalize_text(evidence.get("evidence_id"))
    ]

    candidate_name_value = _normalize_value_from_fact(candidate_name_fact)
    dob_value = _dob_value(facts)
    age_value = _age_years_value(facts)
    rank_value = _rank_value(facts)
    passport_expiry_value = _passport_expiry_value(facts)
    passport_valid_value = _passport_valid_value(facts)
    coc_grade_value = _coc_field_value(facts, "grade")
    coc_expiry_value = _coc_field_value(facts, "expiry_date")
    coc_status_value = _coc_field_value(facts, "status")
    vessel_types = _experience_vessel_types(facts)
    current_rank_months_total = _current_rank_months_total(facts)
    same_company_contract_count_max = _longest_same_company_run(contracts)
    has_contract_gap_over_6_months = _contract_gap_over_six_months(contracts)
    derived = facts.get("derived") if _is_mapping(facts.get("derived")) else {}
    if current_rank_months_total is None and _is_mapping(derived):
        current_rank_months_total = derived.get("current_rank_months_total")
    if same_company_contract_count_max is None and _is_mapping(derived):
        same_company_contract_count_max = derived.get("same_company_contract_count_max")
    if has_contract_gap_over_6_months is None and _is_mapping(derived):
        has_contract_gap_over_6_months = derived.get("has_contract_gap_over_6_months")

    candidate_name_evidence = _evidence_ids_for_field(candidate_name_fact, fallback_ids=top_level_evidence_ids, value=candidate_name_value)
    dob_evidence = _evidence_ids_for_field(dob_fact, fallback_ids=candidate_name_evidence or top_level_evidence_ids, value=dob_value)
    rank_fact = facts.get("rank") if _is_mapping(facts.get("rank")) else {}
    rank_evidence = _evidence_ids_for_field(rank_fact, fallback_ids=candidate_name_evidence or top_level_evidence_ids, value=rank_value)
    passport_evidence = _evidence_ids_for_field(passport_fact, fallback_ids=candidate_name_evidence or top_level_evidence_ids, value=passport_expiry_value)
    coc_evidence = _evidence_ids_for_field(coc_fact, fallback_ids=candidate_name_evidence or top_level_evidence_ids, value=coc_grade_value or coc_expiry_value or coc_status_value)
    vessel_evidence = []
    for contract in contracts:
        vessel_evidence.extend(_evidence_ids_from_fact(contract))
    if not vessel_evidence:
        vessel_evidence = _evidence_ids_for_field(None, fallback_ids=candidate_name_evidence or top_level_evidence_ids, value=vessel_types)
    if not candidate_name_evidence:
        candidate_name_evidence = []
    if not dob_evidence:
        dob_evidence = []
    if not rank_evidence:
        rank_evidence = []
    if not passport_evidence:
        passport_evidence = []
    if not coc_evidence:
        coc_evidence = []

    rows = [
        _build_row_with_status(
            candidate_facts=facts,
            field_path="identity.candidate_name",
            label="Candidate name",
            value=candidate_name_value,
            evidence_ids=candidate_name_evidence,
            confidence=(candidate_name_fact or {}).get("confidence"),
            status=None,
            source_label=(candidate_name_fact or {}).get("source_label"),
            extraction_method=(candidate_name_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(candidate_name_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="personal.dob",
            label="DOB",
            value=_format_date(dob_value),
            evidence_ids=dob_evidence,
            confidence=(dob_fact or {}).get("confidence"),
            status=None,
            source_label=(dob_fact or {}).get("source_label"),
            extraction_method=(dob_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(dob_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="derived.age_years",
            label="Age",
            value=age_value,
            evidence_ids=dob_evidence,
            confidence=(facts.get("derived") or {}).get("age_confidence") or (dob_fact or {}).get("confidence"),
            status=None if age_value is not None else "MISSING",
            source_label="summary_derived" if age_value is not None else "",
            extraction_method="derived_from_personal.dob" if age_value is not None else "",
            source_excerpt=_resolve_excerpt(_fact_snippet(dob_fact)),
            affects_match=True,
            derived=True,
            extra={"derived_from": "personal.dob"},
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="role.applied_rank_normalized",
            label="Applied rank",
            value=rank_value,
            evidence_ids=rank_evidence,
            confidence=(rank_fact or {}).get("confidence"),
            status=None,
            source_label=(rank_fact or {}).get("source_label"),
            extraction_method=(rank_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(rank_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="logistics.passport_expiry_date",
            label="Passport expiry",
            value=_format_date(passport_expiry_value),
            evidence_ids=passport_evidence,
            confidence=(passport_fact or {}).get("confidence"),
            status=None,
            source_label=(passport_fact or {}).get("source_label"),
            extraction_method=(passport_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(passport_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="logistics.passport_valid",
            label="Passport valid",
            value=passport_valid_value,
            evidence_ids=passport_evidence,
            confidence=(passport_fact or {}).get("confidence"),
            status="DERIVED" if passport_valid_value is not None else "MISSING",
            source_label="summary_derived" if passport_valid_value is not None else "",
            extraction_method="derived_from_logistics.passport_expiry_date" if passport_valid_value is not None else "",
            source_excerpt=_resolve_excerpt(_fact_snippet(passport_fact)),
            affects_match=True,
            derived=True,
            extra={"derived_from": "logistics.passport_expiry_date"},
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="certifications.coc.grade",
            label="CoC grade",
            value=coc_grade_value,
            evidence_ids=coc_evidence,
            confidence=(coc_fact or {}).get("confidence"),
            status=None,
            source_label=(coc_fact or {}).get("source_label"),
            extraction_method=(coc_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(coc_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="certifications.coc.expiry_date",
            label="CoC expiry",
            value=_format_date(coc_expiry_value),
            evidence_ids=coc_evidence,
            confidence=(coc_fact or {}).get("confidence"),
            status=None,
            source_label=(coc_fact or {}).get("source_label"),
            extraction_method=(coc_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(coc_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="certifications.coc.status",
            label="CoC status",
            value=coc_status_value,
            evidence_ids=coc_evidence,
            confidence=(coc_fact or {}).get("confidence"),
            status=None,
            source_label=(coc_fact or {}).get("source_label"),
            extraction_method=(coc_fact or {}).get("extraction_method"),
            source_excerpt=_resolve_excerpt(_fact_snippet(coc_fact)),
            affects_match=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="experience.vessel_types",
            label="Vessel types",
            value=vessel_types,
            evidence_ids=vessel_evidence,
            confidence=(facts.get("fact_meta") or {}).get("experience.vessel_types", {}).get("confidence") if _is_mapping(facts.get("fact_meta")) else (0.8 if vessel_types else None),
            status=None if vessel_types else "MISSING",
            source_label=(facts.get("fact_meta") or {}).get("experience.vessel_types", {}).get("source_label") if _is_mapping(facts.get("fact_meta")) else "summary_derived",
            extraction_method=(facts.get("fact_meta") or {}).get("experience.vessel_types", {}).get("extraction_method") if _is_mapping(facts.get("fact_meta")) else "derived_from_contracts",
            source_excerpt=_resolve_excerpt(*(row.get("snippet") for row in contracts)),
            affects_match=True,
            derived=not bool(_is_mapping(facts.get("fact_meta")) and (facts.get("fact_meta") or {}).get("experience.vessel_types")),
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="derived.current_rank_months_total",
            label="Current rank months",
            value=current_rank_months_total,
            evidence_ids=[evidence_id for row in rank_experience_rows for evidence_id in _evidence_ids_from_fact(row)],
            confidence=(facts.get("fact_meta") or {}).get("derived.current_rank_months_total", {}).get("confidence") if _is_mapping(facts.get("fact_meta")) else (0.8 if current_rank_months_total is not None else None),
            status=None if current_rank_months_total is not None else "MISSING",
            source_label=(facts.get("fact_meta") or {}).get("derived.current_rank_months_total", {}).get("source_label") if _is_mapping(facts.get("fact_meta")) else "summary_derived",
            extraction_method=(facts.get("fact_meta") or {}).get("derived.current_rank_months_total", {}).get("extraction_method") if _is_mapping(facts.get("fact_meta")) else "derived_from_rank_experience",
            source_excerpt=_resolve_excerpt(*(row.get("snippet") for row in rank_experience_rows)),
            affects_match=True,
            derived=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="derived.has_contract_gap_over_6_months",
            label="Contract gap over 6 months",
            value=has_contract_gap_over_6_months,
            evidence_ids=[evidence_id for contract in contracts for evidence_id in _evidence_ids_from_fact(contract)],
            confidence=(facts.get("fact_meta") or {}).get("derived.has_contract_gap_over_6_months", {}).get("confidence") if _is_mapping(facts.get("fact_meta")) else (0.8 if has_contract_gap_over_6_months is not None else None),
            status=None if has_contract_gap_over_6_months is not None else "MISSING",
            source_label=(facts.get("fact_meta") or {}).get("derived.has_contract_gap_over_6_months", {}).get("source_label") if _is_mapping(facts.get("fact_meta")) else "summary_derived",
            extraction_method=(facts.get("fact_meta") or {}).get("derived.has_contract_gap_over_6_months", {}).get("extraction_method") if _is_mapping(facts.get("fact_meta")) else "derived_from_contract_dates",
            source_excerpt=_resolve_excerpt(*(contract.get("snippet") for contract in contracts)),
            affects_match=True,
            derived=True,
        ),
        _build_row_with_status(
            candidate_facts=facts,
            field_path="derived.same_company_contract_count_max",
            label="Same-company contract count",
            value=same_company_contract_count_max,
            evidence_ids=[evidence_id for contract in contracts for evidence_id in _evidence_ids_from_fact(contract)],
            confidence=(facts.get("fact_meta") or {}).get("derived.same_company_contract_count_max", {}).get("confidence") if _is_mapping(facts.get("fact_meta")) else (0.8 if same_company_contract_count_max is not None else None),
            status=None if same_company_contract_count_max is not None else "MISSING",
            source_label=(facts.get("fact_meta") or {}).get("derived.same_company_contract_count_max", {}).get("source_label") if _is_mapping(facts.get("fact_meta")) else "summary_derived",
            extraction_method=(facts.get("fact_meta") or {}).get("derived.same_company_contract_count_max", {}).get("extraction_method") if _is_mapping(facts.get("fact_meta")) else "derived_from_contract_sequence",
            source_excerpt=_resolve_excerpt(*(contract.get("snippet") for contract in contracts)),
            affects_match=True,
            derived=True,
        ),
    ]

    key_fact_warning_counts = OrderedDict((level, 0) for level in ("ok", "low_confidence", "missing", "warning", "conflict"))
    for row in rows:
        key_fact_warning_counts[row["warning_level"]] = key_fact_warning_counts.get(row["warning_level"], 0) + 1

    return {
        "key_facts": rows,
        "key_fact_count": len(rows),
        "warning_counts": dict(key_fact_warning_counts),
        "missing_key_fact_count": key_fact_warning_counts.get("missing", 0),
        "low_confidence_key_fact_count": key_fact_warning_counts.get("low_confidence", 0),
        "conflict_key_fact_count": key_fact_warning_counts.get("conflict", 0),
    }
