"""Helper functions for candidate-facts storage/versioning workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Dict, List, Sequence


def build_candidate_resume_facts_identity(
    *,
    candidate_resume_id: str,
    schema_version: str,
    parser_version: str,
    facts_revision: str,
) -> str:
    """Return the deterministic identity tuple used for persisted facts rows."""

    return "::".join([candidate_resume_id, schema_version, parser_version, facts_revision])


def build_transient_facts_id(
    *,
    candidate_resume_id: str,
    resume_blob_content_hash: str,
    extractor_version: str,
    raw_text_version: str | None,
    chunk_index_version: str | None,
    fallback_mode: str,
    query_session_id: str,
) -> str:
    """Build the deterministic transient-facts id required for fallback audits."""

    payload = "|".join(
        [
            candidate_resume_id,
            resume_blob_content_hash,
            extractor_version,
            raw_text_version or "",
            chunk_index_version or "",
            fallback_mode,
            query_session_id,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def select_current_candidate_resume_facts_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    schema_version: str,
) -> List[Mapping[str, Any]]:
    """Return candidate-resume facts rows for a key, ordered with the current row first."""

    matching = [
        row
        for row in rows
        if str(row.get("candidate_resume_id") or "") == candidate_resume_id
        and str(row.get("schema_version") or "") == schema_version
    ]

    def sort_key(row: Mapping[str, Any]) -> tuple[int, datetime | None, str]:
        return (
            1 if (_parse_timestamp(row.get("updated_at")) or _parse_timestamp(row.get("created_at"))) else 0,
            _parse_timestamp(row.get("updated_at")) or _parse_timestamp(row.get("created_at")),
            str(row.get("facts_revision") or ""),
        )

    return sorted(matching, key=sort_key, reverse=True)


def ensure_single_current_candidate_resume_facts_row(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    schema_version: str,
) -> List[Dict[str, Any]]:
    """Return a copy of the rows with only the best row marked current for the key."""

    ordered = select_current_candidate_resume_facts_rows(
        rows,
        candidate_resume_id=candidate_resume_id,
        schema_version=schema_version,
    )
    current_id = str(ordered[0].get("id") or "") if ordered else ""
    updated: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if (
            str(item.get("candidate_resume_id") or "") == candidate_resume_id
            and str(item.get("schema_version") or "") == schema_version
        ):
            item["is_current_for_resume"] = str(item.get("id") or "") == current_id
        updated.append(item)
    return updated
