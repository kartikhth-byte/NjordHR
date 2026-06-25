"""CoC issue-authority alias loading and validation."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, NamedTuple


ALIAS_FILE = Path(__file__).with_name("coc_issue_authority.json")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def normalize_alias_key(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z]+", " ", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


@dataclass(frozen=True)
class CocIssueAuthorityAliases:
    version: str
    raw: Mapping[str, Any]
    alias_map: Mapping[str, str]
    display_labels: Mapping[str, str]
    country_by_authority: Mapping[str, str]


class _AliasEntry(NamedTuple):
    alias: str
    authority_id: str
    country: str
    path: str


def _validate_required_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _alias_has_country_qualifier(alias: str, country: str, country_aliases: Mapping[str, str]) -> bool:
    country_tokens = {
        normalized_alias
        for normalized_alias, canonical in (country_aliases or {}).items()
        if canonical == country and normalized_alias
    }
    return any(re.search(rf"\b{re.escape(country_alias)}\b", alias) for country_alias in country_tokens)


def _validate_cross_canonical_bare_aliases(alias_entries: list[_AliasEntry], country_aliases: Mapping[str, str]) -> None:
    bare_aliases = [
        entry
        for entry in alias_entries
        if not _alias_has_country_qualifier(entry.alias, entry.country, country_aliases)
    ]
    for bare_entry in bare_aliases:
        if not bare_entry.alias:
            continue
        bare_pattern = re.compile(rf"(^|\s){re.escape(bare_entry.alias)}(\s|$)")
        for qualified_entry in alias_entries:
            if qualified_entry.authority_id == bare_entry.authority_id:
                continue
            if not bare_pattern.search(qualified_entry.alias):
                continue
            if qualified_entry.alias == bare_entry.alias:
                continue
            raise ValueError(
                "ambiguous bare CoC authority alias across canonicals: "
                f"'{bare_entry.alias}' at {bare_entry.path} ({bare_entry.authority_id}/{bare_entry.country}) "
                f"conflicts with {qualified_entry.path} ({qualified_entry.authority_id}/{qualified_entry.country})"
            )


def load_coc_issue_authority_aliases(path: Path = ALIAS_FILE, *, country_aliases: Mapping[str, str]) -> CocIssueAuthorityAliases:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    root = _validate_required_mapping(payload, "coc_issue_authority")
    for key in ("version", "last_updated", "source", "authorities"):
        if key not in root:
            raise ValueError(f"coc_issue_authority.{key} is required")
    version = root.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError("coc_issue_authority.version must match x.y.z")

    authorities_by_country = _validate_required_mapping(root.get("authorities"), "coc_issue_authority.authorities")
    normalized_country_aliases = {
        normalize_alias_key(alias): canonical
        for alias, canonical in (country_aliases or {}).items()
        if normalize_alias_key(alias)
    }
    alias_map: Dict[str, str] = {}
    alias_owner: Dict[str, str] = {}
    display_labels: Dict[str, str] = {}
    country_by_authority: Dict[str, str] = {}
    seen_authorities: set[str] = set()
    alias_entries: list[_AliasEntry] = []

    for country_key, country_entry_value in authorities_by_country.items():
        country_path = f"coc_issue_authority.authorities.{country_key}"
        country_entry = _validate_required_mapping(country_entry_value, country_path)
        for key in ("country_canonical", "authorities"):
            if key not in country_entry:
                raise ValueError(f"{country_path}.{key} is required")
        country_canonical = country_entry.get("country_canonical")
        if not isinstance(country_canonical, str) or not country_canonical.strip():
            raise ValueError(f"{country_path}.country_canonical must be a non-empty string")
        if country_canonical not in set(normalized_country_aliases.values()):
            raise ValueError(f"{country_path}.country_canonical is not in the CoC country aliases")

        authority_entries = _validate_required_mapping(country_entry.get("authorities"), f"{country_path}.authorities")
        for authority_id, authority_value in authority_entries.items():
            authority_path = f"{country_path}.authorities.{authority_id}"
            authority_entry = _validate_required_mapping(authority_value, authority_path)
            if authority_id in seen_authorities:
                raise ValueError(f"duplicate CoC authority id: {authority_id}")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(authority_id or "")):
                raise ValueError(f"{authority_path} id must be canonical snake_case")
            seen_authorities.add(authority_id)
            for key in ("display_label", "aliases"):
                if key not in authority_entry:
                    raise ValueError(f"{authority_path}.{key} is required")
            display_label = authority_entry.get("display_label")
            if not isinstance(display_label, str) or not display_label.strip():
                raise ValueError(f"{authority_path}.display_label must be a non-empty string")
            aliases = authority_entry.get("aliases")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError(f"{authority_path}.aliases must be a non-empty list")
            display_labels[authority_id] = display_label.strip()
            country_by_authority[authority_id] = country_canonical
            for raw_alias in aliases:
                normalized_alias = normalize_alias_key(raw_alias)
                if not normalized_alias:
                    raise ValueError(f"{authority_path}.aliases contains an empty alias")
                previous = alias_owner.get(normalized_alias)
                if previous and previous != authority_id:
                    raise ValueError(f"duplicate CoC authority alias after normalization: {normalized_alias}")
                alias_owner[normalized_alias] = authority_id
                alias_map[normalized_alias] = authority_id
                alias_entries.append(
                    _AliasEntry(
                        alias=normalized_alias,
                        authority_id=authority_id,
                        country=country_canonical,
                        path=f"{authority_path}.aliases",
                    )
                )

    _validate_cross_canonical_bare_aliases(alias_entries, normalized_country_aliases)

    return CocIssueAuthorityAliases(
        version=version,
        raw=root,
        alias_map=alias_map,
        display_labels=display_labels,
        country_by_authority=country_by_authority,
    )
