"""Query-understanding foundation for NjordHR."""

from .hard_filter_catalog import (
    CATALOG_VERSION,
    ACTIVE_FAMILY_IDS,
    SUPPORTED_FAMILY_IDS,
    UNAPPLIED_FAMILY_IDS,
)
from .legacy_parser_adapter import LegacyParserAdapter
from .normalizer_compare import compare_query_plans, canonical_comparison_records
from .shadow_audit import build_shadow_audit_entry, build_shadow_audit_rows
from .shadow_llm_provider import build_shadow_llm_query_plan, build_shadow_llm_prompt
from .schema import normalize_query_plan_v1, validate_query_plan_v1

__all__ = [
    "CATALOG_VERSION",
    "ACTIVE_FAMILY_IDS",
    "SUPPORTED_FAMILY_IDS",
    "UNAPPLIED_FAMILY_IDS",
    "LegacyParserAdapter",
    "compare_query_plans",
    "canonical_comparison_records",
    "build_shadow_audit_entry",
    "build_shadow_audit_rows",
    "build_shadow_llm_query_plan",
    "build_shadow_llm_prompt",
    "normalize_query_plan_v1",
    "validate_query_plan_v1",
]
