"""CoC country alias loading and validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from candidate_facts.aliases.coc_issue_authority import normalize_alias_key


ALIAS_FILE = Path(__file__).with_name("coc_country.json")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AMBIGUOUS_SHORTCUTS_V1 = {
    "india": {"in"},
    "usa": {"us", "u s"},
}


@dataclass(frozen=True)
class CocCountryAliases:
    version: str
    raw: Mapping[str, Any]
    alias_map: Mapping[str, str]
    alias_map_without_ambiguous_shortcuts: Mapping[str, str]
    authority_country_alias_map: Mapping[str, str]
    display_labels: Mapping[str, str]
    ambiguous_shortcuts_by_country: Mapping[str, tuple[str, ...]]


def _validate_required_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _validate_string_list(value: Any, path: str, *, non_empty: bool) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    if non_empty and not value:
        raise ValueError(f"{path} must be a non-empty list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}[{index}] must be a non-empty string")
        result.append(item)
    return result


def _put_alias(alias_map: Dict[str, str], owners: Dict[str, str], alias: str, canonical: str, path: str) -> None:
    normalized_alias = normalize_alias_key(alias)
    if not normalized_alias:
        raise ValueError(f"{path} contains an empty alias")
    previous = owners.get(normalized_alias)
    if previous and previous != canonical:
        raise ValueError(
            f"duplicate CoC country alias after normalization: {normalized_alias} "
            f"({previous}, {canonical})"
        )
    owners[normalized_alias] = canonical
    alias_map[normalized_alias] = canonical


def load_coc_country_aliases(path: Path = ALIAS_FILE) -> CocCountryAliases:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    root = _validate_required_mapping(payload, "coc_country")
    for key in ("version", "last_updated", "source", "countries"):
        if key not in root:
            raise ValueError(f"coc_country.{key} is required")
    version = root.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("coc_country.version must match x.y.z")
    last_updated = root.get("last_updated")
    if not isinstance(last_updated, str) or not DATE_PATTERN.fullmatch(last_updated):
        raise ValueError("coc_country.last_updated must match YYYY-MM-DD")
    source = root.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("coc_country.source must be a non-empty string")

    countries = _validate_required_mapping(root.get("countries"), "coc_country.countries")
    alias_map: Dict[str, str] = {}
    alias_map_without_ambiguous_shortcuts: Dict[str, str] = {}
    authority_country_alias_map: Dict[str, str] = {}
    alias_owners: Dict[str, str] = {}
    broad_alias_owners: Dict[str, str] = {}
    authority_alias_owners: Dict[str, str] = {}
    display_labels: Dict[str, str] = {}
    ambiguous_shortcuts_by_country: Dict[str, tuple[str, ...]] = {}

    for canonical_id, country_value in countries.items():
        country_path = f"coc_country.countries.{canonical_id}"
        if not isinstance(canonical_id, str) or not canonical_id.strip():
            raise ValueError(f"{country_path} id must be a non-empty string")
        country_entry = _validate_required_mapping(country_value, country_path)
        for key in ("display_label", "authority_catalog_allowed", "aliases"):
            if key not in country_entry:
                raise ValueError(f"{country_path}.{key} is required")
        display_label = country_entry.get("display_label")
        if not isinstance(display_label, str) or not display_label.strip():
            raise ValueError(f"{country_path}.display_label must be a non-empty string")
        authority_catalog_allowed = country_entry.get("authority_catalog_allowed")
        if not isinstance(authority_catalog_allowed, bool):
            raise ValueError(f"{country_path}.authority_catalog_allowed must be boolean")
        aliases = _validate_string_list(country_entry.get("aliases"), f"{country_path}.aliases", non_empty=True)
        ambiguous_shortcuts = _validate_string_list(
            country_entry.get("ambiguous_shortcuts", []),
            f"{country_path}.ambiguous_shortcuts",
            non_empty=False,
        )

        display_labels[canonical_id] = display_label.strip()
        normalized_aliases: set[str] = set()
        for alias in aliases:
            normalized_alias = normalize_alias_key(alias)
            if normalized_alias in normalized_aliases:
                raise ValueError(
                    f"{country_path}.aliases contains duplicate alias after normalization: "
                    f"{normalized_alias} from {alias!r}"
                )
            normalized_aliases.add(normalized_alias)
        if normalize_alias_key(canonical_id) not in normalized_aliases:
            raise ValueError(f"{country_path}.aliases must include its canonical ID")

        for alias in aliases:
            _put_alias(alias_map_without_ambiguous_shortcuts, alias_owners, alias, canonical_id, f"{country_path}.aliases")
            _put_alias(alias_map, broad_alias_owners, alias, canonical_id, f"{country_path}.aliases")
            if authority_catalog_allowed:
                _put_alias(authority_country_alias_map, authority_alias_owners, alias, canonical_id, f"{country_path}.aliases")

        normalized_shortcuts: list[str] = []
        allowed_shortcuts = AMBIGUOUS_SHORTCUTS_V1.get(canonical_id, set())
        for shortcut in ambiguous_shortcuts:
            normalized_shortcut = normalize_alias_key(shortcut)
            if normalized_shortcut not in allowed_shortcuts:
                raise ValueError(f"{country_path}.ambiguous_shortcuts contains an unsupported v1 shortcut")
            if normalized_shortcut in normalized_aliases:
                raise ValueError(f"{country_path}.ambiguous_shortcuts duplicates aliases after normalization")
            _put_alias(alias_map, broad_alias_owners, shortcut, canonical_id, f"{country_path}.ambiguous_shortcuts")
            normalized_shortcuts.append(normalized_shortcut)
        if normalized_shortcuts:
            ambiguous_shortcuts_by_country[canonical_id] = tuple(normalized_shortcuts)

    country_ids = set(countries)
    for canonical_id in alias_map.values():
        if canonical_id not in country_ids:
            raise ValueError(f"coc_country alias map references unknown country: {canonical_id}")

    return CocCountryAliases(
        version=version,
        raw=root,
        alias_map=alias_map,
        alias_map_without_ambiguous_shortcuts=alias_map_without_ambiguous_shortcuts,
        authority_country_alias_map=authority_country_alias_map,
        display_labels=display_labels,
        ambiguous_shortcuts_by_country=ambiguous_shortcuts_by_country,
    )
