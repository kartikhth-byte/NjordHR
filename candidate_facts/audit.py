"""Audit helpers for pinned candidate_resume_facts replay metadata."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def build_candidate_resume_facts_audit_metadata(
    row: Mapping[str, Any] | None,
    *,
    resolution: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return replay-friendly audit metadata for a pinned candidate facts row."""

    row = row or {}
    resolution = resolution or {}
    identity = {
        "candidate_resume_id": row.get("candidate_resume_id"),
        "schema_version": row.get("schema_version"),
        "parser_version": row.get("parser_version"),
        "facts_revision": row.get("facts_revision"),
    }
    return {
        "candidate_resume_facts_id": row.get("id"),
        "candidate_id": row.get("candidate_id"),
        "candidate_resume_id": row.get("candidate_resume_id"),
        "resume_blob_id": row.get("resume_blob_id"),
        "facts_schema_version": row.get("schema_version"),
        "parser_version": row.get("parser_version"),
        "facts_revision": row.get("facts_revision"),
        "pinned_facts_identity": identity,
        "selection_status": resolution.get("status") or ("resolved" if row else "unavailable"),
        "selection_reason": resolution.get("reason"),
        "replay_lookup_kind": "candidate_resume_facts_id" if str(resolution.get("reason") or "") == "id" else "identity_tuple",
    }
