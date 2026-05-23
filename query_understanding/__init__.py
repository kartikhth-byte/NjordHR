"""Query-understanding foundation for NjordHR."""

from .hard_filter_catalog import (
    CATALOG_VERSION,
    ACTIVE_FAMILY_IDS,
    SUPPORTED_FAMILY_IDS,
    UNAPPLIED_FAMILY_IDS,
)
from .legacy_parser_adapter import LegacyParserAdapter
from .normalizer_compare import compare_query_plans, canonical_comparison_records
from .schema import normalize_query_plan_v1, validate_query_plan_v1

__all__ = [
    "CATALOG_VERSION",
    "ACTIVE_FAMILY_IDS",
    "SUPPORTED_FAMILY_IDS",
    "UNAPPLIED_FAMILY_IDS",
    "LegacyParserAdapter",
    "compare_query_plans",
    "canonical_comparison_records",
    "normalize_query_plan_v1",
    "validate_query_plan_v1",
]
