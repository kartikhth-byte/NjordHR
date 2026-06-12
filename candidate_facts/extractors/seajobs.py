"""SeaJobs candidate-facts extractor compatibility wrapper."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..schema import CANDIDATE_FACTS_SCHEMA_VERSION, normalize_candidate_facts_v1

SOURCE_NAME = "seajobs"
SOURCE_ORIGIN = "seajobs_download"
DETECTED_LAYOUT = "seajobs"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_evidence(source_id: str, source_kind: str = "raw_text_chunk") -> Dict[str, Any]:
    return {
        "evidence_id": f"ev-{_stable_hash(source_id)[:12]}",
        "source_kind": source_kind,
        "source_id": source_id,
    }


def _presence_for_value(value: Any) -> str:
    return "observed_true" if value not in (None, "", [], {}) else "unobserved_unknown"


def _compact_excerpt(value: str, *, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:|-")
    return shortened or text[:max_chars]


_STOP_LABEL_PATTERNS = (
    r"\bapplied for rank\b",
    r"\bpresent rank\b",
    r"\bname\b",
    r"\bemail address\b",
    r"\bpassport details\b",
    r"\bpassport expiry date\b",
    r"\bdate of birth\b",
    r"\bdob\b",
    r"\bcoc grade\b",
    r"\bcoc expiry date\b",
    r"\bstcw\b",
    r"\bmobile no\b",
    r"\bphone no\b",
    r"\bvessel name\b",
    r"\bvessel type\b",
    r"\bcompany\b",
    r"\bfrom date\b",
    r"\btill date\b",
    r"\bnationality\b",
    r"\bgender\b",
    r"\baddress\b",
    r"\bcity\b",
    r"\bcountry\b",
    r"\bzipcode\b",
)


def _source_excerpt_from_text(source_text: str, needle: str | None = None, *, max_chars: int = 120) -> str | None:
    text = str(source_text or "").strip()
    if not text:
        return None

    lines = [" ".join(str(line or "").split()) for line in text.splitlines() if str(line or "").strip()]
    if needle:
        normalized_needle = " ".join(str(needle or "").split()).strip()
        if normalized_needle:
            needle_lower = normalized_needle.lower()
            needle_folded = re.sub(r"\s+", "", normalized_needle).lower()
            for line in lines:
                line_lower = line.lower()
                if needle_lower in line_lower or needle_folded in re.sub(r"\s+", "", line_lower):
                    fragment = line
                    if normalized_needle:
                        match = re.search(re.escape(normalized_needle), line, flags=re.IGNORECASE)
                        if match:
                            stop_match = None
                            for pattern in _STOP_LABEL_PATTERNS:
                                candidate = re.search(pattern, line[match.end():], flags=re.IGNORECASE)
                                if candidate and (stop_match is None or candidate.start() < stop_match.start()):
                                    stop_match = candidate
                            if stop_match:
                                fragment = line[: match.end() + stop_match.start()].strip()
                    return _compact_excerpt(fragment, max_chars=max_chars)
            return None

    if lines:
        return _compact_excerpt(lines[0], max_chars=max_chars)
    return None


def _common_fact(
    *,
    fact_id: str,
    fact_type: str,
    canonical_value: Any,
    display_value: Any,
    evidence_ids: List[str],
    confidence: str = "medium",
    extraction_method: str = "fallback",
    source_label: str = "seajobs_legacy_bridge",
    snippet: str | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    fact: Dict[str, Any] = {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "canonical_value": canonical_value,
        "display_value": display_value,
        "presence": _presence_for_value(canonical_value),
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "extraction": {
            "extractor": SOURCE_NAME,
            "parser_version": "legacy_bridge.v1",
            "method": extraction_method,
            "source_origin": SOURCE_ORIGIN,
            "detected_layout": DETECTED_LAYOUT,
        },
        "source_label": source_label,
    }
    if snippet:
        fact["snippet"] = snippet
    fact.update(extra)
    return fact


def _build_source_identity(legacy_facts: Mapping[str, Any], filename: str, source_text: str) -> Dict[str, Any]:
    candidate_id = str(legacy_facts.get("candidate_id") or filename)
    content_hash = _stable_hash(f"{filename}|{source_text}|{candidate_id}")
    return {
        "resume_id": candidate_id,
        "candidate_id": candidate_id,
        "source_origin": SOURCE_ORIGIN,
        "detected_layout": DETECTED_LAYOUT,
        "file_name": filename,
        "content_hash": content_hash,
    }


def _build_documents(legacy_facts: Mapping[str, Any], evidence_ids: List[str], source_text: str = "") -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []
    logistics = legacy_facts.get("logistics") or {}

    passport_expiry = logistics.get("passport_expiry_date")
    if passport_expiry or logistics.get("passport_valid") is not None:
        documents.append(
            _common_fact(
                fact_id="passport",
                fact_type="document",
                canonical_value="passport",
                display_value="Passport",
                evidence_ids=evidence_ids,
                confidence="high" if passport_expiry else "medium",
                passport_expiry_date=passport_expiry,
                document_type="passport",
                document_number_present=None,
                issue_date=None,
                expiry_date=passport_expiry,
                country=None,
                snippet=(
                    _source_excerpt_from_text(source_text, "passport expiry date")
                    or _source_excerpt_from_text(source_text, "passport no")
                    or _source_excerpt_from_text(source_text, "passport details")
                    or _source_excerpt_from_text(source_text, "passport")
                ),
            )
        )

    visa_status = str(logistics.get("us_visa_status") or "").strip().upper()
    visa_expiry = logistics.get("us_visa_expiry_date")
    if visa_expiry or visa_status:
        documents.append(
            _common_fact(
                fact_id="us_visa",
                fact_type="document",
                canonical_value="us_visa",
                display_value="US Visa",
                evidence_ids=evidence_ids,
                confidence="high" if visa_expiry else "medium",
                document_type="us_visa",
                document_number_present=None,
                issue_date=None,
                expiry_date=visa_expiry,
                country="US",
                status=visa_status or None,
                snippet=(
                    _source_excerpt_from_text(source_text, "visa expiry date")
                    or _source_excerpt_from_text(source_text, "visa status")
                    or _source_excerpt_from_text(source_text, "visa")
                ),
            )
        )

    return documents


def _build_certificates(legacy_facts: Mapping[str, Any], evidence_ids: List[str], source_text: str = "") -> List[Dict[str, Any]]:
    certificates: List[Dict[str, Any]] = []
    certs = legacy_facts.get("certifications") or {}
    coc = certs.get("coc") or {}
    if coc.get("status") or coc.get("grade") or coc.get("expiry_date"):
        certificates.append(
            _common_fact(
                fact_id="coc",
                fact_type="certificate",
                canonical_value=coc.get("grade") or "coc",
                display_value=coc.get("grade") or "CoC",
                evidence_ids=evidence_ids,
                confidence="high" if coc.get("grade") else "medium",
                certificate_type="coc",
                certificate_number_present=None,
                issue_date=None,
                expiry_date=coc.get("expiry_date"),
                country=coc.get("country"),
                issue_authority=coc.get("issue_authority"),
                certificate_type_raw=coc.get("certificate_type"),
                grade=coc.get("grade"),
                status=coc.get("status"),
                snippet=(
                    _source_excerpt_from_text(source_text, "coc grade")
                    or _source_excerpt_from_text(source_text, "coc expiry date")
                    or _source_excerpt_from_text(source_text, "coc")
                ),
            )
        )

    stcw_basic = certs.get("stcw_basic_all_valid")
    if stcw_basic is not None:
        certificates.append(
            _common_fact(
                fact_id="stcw_basic",
                fact_type="certificate",
                canonical_value="stcw_basic_all_valid" if stcw_basic else None,
                display_value="STCW Basic",
                evidence_ids=evidence_ids,
                confidence="high",
                certificate_type="stcw_basic",
                certificate_number_present=None,
                issue_date=None,
                expiry_date=None,
                all_valid=stcw_basic,
                snippet=_source_excerpt_from_text(source_text, "stcw"),
            )
        )
    return certificates


def _build_endorsements(legacy_facts: Mapping[str, Any], evidence_ids: List[str], source_text: str = "") -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    endorsements = legacy_facts.get("certifications", {}).get("endorsements") or {}
    for name, value in endorsements.items():
        if value in (None, "unknown"):
            continue
        items.append(
            _common_fact(
                fact_id=f"endorsement:{name}",
                fact_type="endorsement",
                canonical_value=name,
                display_value=name.replace("_", " ").title(),
                evidence_ids=evidence_ids,
                confidence="high",
                endorsement_type=name,
                level=value if value in {"basic", "advanced"} else "unknown",
                issue_date=None,
                expiry_date=None,
                    snippet=_source_excerpt_from_text(source_text, name),
                )
            )
    return items


def _build_contracts(legacy_facts: Mapping[str, Any], evidence_ids: List[str], source_text: str = "") -> List[Dict[str, Any]]:
    rows = (legacy_facts.get("experience") or {}).get("service_rows") or []
    contracts: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        contracts.append(
            _common_fact(
                fact_id=f"contract-{index}",
                fact_type="contract",
                canonical_value=row.get("vessel_name") or row.get("rank_normalized") or f"contract-{index}",
                display_value=row.get("vessel_name") or row.get("rank_raw") or f"Contract {index}",
                evidence_ids=evidence_ids,
                confidence="medium",
                extraction_method="fallback",
                contract_order=index,
                rank=row.get("rank_normalized"),
                vessel_name=row.get("vessel_name"),
                vessel_type=row.get("vessel_type"),
                ship_family=row.get("ship_family"),
                vessel_tonnage=row.get("vessel_tonnage") or [],
                engine_family=row.get("engine_family"),
                engine_types=row.get("engine_types") or [],
                engine_details=row.get("engine_details") or [],
                company=row.get("company"),
                start_date=row.get("start_date"),
                end_date=row.get("end_date"),
                duration_months=row.get("months_total"),
                is_current_contract=row.get("is_current_contract"),
                snippet=row.get("snippet") or _source_excerpt_from_text(
                    source_text,
                    row.get("vessel_name") or row.get("company") or row.get("rank_raw") or row.get("rank_normalized") or row.get("vessel_type"),
                ),
            )
        )
    return contracts


def _collect_vessel_tonnage_values(legacy_facts: Mapping[str, Any]) -> List[int]:
    values: List[int] = []
    for row in (legacy_facts.get("experience") or {}).get("service_rows") or []:
        if not isinstance(row, Mapping):
            continue
        for entry in row.get("vessel_tonnage") or []:
            if not isinstance(entry, Mapping):
                continue
            value = entry.get("value")
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                values.append(value)
    return values


def _build_rank_experience(legacy_facts: Mapping[str, Any], evidence_ids: List[str], source_text: str = "") -> List[Dict[str, Any]]:
    rows = (legacy_facts.get("experience") or {}).get("rank_duration_rows") or []
    experience: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("rank_normalized") is None:
            continue
        experience.append(
            {
                "fact_id": f"rank-exp-{row.get('rank_normalized')}",
                "fact_type": "rank_experience",
                "canonical_value": row.get("rank_normalized"),
                "display_value": row.get("rank_normalized").replace("_", " "),
                "presence": _presence_for_value(row.get("months_total")),
                "confidence": "medium",
                "evidence_ids": evidence_ids,
                "extraction": {
                    "extractor": SOURCE_NAME,
                    "parser_version": "legacy_bridge.v1",
                    "method": "fallback",
                    "source_origin": SOURCE_ORIGIN,
                    "detected_layout": DETECTED_LAYOUT,
                },
                "rank": row.get("rank_normalized"),
                "duration_months": row.get("months_total"),
                "source": "contracts",
                "snippet": row.get("snippet") or _source_excerpt_from_text(
                    source_text,
                    row.get("rank_raw") or row.get("rank_normalized"),
                ),
            }
        )
    return experience


def _has_meaningful_legacy_facts(legacy_facts: Mapping[str, Any]) -> bool:
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return any(_has_meaningful_value(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(_has_meaningful_value(item) for item in value)
        return True

    return any(
        _has_meaningful_value(legacy_facts.get(section))
        for section in (
            "identity",
            "role",
            "personal",
            "certifications",
            "logistics",
            "experience",
            "application",
            "derived",
        )
    )


def build_candidate_facts_v1(
    analyzer: Any,
    filename: str,
    rank: str,
    chunks: Any,
    *,
    original_path: str | None = None,
    text_cache: Mapping[str, str] | None = None,
    folder_metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    legacy_facts = analyzer._build_candidate_facts(
        filename,
        rank,
        chunks,
        original_path=original_path,
        text_cache=text_cache,
        folder_metadata=folder_metadata,
    )
    source_text = ""
    if original_path and text_cache is not None:
        source_text = str(text_cache.get(str(original_path), "") or "")
    if not source_text:
        source_text = "\n".join(str((chunk.get("metadata") or {}).get("raw_text", "")) for chunk in (chunks or []))
    evidence = [_make_evidence(str(original_path or filename))]
    evidence_ids = [item["evidence_id"] for item in evidence]
    vessel_tonnage_values = _collect_vessel_tonnage_values(legacy_facts)

    source = _build_source_identity(legacy_facts, filename, source_text)
    candidate_facts = {
        "schema_version": CANDIDATE_FACTS_SCHEMA_VERSION,
        "source": source,
        "identity": {
            "candidate_name": {
                "value": (legacy_facts.get("identity") or {}).get("full_name"),
                "presence": _presence_for_value((legacy_facts.get("identity") or {}).get("full_name")),
                "confidence": "low",
                "evidence_ids": evidence_ids,
                "snippet": (
                    (legacy_facts.get("identity") or {}).get("full_name_snippet")
                    or _source_excerpt_from_text(source_text, (legacy_facts.get("identity") or {}).get("full_name"))
                ),
            },
            "dob": {
                "value": (legacy_facts.get("personal") or {}).get("dob"),
                "presence": _presence_for_value((legacy_facts.get("personal") or {}).get("dob")),
                "confidence": "high" if (legacy_facts.get("personal") or {}).get("dob") else "low",
                "evidence_ids": evidence_ids,
                "extraction": {
                    "extractor": SOURCE_NAME,
                    "parser_version": "legacy_bridge.v1",
                    "method": "fallback",
                },
            },
        },
        "experience": {
            "vessel_types": (legacy_facts.get("experience") or {}).get("vessel_types") or [],
            "engine_types": (legacy_facts.get("experience") or {}).get("engine_types") or [],
            "engine_details": (legacy_facts.get("experience") or {}).get("engine_details") or [],
            "vessel_tonnage_values": vessel_tonnage_values,
            "max_vessel_tonnage": max(vessel_tonnage_values) if vessel_tonnage_values else None,
            "min_vessel_tonnage": min(vessel_tonnage_values) if vessel_tonnage_values else None,
            "last_sign_off_date": (legacy_facts.get("experience") or {}).get("last_sign_off_date"),
            "last_sign_off_months_ago": (legacy_facts.get("experience") or {}).get("last_sign_off_months_ago"),
            "service_rows": (legacy_facts.get("experience") or {}).get("service_rows") or _build_contracts(legacy_facts, evidence_ids, source_text=source_text),
            "rank_duration_rows": (legacy_facts.get("experience") or {}).get("rank_duration_rows") or _build_rank_experience(legacy_facts, evidence_ids, source_text=source_text),
        },
        "rank": {
            "value": (legacy_facts.get("role") or {}).get("applied_rank_normalized"),
            "presence": _presence_for_value((legacy_facts.get("role") or {}).get("applied_rank_normalized")),
            "confidence": "high" if (legacy_facts.get("role") or {}).get("applied_rank_normalized") else "low",
            "evidence_ids": evidence_ids,
        },
        "documents": _build_documents(legacy_facts, evidence_ids, source_text=source_text),
        "certificates": _build_certificates(legacy_facts, evidence_ids, source_text=source_text),
        "endorsements": _build_endorsements(legacy_facts, evidence_ids, source_text=source_text),
        "courses": [],
        "contracts": _build_contracts(legacy_facts, evidence_ids, source_text=source_text),
        "rank_experience": _build_rank_experience(legacy_facts, evidence_ids, source_text=source_text),
        "engine_experience": [],
        "vessel_experience": [],
        "application": {
            "applied_ship_types": (legacy_facts.get("application") or {}).get("applied_ship_types") or [],
        },
        "derived": {
            "age_years": (legacy_facts.get("derived") or {}).get("age_years"),
            "current_rank_months_total": (legacy_facts.get("derived") or {}).get("current_rank_months_total"),
            "same_company_contract_count_max": (legacy_facts.get("derived") or {}).get("same_company_contract_count_max"),
            "has_contract_gap_over_6_months": (legacy_facts.get("derived") or {}).get("has_contract_gap_over_6_months"),
        },
        "evidence": evidence,
        "extraction": {
            "parser_version": "legacy_bridge.v1",
            "status": "complete" if source_text and _has_meaningful_legacy_facts(legacy_facts) else "partial",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "semantic_chunk" if source_text else "raw_text_fallback",
                "raw_text_version": "v1" if source_text else None,
                "chunk_index_version": "v1" if chunks else None,
                "fallback_reason": None,
            },
            "warnings": [],
        },
    }

    normalized = normalize_candidate_facts_v1(candidate_facts)
    normalized.setdefault("extraction", {}).setdefault("warnings", [])
    normalized["extraction"]["warnings"].append("legacy_bridge_seajobs_candidate_facts")
    return normalized


def extract_candidate_facts(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    if not args:
        raise TypeError("extract_candidate_facts requires an analyzer as the first argument")
    analyzer = args[0]
    remaining = args[1:]
    if len(remaining) < 3:
        raise TypeError("extract_candidate_facts requires filename, rank, and chunks after the analyzer")
    filename, rank, chunks = remaining[:3]
    extra_args = remaining[3:]
    if extra_args:
        raise TypeError("unexpected positional arguments for extract_candidate_facts")
    return build_candidate_facts_v1(
        analyzer,
        str(filename),
        str(rank),
        chunks,
        original_path=kwargs.get("original_path"),
        text_cache=kwargs.get("text_cache"),
        folder_metadata=kwargs.get("folder_metadata"),
    )
