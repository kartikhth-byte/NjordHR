"""Canonical comparison helpers for legacy vs LLM query plans."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .hard_filter_catalog import CATALOG_VERSION, is_unsupported_family
from .schema import normalize_query_plan_v1


@dataclass(frozen=True)
class CanonicalComparisonRecord:
    catalog_snapshot_id: str
    prompt_id: str
    family: str
    mode: str
    normalized_payload: Any
    source_text: str
    status: str


@dataclass(frozen=True)
class ComparisonResult:
    catalog_snapshot_id: str
    prompt_id: str
    family: str
    mode: str
    legacy_record: CanonicalComparisonRecord | None
    llm_record: CanonicalComparisonRecord | None
    comparison_outcome: str


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _normalizer_catalog_version(plan: Mapping[str, Any]) -> str:
    normalizer = plan.get("normalizer") if isinstance(plan, Mapping) else {}
    if isinstance(normalizer, Mapping) and isinstance(normalizer.get("catalog_version"), str):
        return normalizer.get("catalog_version") or CATALOG_VERSION
    return CATALOG_VERSION


def canonical_comparison_records(
    plan: Mapping[str, Any],
    *,
    prompt_id: str,
    catalog_snapshot_id: str | None = None,
) -> List[CanonicalComparisonRecord]:
    normalized = normalize_query_plan_v1(plan, mode="production")
    snapshot_id = catalog_snapshot_id or _normalizer_catalog_version(normalized)
    records: List[CanonicalComparisonRecord] = []

    for item in normalized.get("applied_constraints") or []:
        records.append(
            CanonicalComparisonRecord(
                catalog_snapshot_id=snapshot_id,
                prompt_id=prompt_id,
                family=str(item.get("id")),
                mode=str(item.get("mode")),
                normalized_payload=item.get("constraint"),
                source_text=str(item.get("source_text") or ""),
                status="applied" if normalized.get("validation", {}).get("status") != "invalid" else "invalid",
            )
        )

    for item in normalized.get("unapplied_constraints") or []:
        records.append(
            CanonicalComparisonRecord(
                catalog_snapshot_id=snapshot_id,
                prompt_id=prompt_id,
                family=str(item.get("id")),
                mode=str(item.get("mode")),
                normalized_payload={
                    "reason": item.get("reason"),
                    "suggested_handling": item.get("suggested_handling"),
                },
                source_text=str(item.get("source_text") or ""),
                status="unapplied" if normalized.get("validation", {}).get("status") != "invalid" else "invalid",
            )
        )

    return records


def _index_records(records: Sequence[CanonicalComparisonRecord]) -> Dict[Tuple[str, str], CanonicalComparisonRecord]:
    indexed: Dict[Tuple[str, str], CanonicalComparisonRecord] = {}
    for record in records:
        indexed[(record.family, record.mode)] = record
    return indexed


def _is_equivalent(legacy: CanonicalComparisonRecord, llm: CanonicalComparisonRecord) -> bool:
    if legacy.status != llm.status:
        return False
    return _freeze(legacy.normalized_payload) == _freeze(llm.normalized_payload)


def compare_query_plans(
    legacy_plan: Mapping[str, Any],
    llm_plan: Mapping[str, Any],
    *,
    prompt_id: str,
    expected_delta_families: Iterable[str] | None = None,
    catalog_snapshot_id: str | None = None,
) -> List[ComparisonResult]:
    legacy_normalized = normalize_query_plan_v1(legacy_plan, mode="production")
    llm_normalized = normalize_query_plan_v1(llm_plan, mode="production")
    snapshot_id = catalog_snapshot_id or _normalizer_catalog_version(legacy_normalized)

    if _normalizer_catalog_version(legacy_normalized) != _normalizer_catalog_version(llm_normalized):
        family = "catalogue"
        legacy_record = CanonicalComparisonRecord(snapshot_id, prompt_id, family, "required", {}, "", "invalid")
        llm_record = CanonicalComparisonRecord(snapshot_id, prompt_id, family, "required", {}, "", "invalid")
        return [
            ComparisonResult(
                catalog_snapshot_id=snapshot_id,
                prompt_id=prompt_id,
                family=family,
                mode="required",
                legacy_record=legacy_record,
                llm_record=llm_record,
                comparison_outcome="catalogue_drift",
            )
        ]

    legacy_records = canonical_comparison_records(legacy_normalized, prompt_id=prompt_id, catalog_snapshot_id=snapshot_id)
    llm_records = canonical_comparison_records(llm_normalized, prompt_id=prompt_id, catalog_snapshot_id=snapshot_id)
    legacy_index = _index_records(legacy_records)
    llm_index = _index_records(llm_records)
    family_keys = sorted(set(legacy_index.keys()) | set(llm_index.keys()))
    expected_delta_families = set(expected_delta_families or [])

    results: List[ComparisonResult] = []
    legacy_invalid = legacy_normalized.get("validation", {}).get("status") == "invalid"
    llm_invalid = llm_normalized.get("validation", {}).get("status") == "invalid"
    legacy_validation_errors = legacy_normalized.get("validation", {}).get("errors") or []
    legacy_only_mandatory_marker_error = bool(legacy_validation_errors) and all(
        isinstance(error, Mapping) and error.get("code") == "mandatory_marker_in_semantic_query"
        for error in legacy_validation_errors
    )
    for family, mode in family_keys:
        legacy_record = legacy_index.get((family, mode))
        llm_record = llm_index.get((family, mode))

        if llm_invalid or (legacy_invalid and not llm_record):
            outcome = "schema_error"
        elif (
            legacy_invalid
            and legacy_only_mandatory_marker_error
            and llm_normalized.get("validation", {}).get("status") == "valid"
            and legacy_record
            and llm_record
            and _freeze(legacy_record.normalized_payload) == _freeze(llm_record.normalized_payload)
        ):
            outcome = "expected_delta"
        elif legacy_invalid:
            outcome = "schema_error"
        elif family in expected_delta_families:
            outcome = "expected_delta"
        elif legacy_record and llm_record and _is_equivalent(legacy_record, llm_record):
            outcome = "equivalent"
        elif is_unsupported_family(family) or (
            (legacy_record and isinstance(legacy_record.normalized_payload, Mapping) and legacy_record.normalized_payload.get("reason") == "unsupported_filter_family")
            or (llm_record and isinstance(llm_record.normalized_payload, Mapping) and llm_record.normalized_payload.get("reason") == "unsupported_filter_family")
        ):
            outcome = "unsupported_family_delta"
        else:
            outcome = "regression"

        results.append(
            ComparisonResult(
                catalog_snapshot_id=snapshot_id,
                prompt_id=prompt_id,
                family=family,
                mode=mode,
                legacy_record=legacy_record,
                llm_record=llm_record,
                comparison_outcome=outcome,
            )
        )

    return results
