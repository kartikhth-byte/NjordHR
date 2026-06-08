"""Candidate-facts extraction orchestration helpers."""

from __future__ import annotations

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
