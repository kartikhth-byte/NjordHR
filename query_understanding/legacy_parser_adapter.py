"""Adapter that wraps the existing legacy parser into query_plan.v1."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence

from .hard_filter_catalog import (
    CATALOG_VERSION,
    canonical_certificate_values,
    canonical_endorsement_values,
    legacy_applied_constraint_id,
    legacy_hard_constraint_key,
)
from .schema import normalize_query_plan_v1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonicalize_list(values: Iterable[Any]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip(" ,.-")


def _humanize_identifier(identifier: Any) -> str:
    return _normalize_text(str(identifier or "").replace("_", " "))


def _add_fragment(fragments: List[str], fragment: Any) -> None:
    normalized = _normalize_text(fragment)
    if normalized:
        fragments.append(normalized)


def _month_fragments(months: Any) -> List[str]:
    try:
        value = int(months)
    except (TypeError, ValueError):
        return []
    if value <= 0:
        return []

    fragments = [f"{value} months", f"minimum {value} months", f"at least {value} months"]
    if value % 12 == 0:
        years = value // 12
        year_label = "year" if years == 1 else "years"
        fragments.extend(
            [
                f"{years} {year_label}",
                f"{years} {year_label} old",
                f"at least {years} {year_label}",
                f"minimum {years} {year_label}",
            ]
        )
    return fragments


def _strip_phrase(prompt: str, phrase: str) -> str:
    phrase = _normalize_text(phrase)
    if not phrase:
        return prompt

    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    pattern = rf"(?<!\w){escaped}(?!\w)"
    return re.sub(pattern, " ", prompt, flags=re.IGNORECASE)


def _cleanup_semantic_residual(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    cleaned = cleaned.replace("&", " ")
    cleaned = re.sub(r"\bexperience(?:s)?\b", " ", cleaned, flags=re.IGNORECASE)
    while True:
        updated = re.sub(r"^(?:with|and|or)\b\s*", "", cleaned, flags=re.IGNORECASE)
        if updated == cleaned:
            break
        cleaned = updated
    while True:
        updated = re.sub(r"\s+(?:and|or|with)\b\s*$", "", cleaned, flags=re.IGNORECASE)
        if updated == cleaned:
            break
        cleaned = updated
    return " ".join(cleaned.split()).strip(" ,.-")


class LegacyParserAdapter:
    """Wrap the current parser without altering its behavior."""

    def __init__(self, analyzer: Any, *, catalog_version: str = CATALOG_VERSION):
        self.analyzer = analyzer
        self.catalog_version = catalog_version

    def adapt(self, user_prompt: str, rank: str | None = None, *, prompt_template_version: str = "legacy.parser.v1", prompt_id: str | None = None) -> Dict[str, Any]:
        legacy = self.analyzer._extract_job_constraints(user_prompt, rank=rank)
        return self.from_legacy_constraints(
            legacy,
            user_prompt=user_prompt,
            rank=rank,
            prompt_template_version=prompt_template_version,
            prompt_id=prompt_id,
        )

    def from_legacy_constraints(
        self,
        legacy_constraints: Mapping[str, Any],
        *,
        user_prompt: str,
        rank: str | None = None,
        prompt_template_version: str = "legacy.parser.v1",
        prompt_id: str | None = None,
    ) -> Dict[str, Any]:
        applied_constraints: List[Dict[str, Any]] = []
        unapplied_constraints: List[Dict[str, Any]] = []
        residual_text = str(user_prompt or "")
        semantic_fragments: List[str] = []
        parsing_notes = list(dict.fromkeys(legacy_constraints.get("parsing_notes") or []))
        hard_constraints = legacy_constraints.get("hard_constraints") or {}

        def _compat(key: str, applied_id: str | None) -> Dict[str, Any]:
            return {
                "legacy_hard_constraints_key": key,
                "legacy_applied_constraint_id": applied_id,
            }

        def _append_applied(family_id: str, payload: Dict[str, Any], source_text: str, legacy_key: str, applied_id: str | None):
            applied_constraints.append(
                {
                    "id": family_id,
                    "mode": "required",
                    "constraint": payload,
                    "source_text": source_text,
                    "confidence": "high",
                    "compatibility": _compat(legacy_key, applied_id),
                }
            )

        def _append_unapplied(family_id: str, reason: str, source_text: str, legacy_key: str, applied_id: str | None, mode: str = "required", suggested_handling: str | None = None):
            unapplied_constraints.append(
                {
                    "id": family_id,
                    "mode": mode,
                    "reason": reason,
                    "source_text": source_text,
                    "suggested_handling": suggested_handling or ("block_search" if mode == "required" else "semantic_with_warning"),
                    "confidence": "medium",
                }
            )

        if "age_years" in hard_constraints:
            age = hard_constraints.get("age_years") or {}
            min_age = age.get("min_age")
            max_age = age.get("max_age")
            if min_age is not None and max_age is not None:
                semantic_fragments.extend(
                    [
                        f"between {min_age} and {max_age} years old",
                        f"{min_age} and {max_age} years old",
                        f"{min_age} to {max_age} years old",
                        f"age between {min_age} and {max_age}",
                        f"aged {min_age} to {max_age}",
                    ]
                )
            elif min_age is not None:
                semantic_fragments.extend(
                    [
                        f"at least {min_age} years old",
                        f"minimum age {min_age}",
                        f"older than {min_age}",
                        f"over {min_age}",
                    ]
                )
            elif max_age is not None:
                semantic_fragments.extend(
                    [
                        f"up to {max_age} years old",
                        f"maximum age {max_age}",
                        f"younger than {max_age}",
                        f"below {max_age}",
                    ]
                )
            _append_applied(
                "age_range",
                {
                    "type": "age_range",
                    "minimum_years": age.get("min_age"),
                    "maximum_years": age.get("max_age"),
                },
                user_prompt,
                "age_years",
                "age_range",
            )

        if "rank" in hard_constraints:
            rank_payload = hard_constraints.get("rank") or {}
            ranks = rank_payload.get("applied_rank_normalized") or []
            if ranks:
                semantic_fragments.extend(_humanize_identifier(rank) for rank in ranks)
                _append_applied(
                    "rank_match",
                    {
                        "type": "rank_match",
                        "rank": ranks[0],
                    },
                    rank_payload.get("display_value") or user_prompt,
                    "rank",
                    "rank_match",
                )

        if "us_visa" in hard_constraints:
            visa = hard_constraints.get("us_visa") or {}
            if visa.get("display_value"):
                _add_fragment(semantic_fragments, visa.get("display_value"))
            else:
                _add_fragment(semantic_fragments, "valid us visa")
                if visa.get("minimum_months_remaining"):
                    semantic_fragments.extend(
                        [
                            f"us visa for at least {visa.get('minimum_months_remaining')} months",
                            f"valid us visa for at least {visa.get('minimum_months_remaining')} months",
                        ]
                    )
            if visa.get("supported", True) is False:
                _append_unapplied("us_visa", "unsupported_filter_family", visa.get("display_value") or user_prompt, "us_visa", "us_visa")
            else:
                _append_applied(
                    "us_visa",
                    {
                        "type": "us_visa",
                        "required": True,
                        "minimum_months_remaining": visa.get("minimum_months_remaining"),
                    },
                    visa.get("display_value") or user_prompt,
                    "us_visa",
                    "us_visa",
                )

        if "passport_validity" in hard_constraints:
            passport = hard_constraints.get("passport_validity") or {}
            if passport.get("display_value"):
                _add_fragment(semantic_fragments, passport.get("display_value"))
            else:
                months = passport.get("minimum_months_remaining")
                semantic_fragments.extend(
                    [
                        "valid passport",
                        "passport required",
                        "passport mandatory",
                    ]
                )
                semantic_fragments.extend(_month_fragments(months))
            _append_applied(
                "passport_validity",
                {
                    "type": "passport_validity",
                    "must_be_valid": True,
                    "minimum_months_remaining": passport.get("minimum_months_remaining"),
                },
                passport.get("display_value") or user_prompt,
                "passport_validity",
                "passport_validity",
            )

        if "certifications" in hard_constraints:
            certs = hard_constraints.get("certifications") or {}
            if certs.get("coc_required"):
                semantic_fragments.extend(
                    [
                        "valid coc",
                        "coc required",
                        "coc mandatory",
                        "must hold valid coc",
                        "certificate of competency required",
                    ]
                )
                _append_applied(
                    "coc_document_gate",
                    {
                        "type": "coc_document_gate",
                        "required": True,
                    },
                    certs.get("display_value") or user_prompt,
                    "certifications",
                    "coc_document_gate",
                )
            if certs.get("coc_valid_required"):
                # The v1 payload only needs the document gate flag.
                pass
            if certs.get("endorsements_required"):
                tokens = _canonicalize_list(certs.get("endorsements_required") or [])
                certificate_tokens = [token for token in tokens if token in canonical_certificate_values()]
                endorsement_tokens = [token for token in tokens if token in canonical_endorsement_values()]
                if certificate_tokens:
                    semantic_fragments.extend(_humanize_identifier(token) for token in certificate_tokens)
                    is_rank_certificate_expectation = bool(
                        isinstance(certs.get("endorsement_display_value"), str)
                        and certs.get("endorsement_display_value", "").startswith("standard ")
                    )
                    family_id = "rank_certificate_expectation" if is_rank_certificate_expectation else "certificate_requirement"
                    payload = {
                        "type": family_id,
                        "certificates_required": certificate_tokens,
                    }
                    if family_id == "rank_certificate_expectation":
                        payload["rank"] = rank if rank and isinstance(rank, str) else None
                        payload["endorsements_required"] = []
                    _append_applied(
                        family_id,
                        payload,
                        certs.get("endorsement_display_value") or user_prompt,
                        "certifications",
                        "stcw_endorsement",
                    )
                if endorsement_tokens:
                    semantic_fragments.extend(_humanize_identifier(token) for token in endorsement_tokens)
                    _append_applied(
                        "stcw_endorsement",
                        {
                            "type": "stcw_endorsement",
                            "endorsements_required": endorsement_tokens,
                        },
                        certs.get("endorsement_display_value") or user_prompt,
                        "certifications",
                        "stcw_endorsement",
                    )

        if "coc_grade" in hard_constraints:
            coc_grade = hard_constraints.get("coc_grade") or {}
            grades = coc_grade.get("required_grades") or []
            if grades:
                semantic_fragments.extend(_humanize_identifier(rank) for rank in grades)
                _append_applied(
                    "coc_grade_match",
                    {
                        "type": "coc_grade_match",
                        "grade": grades[0],
                    },
                    coc_grade.get("display_value") or user_prompt,
                    "coc_grade",
                    "coc_grade_match",
                )

        if "stcw_basic" in hard_constraints:
            semantic_fragments.extend(
                [
                    "stcw basic",
                    "valid stcw basic",
                    "basic stcw required",
                    "must hold all basic stcw certificates",
                ]
            )
            _append_applied(
                "stcw_basic",
                {
                    "type": "stcw_basic",
                    "required": True,
                },
                user_prompt,
                "stcw_basic",
                "stcw_basic",
            )

        if "company_continuity" in hard_constraints:
            company = hard_constraints.get("company_continuity") or {}
            semantic_fragments.extend(
                [
                    "same company",
                    "same employer",
                ]
            )
            semantic_fragments.extend(
                [
                    f"{company.get('min_same_company_contract_count')} contracts",
                    f"at least {company.get('min_same_company_contract_count')} contracts",
                    f"more than {company.get('min_same_company_contract_count') - 1} contracts" if isinstance(company.get("min_same_company_contract_count"), int) else "",
                ]
            )
            _append_applied(
                "company_continuity",
                {
                    "type": "company_continuity",
                    "minimum_contracts": company.get("min_same_company_contract_count"),
                    "same_company_required": True,
                },
                company.get("display_value") or user_prompt,
                "company_continuity",
                "company_continuity",
            )

        if "engine_vessel_experience" in hard_constraints:
            payload = hard_constraints.get("engine_vessel_experience") or {}
            semantic_fragments.extend(_humanize_identifier(payload.get("engine_type")))
            semantic_fragments.extend(_humanize_identifier(payload.get("vessel_type")))
            semantic_fragments.extend(["experience", "with experience", "engine experience", "vessel experience", "experience on", "experience in"])
            semantic_fragments.extend(_month_fragments(payload.get("min_months")))
            if payload.get("lookback_contracts"):
                semantic_fragments.extend(
                    [
                        f"last {payload.get('lookback_contracts')} contracts",
                        f"recent {payload.get('lookback_contracts')} contracts",
                    ]
                )
            _append_applied(
                "engine_vessel_experience",
                {
                    "type": "engine_vessel_experience",
                    "engine_family": payload.get("engine_type"),
                    "ship_family": payload.get("vessel_type"),
                    "minimum_months": payload.get("min_months"),
                    "recent_contract_count": payload.get("lookback_contracts") or None,
                },
                payload.get("display_value") or user_prompt,
                "engine_vessel_experience",
                "engine_vessel_experience",
            )

        if "recent_contract_vessel_experience" in hard_constraints:
            payload = hard_constraints.get("recent_contract_vessel_experience") or {}
            semantic_fragments.extend(_humanize_identifier(payload.get("vessel_type")))
            semantic_fragments.extend(["experience", "with experience", "vessel experience", "experience on", "experience in", "recent contract experience"])
            semantic_fragments.extend(_month_fragments(payload.get("min_months")))
            if payload.get("lookback_contracts"):
                semantic_fragments.extend(
                    [
                        f"last {payload.get('lookback_contracts')} contracts",
                        f"recent {payload.get('lookback_contracts')} contracts",
                    ]
                )
            _append_applied(
                "recent_contract_vessel_experience",
                {
                    "type": "recent_contract_vessel_experience",
                    "ship_family": payload.get("vessel_type"),
                    "minimum_months": payload.get("min_months"),
                    "recent_contract_count": payload.get("lookback_contracts"),
                },
                payload.get("display_value") or user_prompt,
                "recent_contract_vessel_experience",
                "recent_contract_vessel_experience",
            )

        if "engine_experience" in hard_constraints:
            payload = hard_constraints.get("engine_experience") or {}
            semantic_fragments.extend(_humanize_identifier(payload.get("engine_type")))
            semantic_fragments.extend(["experience", "with experience", "engine experience", "experience on", "experience in", "engine experience in"])
            semantic_fragments.extend(_month_fragments(payload.get("min_months")))
            if payload.get("lookback_contracts"):
                semantic_fragments.extend(
                    [
                        f"last {payload.get('lookback_contracts')} contracts",
                        f"recent {payload.get('lookback_contracts')} contracts",
                    ]
                )
            _append_applied(
                "engine_experience",
                {
                    "type": "engine_experience",
                    "engine_family": payload.get("engine_type"),
                    "minimum_months": payload.get("min_months"),
                    "recent_contract_count": payload.get("lookback_contracts") or None,
                },
                payload.get("display_value") or user_prompt,
                "engine_experience",
                "engine_experience",
            )

        if "rank_duration_experience" in hard_constraints:
            payload = hard_constraints.get("rank_duration_experience") or {}
            semantic_fragments.extend(_humanize_identifier(payload.get("rank_normalized")))
            semantic_fragments.extend(["experience", "with experience", "rank experience", "experience as", "experience in"])
            semantic_fragments.extend(_month_fragments(payload.get("min_months")))
            _append_applied(
                "rank_duration_experience",
                {
                    "type": "rank_duration_experience",
                    "rank": payload.get("rank_normalized"),
                    "minimum_months": payload.get("min_months"),
                },
                payload.get("display_value") or user_prompt,
                "rank_duration_experience",
                "rank_duration_experience",
            )

        if "experience_ship_type" in hard_constraints:
            payload = hard_constraints.get("experience_ship_type") or {}
            if isinstance(payload, list):
                semantic_fragments.extend(_humanize_identifier(item) for item in payload)
                semantic_fragments.extend(["experience", "with experience", "ship experience", "experience on", "experience in"])
            _append_applied(
                "experience_ship_type",
                {
                    "type": "experience_ship_type",
                    "ship_family": payload,
                    "minimum_months": None,
                },
                user_prompt,
                "experience_ship_type",
                "experience_ship_type",
            )

        if "recency" in hard_constraints:
            payload = hard_constraints.get("recency") or {}
            if payload.get("display_value"):
                _add_fragment(semantic_fragments, payload.get("display_value"))
            else:
                months = payload.get("max_months_since_sign_off")
                semantic_fragments.extend(
                    [
                        f"signed off in last {months} months",
                        f"signed off within {months} months",
                        f"last sign off within {months} months",
                    ]
                )
            _append_applied(
                "recency",
                {
                    "type": "recency",
                    "maximum_months_since_last_contract": payload.get("max_months_since_sign_off"),
                    "must_be_currently_sailing": None,
                },
                payload.get("display_value") or user_prompt,
                "recency",
                "recency",
            )

        if "availability" in hard_constraints:
            payload = hard_constraints.get("availability") or {}
            status = payload.get("value_type")
            if payload.get("display_value"):
                _add_fragment(semantic_fragments, payload.get("display_value"))
            if payload.get("status") == "immediately":
                availability_status = "available"
                available_by = None
            elif payload.get("value_type") == "date":
                availability_status = "available_by_date"
                available_by = payload.get("available_from_date")
            elif payload.get("value_type") == "relative_phrase":
                availability_status = "available_by_date"
                available_by = None
            else:
                availability_status = "available"
                available_by = None
            _append_applied(
                "availability",
                {
                    "type": "availability",
                    "status": availability_status,
                    "available_by": available_by,
                },
                payload.get("display_value") or user_prompt,
                "availability",
                "availability",
            )

        if "sea_service" in hard_constraints:
            payload = hard_constraints.get("sea_service") or {}
            if payload.get("display_value"):
                _add_fragment(semantic_fragments, payload.get("display_value"))
            _append_unapplied("min_sea_service", "unsupported_filter_family", payload.get("display_value") or user_prompt, "sea_service", None)

        if "vessel_type" in hard_constraints:
            payload = hard_constraints.get("vessel_type") or {}
            if payload.get("display_value"):
                _add_fragment(semantic_fragments, payload.get("display_value"))
            _append_unapplied("vessel_type", "unsupported_filter_family", payload.get("display_value") or user_prompt, "vessel_type", None)

        if parsing_notes:
            for note in parsing_notes:
                if isinstance(note, str) and note.strip():
                    residual_text = _strip_phrase(residual_text, note)

        for fragment in semantic_fragments:
            residual_text = _strip_phrase(residual_text, fragment)

        for item in applied_constraints:
            source_text = str(item.get("source_text") or "")
            if source_text and _normalize_text(source_text) != _normalize_text(user_prompt):
                residual_text = _strip_phrase(residual_text, source_text)

        residual_text = _cleanup_semantic_residual(residual_text)

        residual_text = " ".join(residual_text.split()).strip(" ,.-")
        semantic_query = residual_text if residual_text else ""

        query_plan = {
            "schema_version": "query_plan.v1",
            "normalizer": {
                "name": "legacy",
                "model": None,
                "prompt_template_version": prompt_template_version,
                "catalog_version": self.catalog_version,
                "created_at": _utc_now_iso(),
            },
            "input": {
                "raw_prompt": str(user_prompt or ""),
                "rank_context": str(rank or "").strip() or None,
                "ui_filters": {
                    "schema_version": "ui_filters.v1",
                    "filters": [],
                },
            },
            "applied_constraints": applied_constraints,
            "unapplied_constraints": unapplied_constraints,
            "semantic_query": semantic_query,
            "unrecognized_residual": [
                {"text": note, "suggested_handling": "semantic"} for note in parsing_notes if isinstance(note, str) and note.strip()
            ],
            "warnings": [],
            "validation": {"status": "invalid", "errors": []},
        }
        return normalize_query_plan_v1(query_plan, mode="production")
