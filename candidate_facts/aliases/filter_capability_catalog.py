"""Shadow LLM filter capability catalog loading and validation."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from query_understanding.hard_filter_catalog import ACTIVE_FAMILY_IDS


CATALOG_FILE = Path(__file__).with_name("filter_capability_catalog.json")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FAMILY_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")

# PR-6 validates executor membership against the existing hard-filter catalog.
# The callable-backed normalizer registry replaces this membership-only map when live dispatch lands.
CAPABILITY_REGISTRY = {family_id: family_id for family_id in ACTIVE_FAMILY_IDS}
PROMOTED_FAMILIES: set[str] = {"availability"}


@dataclass(frozen=True)
class FilterCapabilityCatalog:
    version: str
    last_updated: str
    raw: Mapping[str, Any]
    families_by_id: Mapping[str, Mapping[str, Any]]


def _validate_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _validate_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _validate_string_list(value: Any, path: str, *, min_length: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    if len(value) < min_length:
        raise ValueError(f"{path} must contain at least {min_length} entries")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_validate_string(item, f"{path}[{index}]"))
    return result


def _schema_type_allows(value: Any, expected_type: Any) -> bool:
    expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
    for item_type in expected_types:
        if item_type == "null" and value is None:
            return True
        if item_type == "string" and isinstance(value, str):
            return True
        if item_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item_type == "object" and isinstance(value, Mapping):
            return True
        if item_type == "array" and isinstance(value, list):
            return True
        if item_type == "boolean" and isinstance(value, bool):
            return True
    return False


def _numeric_fields_from_schema(schema: Mapping[str, Any]) -> set[str]:
    numeric_fields: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for field, field_schema in properties.items():
            if not isinstance(field, str) or not isinstance(field_schema, Mapping):
                continue
            field_type = field_schema.get("type")
            field_types = field_type if isinstance(field_type, list) else [field_type]
            if "integer" in field_types or "number" in field_types:
                numeric_fields.add(field)
    return numeric_fields


def _validate_plausibility_bounds(row: Mapping[str, Any], path: str) -> None:
    output_schema = _validate_mapping(row.get("output_schema"), f"{path}.output_schema")
    bounds = _validate_mapping(row.get("plausibility_bounds"), f"{path}.plausibility_bounds")
    numeric_fields = _numeric_fields_from_schema(output_schema)
    if set(bounds) != numeric_fields:
        missing = sorted(numeric_fields - set(bounds))
        extra = sorted(set(bounds) - numeric_fields)
        if missing:
            raise ValueError(f"{path}.plausibility_bounds missing numeric fields: {', '.join(missing)}")
        if extra:
            raise ValueError(f"{path}.plausibility_bounds has bounds for non-numeric fields: {', '.join(extra)}")
    for field, bound_value in bounds.items():
        bound = _validate_mapping(bound_value, f"{path}.plausibility_bounds.{field}")
        min_value = bound.get("min")
        max_value = bound.get("max")
        if not isinstance(min_value, (int, float)) or isinstance(min_value, bool):
            raise ValueError(f"{path}.plausibility_bounds.{field}.min must be numeric")
        if not isinstance(max_value, (int, float)) or isinstance(max_value, bool):
            raise ValueError(f"{path}.plausibility_bounds.{field}.max must be numeric")
        if min_value > max_value:
            raise ValueError(f"{path}.plausibility_bounds.{field}.min cannot exceed max")


def load_filter_capability_catalog(
    path: Path = CATALOG_FILE,
    *,
    capability_registry: Mapping[str, Any] | None = None,
) -> FilterCapabilityCatalog:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    root = _validate_mapping(payload, "filter_capability_catalog")
    for key in ("version", "last_updated", "families"):
        if key not in root:
            raise ValueError(f"filter_capability_catalog.{key} is required")
    version = root.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("filter_capability_catalog.version must match x.y.z")
    last_updated = root.get("last_updated")
    if not isinstance(last_updated, str) or not DATE_PATTERN.fullmatch(last_updated):
        raise ValueError("filter_capability_catalog.last_updated must match YYYY-MM-DD")
    families = root.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("filter_capability_catalog.families must be a non-empty list")

    registry = capability_registry if capability_registry is not None else CAPABILITY_REGISTRY
    families_by_id: dict[str, Mapping[str, Any]] = {}
    required_row_keys = {
        "family",
        "purpose",
        "accepted_phrases",
        "output_schema",
        "do_not_use_for",
        "plausibility_bounds",
        "executor_id",
    }
    for index, raw_row in enumerate(families):
        row_path = f"filter_capability_catalog.families[{index}]"
        row = _validate_mapping(raw_row, row_path)
        missing = sorted(required_row_keys - set(row))
        if missing:
            raise ValueError(f"{row_path} missing required keys: {', '.join(missing)}")
        family = _validate_string(row.get("family"), f"{row_path}.family")
        if not FAMILY_ID_PATTERN.fullmatch(family):
            raise ValueError(f"{row_path}.family must match [a-z0-9_]+")
        if family in families_by_id:
            raise ValueError(f"duplicate filter capability family: {family}")
        _validate_string(row.get("purpose"), f"{row_path}.purpose")
        _validate_string_list(row.get("accepted_phrases"), f"{row_path}.accepted_phrases", min_length=3)
        _validate_string_list(row.get("do_not_use_for"), f"{row_path}.do_not_use_for", min_length=2)
        _validate_mapping(row.get("output_schema"), f"{row_path}.output_schema")
        _validate_plausibility_bounds(row, row_path)
        executor_id = _validate_string(row.get("executor_id"), f"{row_path}.executor_id")
        if executor_id not in registry:
            raise ValueError(f"{row_path}.executor_id is not present in CAPABILITY_REGISTRY: {executor_id}")
        families_by_id[family] = row

    return FilterCapabilityCatalog(
        version=version,
        last_updated=last_updated,
        raw=root,
        families_by_id=families_by_id,
    )


def backend_catalog(catalog: FilterCapabilityCatalog | None = None) -> list[Mapping[str, Any]]:
    loaded = catalog or load_filter_capability_catalog()
    return [deepcopy(row) for row in loaded.families_by_id.values()]


def llm_facing_catalog(catalog: FilterCapabilityCatalog | None = None) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for row in backend_catalog(catalog):
        public_row = dict(row)
        public_row.pop("executor_id", None)
        rows.append(public_row)
    return rows


def _validate_schema_value(schema: Mapping[str, Any], value: Any, path: str) -> None:
    if "const" in schema and value != schema.get("const"):
        raise ValueError(f"{path} must equal {schema.get('const')!r}")
    if "enum" in schema and value not in schema.get("enum", []):
        raise ValueError(f"{path} must be one of {schema.get('enum')!r}")
    if "type" in schema and not _schema_type_allows(value, schema.get("type")):
        raise ValueError(f"{path} has invalid type")
    if schema.get("type") == "object":
        obj = _validate_mapping(value, path)
        required = schema.get("required") or []
        for key in required:
            if key not in obj:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = sorted(set(obj) - set(properties))
            if extra:
                raise ValueError(f"{path} has unknown fields: {', '.join(extra)}")
        if isinstance(properties, Mapping):
            for key, field_schema in properties.items():
                if key in obj and isinstance(field_schema, Mapping):
                    _validate_schema_value(field_schema, obj.get(key), f"{path}.{key}")
        one_of = schema.get("oneOf")
        if isinstance(one_of, list) and one_of:
            matches = 0
            errors: list[str] = []
            for option in one_of:
                if not isinstance(option, Mapping):
                    continue
                try:
                    _validate_schema_value({"type": "object", **option}, obj, path)
                except ValueError as exc:
                    errors.append(str(exc))
                else:
                    matches += 1
            if matches != 1:
                raise ValueError(f"{path} must match exactly one value_type schema")


def validate_catalog_parameters(
    family: str,
    parameters: Mapping[str, Any],
    *,
    catalog: FilterCapabilityCatalog | None = None,
) -> None:
    loaded = catalog or load_filter_capability_catalog()
    row = loaded.families_by_id.get(family)
    if not row:
        raise ValueError(f"Unknown filter capability family: {family}")
    schema = _validate_mapping(row.get("output_schema"), f"filter_capability_catalog.families.{family}.output_schema")
    _validate_schema_value(schema, parameters, f"parameters.{family}")
    if family == "availability":
        _validate_availability_parameters(parameters)
    if family == "vessel_tonnage":
        _validate_vessel_tonnage_parameters(parameters)
    bounds = _validate_mapping(row.get("plausibility_bounds"), f"filter_capability_catalog.families.{family}.plausibility_bounds")
    for field, bound_value in bounds.items():
        value = parameters.get(field)
        if value is None:
            continue
        bound = _validate_mapping(bound_value, f"filter_capability_catalog.families.{family}.plausibility_bounds.{field}")
        if value < bound["min"] or value > bound["max"]:
            raise ValueError(f"parameters.{family}.{field} is outside plausibility bounds")


def _validate_availability_parameters(parameters: Mapping[str, Any]) -> None:
    for field in ("available_by_date", "available_from_date", "available_until_date", "resolved_reference_date"):
        value = parameters.get(field)
        if value is not None and (not isinstance(value, str) or not DATE_PATTERN.fullmatch(value)):
            raise ValueError(f"parameters.availability.{field} must match YYYY-MM-DD")
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"parameters.availability.{field} must match YYYY-MM-DD") from exc
    if not isinstance(parameters.get("display_value"), str) or not parameters.get("display_value", "").strip():
        raise ValueError("parameters.availability.display_value must be a non-empty string")
    if parameters.get("value_type") == "window":
        start = parameters.get("available_from_date")
        end = parameters.get("available_until_date")
        if isinstance(start, str) and isinstance(end, str) and start > end:
            raise ValueError("parameters.availability.available_from_date cannot exceed available_until_date")


def _validate_vessel_tonnage_parameters(parameters: Mapping[str, Any]) -> None:
    if not isinstance(parameters.get("display_value"), str) or not parameters.get("display_value", "").strip():
        raise ValueError("parameters.vessel_tonnage.display_value must be a non-empty string")
    min_value = parameters.get("min_value")
    max_value = parameters.get("max_value")
    if isinstance(min_value, int) and isinstance(max_value, int) and min_value > max_value:
        raise ValueError("parameters.vessel_tonnage.min_value cannot exceed max_value")
