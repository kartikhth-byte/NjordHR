"""Persistence helpers for candidate_resume_facts rows.

This module provides a small in-memory persistence adapter that mirrors the
spec's current-row and revision semantics without coupling to any production
storage backend.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Sequence

from .schema import normalize_candidate_facts_v1
from .storage import ensure_single_current_candidate_resume_facts_row, select_current_candidate_resume_facts_rows


DEFAULT_ACCEPTABLE_STATUSES = {"complete", "partial"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_candidate_resume_facts_content_hash(candidate_facts: Mapping[str, Any]) -> str:
    payload = json.dumps(candidate_facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_candidate_resume_facts_row_id(
    *,
    candidate_resume_id: str,
    schema_version: str,
    parser_version: str,
    facts_revision: str,
    candidate_facts_hash: str | None = None,
) -> str:
    payload_parts = [candidate_resume_id, schema_version, parser_version, facts_revision]
    if candidate_facts_hash:
        payload_parts.append(candidate_facts_hash)
    payload = "::".join(payload_parts)
    return f"candidate_resume_facts:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def build_candidate_resume_facts_row(
    *,
    candidate_resume_id: str,
    resume_blob_id: str,
    candidate_facts: Mapping[str, Any],
    parser_version: str,
    facts_revision: str,
    candidate_facts_hash: str | None = None,
    row_id: str | None = None,
    is_current_for_resume: bool = False,
    created_at: str | None = None,
    updated_at: str | None = None,
    extraction_warnings: Sequence[str] | None = None,
) -> Dict[str, Any]:
    normalized = normalize_candidate_facts_v1(candidate_facts)
    schema_version = str(normalized.get("schema_version") or "candidate_facts.v1")
    candidate_facts_hash = candidate_facts_hash or build_candidate_resume_facts_content_hash(normalized)
    row = {
        "id": row_id or build_candidate_resume_facts_row_id(
            candidate_resume_id=candidate_resume_id,
            schema_version=schema_version,
            parser_version=parser_version,
            facts_revision=facts_revision,
            candidate_facts_hash=candidate_facts_hash,
        ),
        "candidate_id": str((normalized.get("source") or {}).get("candidate_id") or candidate_resume_id),
        "candidate_resume_id": candidate_resume_id,
        "resume_blob_id": resume_blob_id,
        "schema_version": schema_version,
        "parser_version": parser_version,
        "facts_revision": facts_revision,
        "candidate_facts_hash": candidate_facts_hash,
        "facts_json": deepcopy(normalized),
        "extraction_status": str((normalized.get("extraction") or {}).get("status") or "failed"),
        "extraction_warnings": list(extraction_warnings or (normalized.get("extraction") or {}).get("warnings") or []),
        "is_current_for_resume": bool(is_current_for_resume),
        "created_at": created_at or _utc_now_iso(),
        "updated_at": updated_at or _utc_now_iso(),
    }
    return row


def select_candidate_resume_facts_row_by_identity(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    schema_version: str,
    parser_version: str,
    facts_revision: str,
    candidate_facts_hash: str | None = None,
) -> Mapping[str, Any] | None:
    matches = []
    for row in rows:
        if (
            str(row.get("candidate_resume_id") or "") == candidate_resume_id
            and str(row.get("schema_version") or "") == schema_version
            and str(row.get("parser_version") or "") == parser_version
            and str(row.get("facts_revision") or "") == facts_revision
        ):
            if candidate_facts_hash and str(row.get("candidate_facts_hash") or "") != candidate_facts_hash:
                continue
            matches.append(row)
    if not matches:
        return None
    current_matches = [row for row in matches if bool(row.get("is_current_for_resume"))]
    if current_matches:
        return current_matches[0]
    return matches[-1]


def select_current_candidate_resume_facts_row(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    schema_version: str,
) -> Mapping[str, Any] | None:
    selected = select_current_candidate_resume_facts_rows(
        rows,
        candidate_resume_id=candidate_resume_id,
        schema_version=schema_version,
    )
    return selected[0] if selected else None


def resolve_candidate_resume_facts_for_replay(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    schema_version: str,
    parser_version: str,
    facts_revision: str,
    candidate_resume_facts_id: str | None = None,
) -> Dict[str, Any]:
    """Resolve a pinned facts row for replay without using the current selector."""

    if candidate_resume_facts_id:
        for row in rows:
            if str(row.get("id") or "") == candidate_resume_facts_id:
                return {
                    "status": "resolved",
                    "reason": "id",
                    "row": row,
                }
        return {
            "status": "unavailable",
            "reason": "not_found",
            "row": None,
        }

    row = select_candidate_resume_facts_row_by_identity(
        rows,
        candidate_resume_id=candidate_resume_id,
        schema_version=schema_version,
        parser_version=parser_version,
        facts_revision=facts_revision,
    )
    if row is None:
        return {
            "status": "unavailable",
            "reason": "not_found",
            "row": None,
        }
    if candidate_resume_facts_id and str(row.get("id") or "") != candidate_resume_facts_id:
        return {
            "status": "resolved",
            "reason": "identity_mismatch",
            "row": row,
        }
    return {
        "status": "resolved",
        "reason": "identity",
        "row": row,
    }


def upsert_candidate_resume_facts_row(
    rows: Sequence[Mapping[str, Any]],
    new_row: Mapping[str, Any],
    *,
    acceptable_extraction_statuses: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    acceptable = set(acceptable_extraction_statuses or DEFAULT_ACCEPTABLE_STATUSES)
    updated = [dict(row) for row in rows if str(row.get("id") or "") != str(new_row.get("id") or "")]
    record = dict(new_row)
    record["is_current_for_resume"] = False
    record["updated_at"] = record.get("updated_at") or _utc_now_iso()
    updated.append(record)

    normalized = record.get("facts_json") if isinstance(record.get("facts_json"), Mapping) else {}
    validation_status = str((normalized.get("validation") or {}).get("status") or "invalid")
    extraction_status = str(record.get("extraction_status") or "")
    is_acceptable = validation_status != "invalid" and extraction_status in acceptable

    if is_acceptable:
        updated = ensure_single_current_candidate_resume_facts_row(
            updated,
            candidate_resume_id=str(record.get("candidate_resume_id") or ""),
            schema_version=str(record.get("schema_version") or ""),
        )
        # Ensure the newly inserted row becomes the current row.
        for item in updated:
            if str(item.get("id") or "") == str(record.get("id") or ""):
                item["is_current_for_resume"] = True
            elif (
                str(item.get("candidate_resume_id") or "") == str(record.get("candidate_resume_id") or "")
                and str(item.get("schema_version") or "") == str(record.get("schema_version") or "")
            ):
                item["is_current_for_resume"] = False
    return updated


def persist_candidate_resume_facts(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate_resume_id: str,
    resume_blob_id: str,
    candidate_facts: Mapping[str, Any],
    parser_version: str,
    facts_revision: str,
    candidate_facts_hash: str | None = None,
    row_id: str | None = None,
    acceptable_extraction_statuses: Iterable[str] | None = None,
    extraction_warnings: Sequence[str] | None = None,
) -> Dict[str, Any]:
    row = build_candidate_resume_facts_row(
        candidate_resume_id=candidate_resume_id,
        resume_blob_id=resume_blob_id,
        candidate_facts=candidate_facts,
        parser_version=parser_version,
        facts_revision=facts_revision,
        candidate_facts_hash=candidate_facts_hash,
        row_id=row_id,
        extraction_warnings=extraction_warnings,
    )
    updated_rows = upsert_candidate_resume_facts_row(
        rows,
        row,
        acceptable_extraction_statuses=acceptable_extraction_statuses,
    )
    current_row = next(
        (
            dict(item)
            for item in updated_rows
            if str(item.get("id") or "") == str(row.get("id") or "") and item.get("is_current_for_resume")
        ),
        None,
    )
    return {
        "rows": updated_rows,
        "row": row,
        "current_row": current_row,
        "committed": bool(current_row and str(current_row.get("id") or "") == str(row.get("id") or "")),
    }
