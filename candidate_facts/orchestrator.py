"""Candidate-facts extraction orchestration helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from .extractors import seajobs
from .schema import CANDIDATE_FACTS_SCHEMA_VERSION, normalize_candidate_facts_v1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_source_identity(
    *,
    candidate_id: str,
    filename: str,
    source_origin: str,
    detected_layout: str,
    source_text: str,
) -> Dict[str, Any]:
    # Reuse the SeaJobs bridge's stable source identity shape for all sources.
    return seajobs._build_source_identity(  # type: ignore[attr-defined]
        {"candidate_id": candidate_id},
        filename,
        source_text,
    ) | {
        "source_origin": source_origin,
        "detected_layout": detected_layout,
    }


def _build_generic_candidate_facts_v1(
    analyzer: Any,
    filename: str,
    rank: str,
    chunks: Any,
    *,
    original_path: str | None = None,
    text_cache: Mapping[str, str] | None = None,
    folder_metadata: Mapping[str, Any] | None = None,
    source_origin: str = "manual_upload",
    detected_layout: str = "unknown",
) -> Dict[str, Any]:
    source_text = ""
    if original_path and text_cache is not None:
        source_text = str(text_cache.get(str(original_path), "") or "")
    if not source_text:
        source_text = "\n".join(str((chunk.get("metadata") or {}).get("raw_text", "")) for chunk in (chunks or []))
    candidate_id = str(filename or original_path or "candidate")
    source = _stable_source_identity(
        candidate_id=candidate_id,
        filename=str(filename or original_path or candidate_id),
        source_origin=source_origin,
        detected_layout=detected_layout,
        source_text=source_text,
    )
    payload = {
        "schema_version": CANDIDATE_FACTS_SCHEMA_VERSION,
        "source": source,
        "identity": {
            "candidate_name": {
                "value": None,
                "presence": "unobserved_unknown",
                "confidence": "low",
                "evidence_ids": [],
            },
            "dob": {
                "value": None,
                "presence": "unobserved_unknown",
                "confidence": "low",
                "evidence_ids": [],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
            },
        },
        "rank": {
            "value": None,
            "presence": "unobserved_unknown",
            "confidence": "low",
            "evidence_ids": [],
        },
        "documents": [],
        "certificates": [],
        "endorsements": [],
        "courses": [],
        "contracts": [],
        "rank_experience": [],
        "engine_experience": [],
        "vessel_experience": [],
        "application": {
            "applied_ship_types": [],
        },
        "derived": {},
        "evidence": [],
        "extraction": {
            "parser_version": "generic_pdf.v1",
            "status": "partial" if source_text else "failed",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "raw_text_fallback" if source_text else "transient_fallback",
                "raw_text_version": "v1" if source_text else None,
                "chunk_index_version": "v1" if chunks else None,
                "fallback_reason": "unsupported_layout",
            },
            "warnings": ["generic_candidate_facts_fallback"],
        },
    }
    return normalize_candidate_facts_v1(payload)


def build_candidate_facts_v1(
    analyzer: Any,
    filename: str,
    rank: str,
    chunks: Any,
    *,
    original_path: str | None = None,
    text_cache: Mapping[str, str] | None = None,
    folder_metadata: Mapping[str, Any] | None = None,
    source_origin: str | None = None,
    detected_layout: str | None = None,
) -> Dict[str, Any]:
    source_origin = str(source_origin or "").strip()
    detected_layout = str(detected_layout or "").strip()
    if source_origin == "seajobs_download" or detected_layout == "seajobs":
        return seajobs.build_candidate_facts_v1(
            analyzer,
            filename,
            rank,
            chunks,
            original_path=original_path,
            text_cache=text_cache,
            folder_metadata=folder_metadata,
        )
    return _build_generic_candidate_facts_v1(
        analyzer,
        filename,
        rank,
        chunks,
        original_path=original_path,
        text_cache=text_cache,
        folder_metadata=folder_metadata,
        source_origin=source_origin or "manual_upload",
        detected_layout=detected_layout or "unknown",
    )
