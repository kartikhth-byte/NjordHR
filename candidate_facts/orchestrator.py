"""Candidate-facts extraction orchestration helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping

from .extractors import generic_pdf, seajobs


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
    return generic_pdf.build_candidate_facts_v1(
        analyzer,
        filename,
        rank,
        chunks,
        original_path=original_path,
        text_cache=text_cache,
        folder_metadata=folder_metadata,
        source_origin=source_origin,
        detected_layout=detected_layout,
    )


def _source_text_from_inputs(
    chunks: Any,
    *,
    original_path: str | None = None,
    text_cache: Mapping[str, str] | None = None,
) -> str:
    if original_path and text_cache is not None:
        cached = str(text_cache.get(str(original_path), "") or "")
        if cached:
            return cached
    return "\n".join(str((chunk.get("metadata") or {}).get("raw_text", "")) for chunk in (chunks or []))


def _looks_like_seajobs_layout(source_text: str) -> bool:
    text = str(source_text or "")
    if not text.strip():
        return False
    upper = text.upper()
    if "SEAMEN EXPERIENCE DETAILS" in upper and "COMPANY NAME / SHIP TYPE" in upper:
        return True
    if (
        "SEAMEN EXPERIENCE DETAILS" in upper
        and re.search(r"\bTONNAGE\b", text, flags=re.IGNORECASE)
        and re.search(r"\bSIGN\s+IN\b|\bSIGN\s+OUT\b", text, flags=re.IGNORECASE)
    ):
        return True
    return False


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
    source_text = _source_text_from_inputs(chunks, original_path=original_path, text_cache=text_cache)
    if source_origin == "seajobs_download" or detected_layout == "seajobs" or _looks_like_seajobs_layout(source_text):
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
