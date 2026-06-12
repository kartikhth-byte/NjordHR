"""Validation for the candidate_facts.v1 contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Mapping

from .versioning import CANDIDATE_FACTS_SCHEMA_VERSION as _CANDIDATE_FACTS_SCHEMA_VERSION, is_supported_schema_version

CANDIDATE_FACTS_SCHEMA_VERSION = _CANDIDATE_FACTS_SCHEMA_VERSION


VALID_PRESENCE = {"observed_true", "observed_false", "unobserved_unknown"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_EXTRACTION_STATUS = {"complete", "partial", "failed"}
VALID_SOURCE_ORIGINS = {"seajobs_download", "email_intake", "manual_upload", "website_download", "unknown"}
VALID_DETECTED_LAYOUTS = {"seajobs", "email", "manual", "website", "unknown"}
VALID_SOURCE_KINDS = {"pdf_page", "table_cell", "raw_text_chunk", "ocr_text", "manual_entry", "external_record"}
VALID_FACT_TYPES = {
    "document",
    "certificate",
    "endorsement",
    "course",
    "contract",
    "rank_experience",
    "engine_experience",
    "vessel_experience",
}

BUCKET_FACT_TYPES = {
    "documents": "document",
    "certificates": "certificate",
    "endorsements": "endorsement",
    "courses": "course",
    "contracts": "contract",
    "rank_experience": "rank_experience",
    "engine_experience": "engine_experience",
    "vessel_experience": "vessel_experience",
}


@dataclass(frozen=True)
class CandidateFactsValidationResult:
    status: str
    errors: List[Dict[str, Any]]


def _error(path: str, code: str, message: str) -> Dict[str, Any]:
    return {"path": path, "code": code, "message": message}


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if _is_mapping(value):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _require_mapping(payload: Any, path: str, errors: List[Dict[str, Any]]) -> Mapping[str, Any] | None:
    if not _is_mapping(payload):
        errors.append(_error(path, "invalid_type", "must be an object"))
        return None
    return payload


def _require_string(value: Any, path: str, errors: List[Dict[str, Any]], *, allow_null: bool = False) -> str | None:
    if value is None and allow_null:
        return None
    if not isinstance(value, str):
        errors.append(_error(path, "invalid_type", "must be a string"))
        return None
    return value


def _require_list(value: Any, path: str, errors: List[Dict[str, Any]]) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(_error(path, "invalid_type", "must be an array"))
        return None
    return value


def _validate_enum(value: Any, path: str, errors: List[Dict[str, Any]], allowed: set[str]) -> str | None:
    value = _require_string(value, path, errors)
    if value is None:
        return None
    if value not in allowed:
        errors.append(_error(path, "invalid_value", f"must be one of: {', '.join(sorted(allowed))}"))
        return None
    return value


def _validate_source(source: Any, errors: List[Dict[str, Any]]) -> None:
    source_obj = _require_mapping(source, "source", errors)
    if source_obj is None:
        return
    for field in ("resume_id", "candidate_id", "source_origin", "detected_layout", "file_name", "content_hash"):
        if field not in source_obj:
            errors.append(_error(f"source.{field}", "missing_field", "is required"))
    _require_string(source_obj.get("resume_id"), "source.resume_id", errors)
    _require_string(source_obj.get("candidate_id"), "source.candidate_id", errors)
    _validate_enum(source_obj.get("source_origin"), "source.source_origin", errors, VALID_SOURCE_ORIGINS)
    _validate_enum(source_obj.get("detected_layout"), "source.detected_layout", errors, VALID_DETECTED_LAYOUTS)
    _require_string(source_obj.get("file_name"), "source.file_name", errors)
    _require_string(source_obj.get("content_hash"), "source.content_hash", errors)


def _validate_fact_extraction(extraction: Any, path: str, errors: List[Dict[str, Any]]) -> None:
    extraction_obj = _require_mapping(extraction, path, errors)
    if extraction_obj is None:
        return
    for field in ("extractor", "parser_version", "method"):
        if field not in extraction_obj:
            errors.append(_error(f"{path}.{field}", "missing_field", "is required"))
    _require_string(extraction_obj.get("extractor"), f"{path}.extractor", errors)
    _require_string(extraction_obj.get("parser_version"), f"{path}.parser_version", errors)
    _validate_enum(extraction_obj.get("method"), f"{path}.method", errors, {"table_parser", "regex", "ocr", "llm_extraction", "manual", "fallback"})


def _validate_vessel_tonnage_entries(value: Any, path: str, errors: List[Dict[str, Any]]) -> None:
    if value is None:
        return
    entries = _require_list(value, path, errors)
    if entries is None:
        return
    for index, item in enumerate(entries):
        item_path = f"{path}[{index}]"
        obj = _require_mapping(item, item_path, errors)
        if obj is None:
            continue
        tonnage_value = obj.get("value")
        if isinstance(tonnage_value, bool) or not isinstance(tonnage_value, int):
            errors.append(_error(f"{item_path}.value", "invalid_type", "must be integer"))
        elif tonnage_value <= 0:
            errors.append(_error(f"{item_path}.value", "invalid_value", "must be positive"))
        _validate_enum(obj.get("unit"), f"{item_path}.unit", errors, {"unspecified", "gt", "grt", "dwt"})
        _require_string(obj.get("source_label"), f"{item_path}.source_label", errors)
        confidence = obj.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append(_error(f"{item_path}.confidence", "invalid_type", "must be number"))
        elif not 0 <= confidence <= 1:
            errors.append(_error(f"{item_path}.confidence", "invalid_value", "must be between 0 and 1"))
        _require_string(obj.get("evidence_text"), f"{item_path}.evidence_text", errors)


def _validate_fact_item(item: Any, bucket: str, index: int, errors: List[Dict[str, Any]]) -> None:
    path = f"{bucket}[{index}]"
    obj = _require_mapping(item, path, errors)
    if obj is None:
        return

    for field in ("fact_id", "fact_type", "canonical_value", "display_value", "presence", "confidence", "evidence_ids", "extraction"):
        if field not in obj:
            errors.append(_error(f"{path}.{field}", "missing_field", "is required"))

    _require_string(obj.get("fact_id"), f"{path}.fact_id", errors)
    fact_type = _require_string(obj.get("fact_type"), f"{path}.fact_type", errors)
    if fact_type is not None and fact_type not in VALID_FACT_TYPES:
        errors.append(_error(f"{path}.fact_type", "invalid_value", f"must be one of: {', '.join(sorted(VALID_FACT_TYPES))}"))
    _require_string(obj.get("canonical_value"), f"{path}.canonical_value", errors, allow_null=True)
    _require_string(obj.get("display_value"), f"{path}.display_value", errors, allow_null=True)
    _validate_enum(obj.get("presence"), f"{path}.presence", errors, VALID_PRESENCE)
    _validate_enum(obj.get("confidence"), f"{path}.confidence", errors, VALID_CONFIDENCE)
    evidence_ids = _require_list(obj.get("evidence_ids"), f"{path}.evidence_ids", errors)
    if evidence_ids is not None:
        for evidence_index, evidence_id in enumerate(evidence_ids):
            _require_string(evidence_id, f"{path}.evidence_ids[{evidence_index}]", errors)
    _validate_fact_extraction(obj.get("extraction"), f"{path}.extraction", errors)

    if bucket == "documents":
        _validate_enum(obj.get("document_type"), f"{path}.document_type", errors, {"passport", "us_visa", "other"})
        if "document_number_present" in obj and obj.get("document_number_present") is not None and not isinstance(obj.get("document_number_present"), bool):
            errors.append(_error(f"{path}.document_number_present", "invalid_type", "must be boolean or null"))
        _require_string(obj.get("issue_date"), f"{path}.issue_date", errors, allow_null=True)
        _require_string(obj.get("expiry_date"), f"{path}.expiry_date", errors, allow_null=True)
        _require_string(obj.get("country"), f"{path}.country", errors, allow_null=True)
        _require_string(obj.get("issue_authority"), f"{path}.issue_authority", errors, allow_null=True)
        _require_string(obj.get("certificate_type_raw"), f"{path}.certificate_type_raw", errors, allow_null=True)
    elif bucket == "certificates":
        _require_string(obj.get("certificate_type"), f"{path}.certificate_type", errors)
        if "certificate_number_present" in obj and obj.get("certificate_number_present") is not None and not isinstance(obj.get("certificate_number_present"), bool):
            errors.append(_error(f"{path}.certificate_number_present", "invalid_type", "must be boolean or null"))
        _require_string(obj.get("issue_date"), f"{path}.issue_date", errors, allow_null=True)
        _require_string(obj.get("expiry_date"), f"{path}.expiry_date", errors, allow_null=True)
        _require_string(obj.get("country"), f"{path}.country", errors, allow_null=True)
    elif bucket == "endorsements":
        _require_string(obj.get("endorsement_type"), f"{path}.endorsement_type", errors)
        if obj.get("level") is not None:
            _validate_enum(obj.get("level"), f"{path}.level", errors, {"basic", "advanced", "unknown"})
        _require_string(obj.get("issue_date"), f"{path}.issue_date", errors, allow_null=True)
        _require_string(obj.get("expiry_date"), f"{path}.expiry_date", errors, allow_null=True)
    elif bucket == "courses":
        _require_string(obj.get("course_type"), f"{path}.course_type", errors)
        _require_string(obj.get("issue_date"), f"{path}.issue_date", errors, allow_null=True)
        _require_string(obj.get("expiry_date"), f"{path}.expiry_date", errors, allow_null=True)
    elif bucket == "contracts":
        if "contract_order" in obj and obj.get("contract_order") is not None and not isinstance(obj.get("contract_order"), int):
            errors.append(_error(f"{path}.contract_order", "invalid_type", "must be integer or null"))
        _require_string(obj.get("rank"), f"{path}.rank", errors, allow_null=True)
        _require_string(obj.get("vessel_name"), f"{path}.vessel_name", errors, allow_null=True)
        _require_string(obj.get("vessel_type"), f"{path}.vessel_type", errors, allow_null=True)
        _require_string(obj.get("ship_family"), f"{path}.ship_family", errors, allow_null=True)
        _validate_vessel_tonnage_entries(obj.get("vessel_tonnage"), f"{path}.vessel_tonnage", errors)
        _require_string(obj.get("engine_family"), f"{path}.engine_family", errors, allow_null=True)
        _require_string(obj.get("company"), f"{path}.company", errors, allow_null=True)
        _require_string(obj.get("start_date"), f"{path}.start_date", errors, allow_null=True)
        _require_string(obj.get("end_date"), f"{path}.end_date", errors, allow_null=True)
        if "duration_months" in obj and obj.get("duration_months") is not None and not isinstance(obj.get("duration_months"), (int, float)):
            errors.append(_error(f"{path}.duration_months", "invalid_type", "must be number or null"))
        if "is_current_contract" in obj and obj.get("is_current_contract") is not None and not isinstance(obj.get("is_current_contract"), bool):
            errors.append(_error(f"{path}.is_current_contract", "invalid_type", "must be boolean or null"))
    elif bucket == "rank_experience":
        _require_string(obj.get("rank"), f"{path}.rank", errors)
        if "duration_months" in obj and obj.get("duration_months") is not None and not isinstance(obj.get("duration_months"), (int, float)):
            errors.append(_error(f"{path}.duration_months", "invalid_type", "must be number or null"))
        if obj.get("source") is not None:
            _validate_enum(obj.get("source"), f"{path}.source", errors, {"contracts", "total_experience_table", "derived", "unknown"})
    elif bucket == "engine_experience":
        _require_string(obj.get("engine_family"), f"{path}.engine_family", errors)
        if "duration_months" in obj and obj.get("duration_months") is not None and not isinstance(obj.get("duration_months"), (int, float)):
            errors.append(_error(f"{path}.duration_months", "invalid_type", "must be number or null"))
        contract_ids = obj.get("contract_ids")
        if contract_ids is not None:
            ids = _require_list(contract_ids, f"{path}.contract_ids", errors)
            if ids is not None:
                for idx, contract_id in enumerate(ids):
                    _require_string(contract_id, f"{path}.contract_ids[{idx}]", errors)
    elif bucket == "vessel_experience":
        _require_string(obj.get("ship_family"), f"{path}.ship_family", errors)
        if "duration_months" in obj and obj.get("duration_months") is not None and not isinstance(obj.get("duration_months"), (int, float)):
            errors.append(_error(f"{path}.duration_months", "invalid_type", "must be number or null"))
        contract_ids = obj.get("contract_ids")
        if contract_ids is not None:
            ids = _require_list(contract_ids, f"{path}.contract_ids", errors)
            if ids is not None:
                for idx, contract_id in enumerate(ids):
                    _require_string(contract_id, f"{path}.contract_ids[{idx}]", errors)


def _validate_evidence_item(item: Any, index: int, errors: List[Dict[str, Any]]) -> None:
    path = f"evidence[{index}]"
    obj = _require_mapping(item, path, errors)
    if obj is None:
        return
    for field in ("evidence_id", "source_kind", "source_id"):
        if field not in obj:
            errors.append(_error(f"{path}.{field}", "missing_field", "is required"))
    _require_string(obj.get("evidence_id"), f"{path}.evidence_id", errors)
    _validate_enum(obj.get("source_kind"), f"{path}.source_kind", errors, VALID_SOURCE_KINDS)
    _require_string(obj.get("source_id"), f"{path}.source_id", errors)


def _validate_extraction_container(container: Any, errors: List[Dict[str, Any]]) -> None:
    extraction_obj = _require_mapping(container, "extraction", errors)
    if extraction_obj is None:
        return
    for field in ("parser_version", "status", "provenance"):
        if field not in extraction_obj:
            errors.append(_error(f"extraction.{field}", "missing_field", "is required"))
    _require_string(extraction_obj.get("parser_version"), "extraction.parser_version", errors)
    _validate_enum(extraction_obj.get("status"), "extraction.status", errors, VALID_EXTRACTION_STATUS)
    provenance = _require_mapping(extraction_obj.get("provenance"), "extraction.provenance", errors)
    if provenance is not None:
        for field in ("mode", "fallback_reason"):
            if field not in provenance:
                continue
        if provenance.get("mode") is not None:
            _validate_enum(provenance.get("mode"), "extraction.provenance.mode", errors, {"persisted", "transient_fallback", "raw_text_fallback", "semantic_chunk"})
        _require_string(provenance.get("raw_text_version"), "extraction.provenance.raw_text_version", errors, allow_null=True)
        _require_string(provenance.get("chunk_index_version"), "extraction.provenance.chunk_index_version", errors, allow_null=True)
        _require_string(provenance.get("fallback_reason"), "extraction.provenance.fallback_reason", errors, allow_null=True)
    minimums_satisfied = extraction_obj.get("minimums_satisfied")
    minimums_missing = extraction_obj.get("minimums_missing")
    if minimums_satisfied is not None:
        sats = _require_list(minimums_satisfied, "extraction.minimums_satisfied", errors)
        if sats is not None:
            for idx, family_id in enumerate(sats):
                _require_string(family_id, f"extraction.minimums_satisfied[{idx}]", errors)
    if minimums_missing is not None:
        miss = _require_list(minimums_missing, "extraction.minimums_missing", errors)
        if miss is not None:
            for idx, family_id in enumerate(miss):
                _require_string(family_id, f"extraction.minimums_missing[{idx}]", errors)


def validate_candidate_facts_v1(payload: Mapping[str, Any] | Any) -> CandidateFactsValidationResult:
    errors: List[Dict[str, Any]] = []
    obj = _require_mapping(payload, "candidate_facts", errors)
    if obj is None:
        return CandidateFactsValidationResult(status="invalid", errors=errors)

    if not is_supported_schema_version(obj.get("schema_version")):
        errors.append(_error("schema_version", "unsupported_version", f"must be {CANDIDATE_FACTS_SCHEMA_VERSION}"))

    for field in ("schema_version", "source", "extraction"):
        if field not in obj:
            errors.append(_error(field, "missing_field", "is required"))

    _validate_source(obj.get("source"), errors)
    _validate_extraction_container(obj.get("extraction"), errors)

    for bucket, expected_fact_type in BUCKET_FACT_TYPES.items():
        items = obj.get(bucket)
        if items is None:
            continue
        bucket_items = _require_list(items, bucket, errors)
        if bucket_items is None:
            continue
        for index, item in enumerate(bucket_items):
            _validate_fact_item(item, bucket, index, errors)
            if _is_mapping(item) and item.get("fact_type") not in {None, expected_fact_type}:
                errors.append(_error(f"{bucket}[{index}].fact_type", "invalid_value", f"must be {expected_fact_type}"))

    extraction_obj = obj.get("extraction") if _is_mapping(obj.get("extraction")) else {}
    source_obj = obj.get("source") if _is_mapping(obj.get("source")) else {}
    source_origin = source_obj.get("source_origin")
    detected_layout = source_obj.get("detected_layout")
    extraction_status = extraction_obj.get("status")
    if source_origin == "unknown" or detected_layout == "unknown":
        if extraction_status not in {"partial", "failed"}:
            errors.append(_error("extraction.status", "invalid_value", "unknown sources must be partial or failed"))

    status = "valid" if not errors else "invalid"
    if status == "valid" and extraction_status in {"partial", "failed"}:
        status = "degraded"
    return CandidateFactsValidationResult(status=status, errors=errors)


def normalize_candidate_facts_v1(payload: Mapping[str, Any] | Any) -> Dict[str, Any]:
    normalized = _json_safe_value(deepcopy(payload)) if _is_mapping(payload) else {"schema_version": CANDIDATE_FACTS_SCHEMA_VERSION}
    result = validate_candidate_facts_v1(normalized)
    normalized["validation"] = {"status": result.status, "errors": result.errors}
    normalized["schema_version"] = CANDIDATE_FACTS_SCHEMA_VERSION
    return normalized
