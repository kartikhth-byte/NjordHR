"""Version helpers for candidate-facts records."""

from __future__ import annotations

CANDIDATE_FACTS_SCHEMA_VERSION = "candidate_facts.v1"


def is_supported_schema_version(version: str | None) -> bool:
    return str(version or "") == CANDIDATE_FACTS_SCHEMA_VERSION
