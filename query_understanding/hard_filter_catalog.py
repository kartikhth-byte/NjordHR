"""Supported hard-filter catalogue for query-plan normalization.

This module keeps the family map and canonical value vocabularies separate from
the validator so the schema layer can stay small and deterministic.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Mapping

CATALOG_VERSION = "query_understanding.catalog.v1"

ACTIVE = "active"
UNSUPPORTED = "unsupported"

ACTIVE_FAMILY_IDS = (
    "age_range",
    "rank_match",
    "coc_document_gate",
    "coc_country_match",
    "coc_grade_match",
    "stcw_basic",
    "us_visa",
    "passport_validity",
    "recent_contract_vessel_experience",
    "engine_experience",
    "engine_vessel_experience",
    "company_continuity",
    "recency",
    "rank_duration_experience",
    "stcw_endorsement",
    "rank_certificate_expectation",
    "certificate_requirement",
    "experience_ship_type",
    "availability",
)

UNAPPLIED_FAMILY_IDS = (
    "min_sea_service",
    "vessel_type",
)

SUPPORTED_FAMILY_IDS = ACTIVE_FAMILY_IDS + UNAPPLIED_FAMILY_IDS

FAMILY_CATALOG: Dict[str, Dict[str, object]] = {
    "age_range": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "age_years",
        "legacy_applied_constraint_id": "age_range",
    },
    "rank_match": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "rank",
        "legacy_applied_constraint_id": "rank_match",
    },
    "coc_document_gate": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "certifications",
        "legacy_applied_constraint_id": "coc_document_gate",
    },
    "coc_country_match": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "coc_country",
        "legacy_applied_constraint_id": "coc_country_match",
    },
    "coc_grade_match": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "coc_grade",
        "legacy_applied_constraint_id": "coc_grade_match",
    },
    "stcw_basic": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "stcw_basic",
        "legacy_applied_constraint_id": "stcw_basic",
    },
    "us_visa": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "us_visa",
        "legacy_applied_constraint_id": "us_visa",
    },
    "passport_validity": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "passport_validity",
        "legacy_applied_constraint_id": "passport_validity",
    },
    "recent_contract_vessel_experience": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "recent_contract_vessel_experience",
        "legacy_applied_constraint_id": "recent_contract_vessel_experience",
    },
    "engine_experience": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "engine_experience",
        "legacy_applied_constraint_id": "engine_experience",
    },
    "engine_vessel_experience": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "engine_vessel_experience",
        "legacy_applied_constraint_id": "engine_vessel_experience",
    },
    "company_continuity": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "company_continuity",
        "legacy_applied_constraint_id": "company_continuity",
    },
    "recency": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "recency",
        "legacy_applied_constraint_id": "recency",
    },
    "rank_duration_experience": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "rank_duration_experience",
        "legacy_applied_constraint_id": "rank_duration_experience",
    },
    "stcw_endorsement": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "certifications",
        "legacy_applied_constraint_id": "stcw_endorsement",
    },
    "rank_certificate_expectation": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "certifications",
        "legacy_applied_constraint_id": "stcw_endorsement",
    },
    "certificate_requirement": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "certifications",
        "legacy_applied_constraint_id": "stcw_endorsement",
    },
    "experience_ship_type": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "experience_ship_type",
        "legacy_applied_constraint_id": "experience_ship_type",
    },
    "availability": {
        "status": ACTIVE,
        "legacy_hard_constraints_key": "availability",
        "legacy_applied_constraint_id": "availability",
    },
    "min_sea_service": {
        "status": UNSUPPORTED,
        "legacy_hard_constraints_key": "sea_service",
        "legacy_applied_constraint_id": None,
    },
    "vessel_type": {
        "status": UNSUPPORTED,
        "legacy_hard_constraints_key": "vessel_type",
        "legacy_applied_constraint_id": None,
    },
}

UI_FILTER_CATALOG: Dict[str, str] = {
    "applied_ship_type": "canonical_ship_type",
    "experienced_ship_type": "canonical_ship_type",
    "rank": "canonical_rank",
}

CANONICAL_RANKS: FrozenSet[str] = frozenset(
    {
        "master",
        "chief_officer",
        "2nd_officer",
        "3rd_officer",
        "chief_engineer",
        "2nd_engineer",
        "3rd_engineer",
        "4th_engineer",
        "deck_cadet",
        "engine_cadet",
        "junior_engineer",
        "electrical_officer",
        "electro_technical_officer",
        "general_purpose_rating",
        "bosun",
        "os",
        "ab",
        "wiper",
        "pumpman",
        "fitter",
        "chief_cook",
        "oiler",
    }
)

CANONICAL_SHIP_FAMILIES: FrozenSet[str] = frozenset(
    {
        "tanker",
        "bulk carrier",
        "container",
        "offshore",
        "lng",
        "lpg",
        "ro-ro",
        "car carrier",
    }
)

CANONICAL_ENGINE_FAMILIES: FrozenSet[str] = frozenset(
    {
        "man_b_w_mc",
        "man_b_w_me",
        "man_b_w_me_gi",
        "man_b_w_me_ga",
        "man_b_w_me_lgi",
        "man_b_w_me_lgim",
        "man_b_w_me_lgip",
        "man_b_w_me_lgia",
        "man_b_w_me_gie",
        "wingd_x_df",
        "wingd_x_df_m",
        "wingd_x_df_a",
        "wingd_x_df_p",
        "wingd_x_df_e",
        "wingd_x_engines",
        "wartsila_rta",
        "wartsila_dual_fuel",
        "wartsila_rt_flex",
        "mitsubishi_uec",
        "electronically_controlled_engine",
        "dual_fuel",
        "methanol_engine",
        "ammonia_engine",
    }
)

CANONICAL_CERTIFICATES: FrozenSet[str] = frozenset(
    {
        "gmdss",
        "cert_arpa",
        "cert_brm_btm",
        "cert_erm",
        "cert_pscrb",
        "cert_aff",
        "cert_mfa",
        "cert_medical_care",
        "cert_sso",
        "cert_ecdis",
        "cert_ccm",
        "cert_lms",
    }
)

CANONICAL_ENDORSEMENTS: FrozenSet[str] = frozenset(
    {
        "igf_advanced_cop",
        "igf_basic_cop",
        "tanker_oil",
        "tanker_oil_basic_cop",
        "tanker_oil_advanced_cop",
        "tanker_chemical",
        "tanker_chemical_basic_cop",
        "tanker_chemical_advanced_cop",
        "tanker_gas",
        "tanker_gas_basic_cop",
        "tanker_gas_advanced_cop",
        "dp_operational",
        "tanker_oil_dce",
        "tanker_chemical_dce",
        "tanker_gas_dce",
    }
)


def get_family_spec(family_id: str) -> Mapping[str, object] | None:
    return FAMILY_CATALOG.get(str(family_id or ""))


def is_active_family(family_id: str) -> bool:
    return bool(get_family_spec(family_id) and get_family_spec(family_id).get("status") == ACTIVE)


def is_unsupported_family(family_id: str) -> bool:
    return bool(get_family_spec(family_id) and get_family_spec(family_id).get("status") == UNSUPPORTED)


def legacy_hard_constraint_key(family_id: str) -> str | None:
    spec = get_family_spec(family_id)
    if not spec:
        return None
    value = spec.get("legacy_hard_constraints_key")
    return str(value) if value is not None else None


def legacy_applied_constraint_id(family_id: str) -> str | None:
    spec = get_family_spec(family_id)
    if not spec:
        return None
    value = spec.get("legacy_applied_constraint_id")
    return str(value) if value is not None else None


def canonical_ui_filter_type(filter_id: str) -> str | None:
    return UI_FILTER_CATALOG.get(str(filter_id or ""))


def _lower_set(values: Iterable[str]) -> FrozenSet[str]:
    return frozenset(str(value).strip() for value in values if str(value).strip())


def canonical_rank_values() -> FrozenSet[str]:
    return CANONICAL_RANKS


def canonical_ship_family_values() -> FrozenSet[str]:
    return CANONICAL_SHIP_FAMILIES


def canonical_engine_family_values() -> FrozenSet[str]:
    return CANONICAL_ENGINE_FAMILIES


def canonical_certificate_values() -> FrozenSet[str]:
    return CANONICAL_CERTIFICATES


def canonical_endorsement_values() -> FrozenSet[str]:
    return CANONICAL_ENDORSEMENTS
