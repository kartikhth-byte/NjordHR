"""Portable generic PDF candidate-facts extractor."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Mapping

from ..schema import CANDIDATE_FACTS_SCHEMA_VERSION, normalize_candidate_facts_v1

SOURCE_NAME = "generic_pdf"
DEFAULT_PARSER_VERSION = "generic_pdf.v1"
DEFAULT_SOURCE_ORIGIN = "manual_upload"
DEFAULT_DETECTED_LAYOUT = "unknown"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _presence_for_value(value: Any) -> str:
    return "observed_true" if value not in (None, "", [], {}) else "unobserved_unknown"


def _normalize_rank(analyzer: Any, raw_rank: Any) -> str | None:
    rank_text = str(raw_rank or "").strip()
    if not rank_text:
        return None
    normalize_rank = getattr(analyzer, "_normalize_rank", None)
    if callable(normalize_rank):
        try:
            normalized = normalize_rank(rank_text)
            if isinstance(normalized, (tuple, list)) and normalized:
                canonical_id = normalized[0]
                if isinstance(canonical_id, str) and canonical_id.strip():
                    return canonical_id.strip()
        except Exception:
            pass
    return re.sub(r"[^a-z0-9]+", "_", rank_text.lower()).strip("_") or None


def _extract_text_from_pdf(pdf_path: str | None) -> str:
    if not pdf_path:
        return ""
    try:
        from resume_extractor import ResumeExtractor

        extractor = ResumeExtractor()
        return extractor.extract_text_from_pdf(pdf_path) or ""
    except Exception:
        return ""


def _extract_candidate_name(source_text: str) -> str | None:
    text = str(source_text or "").strip()
    if not text:
        return None

    def _looks_like_candidate_name(value: str) -> bool:
        candidate = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;|-")
        if not candidate:
            return False
        if any(token in candidate.lower() for token in ("passport", "email", "mobile", "phone", "rank", "resume", "curriculum vitae")):
            return False
        if any(char.isdigit() for char in candidate):
            return False
        parts = candidate.split()
        if len(parts) < 2 or len(parts) > 4:
            return False
        return all(re.fullmatch(r"[A-Za-z][A-Za-z'’.-]*", part) for part in parts)

    def _normalize_candidate_name(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip(" .,:;|-")

    try:
        from resume_extractor import ResumeExtractor

        extractor = ResumeExtractor()
        candidate = extractor.extract_candidate_name_from_text(text)
        if candidate and _looks_like_candidate_name(candidate):
            return _normalize_candidate_name(candidate)
    except Exception:
        pass

    # Conservative fallback for generic PDFs that still expose a clear name label.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    for line in lines[:8]:
        if _looks_like_candidate_name(line):
            return _normalize_candidate_name(line)

    for pattern in (
        r"^\s*full\s+name\s*[:.\-]?\s*(.+?)\s*$",
        r"^\s*name\s*[:.\-]?\s*(.+?)\s*$",
    ):
        for line in lines[:20]:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                candidate = _normalize_candidate_name(match.group(1))
                if _looks_like_candidate_name(candidate):
                    return candidate
    return None


def _resolve_source_text(
    *,
    original_path: str | None,
    text_cache: Mapping[str, str] | None,
    chunks: Any,
    raw_text: str | None,
    pdf_path: str | None,
) -> str:
    if raw_text:
        return str(raw_text)

    if original_path and text_cache is not None:
        cached = text_cache.get(str(original_path), "")
        if cached:
            return str(cached)

    if pdf_path:
        extracted = _extract_text_from_pdf(pdf_path)
        if extracted:
            return extracted

    if original_path:
        extracted = _extract_text_from_pdf(original_path)
        if extracted:
            return extracted

    chunk_text = []
    for chunk in chunks or []:
        metadata = chunk.get("metadata") if isinstance(chunk, dict) else {}
        if isinstance(metadata, dict):
            chunk_text.append(str(metadata.get("raw_text", "") or ""))
    return "\n".join(part for part in chunk_text if part).strip()


def _build_source_identity(
    *,
    candidate_id: str,
    filename: str,
    source_origin: str,
    detected_layout: str,
    source_text: str,
) -> Dict[str, Any]:
    content_hash = _stable_hash(f"{filename}|{source_text}|{candidate_id}")
    return {
        "resume_id": candidate_id,
        "candidate_id": candidate_id,
        "source_origin": source_origin,
        "detected_layout": detected_layout,
        "file_name": filename,
        "content_hash": content_hash,
    }


def _build_evidence(*, source_id: str, source_kind: str) -> list[Dict[str, Any]]:
    evidence_id = f"ev-{_stable_hash(source_id)[:12]}"
    return [
        {
            "evidence_id": evidence_id,
            "source_kind": source_kind,
            "source_id": source_id,
        }
    ]


def _basename_for_display(path_value: str | None) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    if "\\" in text and "/" not in text:
        return PureWindowsPath(text).name
    return Path(text.replace("\\", "/")).name


def build_candidate_facts_v1(
    analyzer: Any,
    filename: str,
    rank: str,
    chunks: Any,
    *,
    original_path: str | None = None,
    text_cache: Mapping[str, str] | None = None,
    folder_metadata: Mapping[str, Any] | None = None,
    source_origin: str = DEFAULT_SOURCE_ORIGIN,
    detected_layout: str = DEFAULT_DETECTED_LAYOUT,
    raw_text: str | None = None,
    pdf_path: str | None = None,
) -> Dict[str, Any]:
    source_text = _resolve_source_text(
        original_path=original_path,
        text_cache=text_cache,
        chunks=chunks,
        raw_text=raw_text,
        pdf_path=pdf_path,
    )

    candidate_id = _basename_for_display(str(filename or original_path or pdf_path or "candidate")) or "candidate"
    resolved_filename = _basename_for_display(str(filename or original_path or pdf_path or candidate_id)) or candidate_id
    evidence_source_id = _basename_for_display(str(original_path or pdf_path or resolved_filename))
    evidence_kind = "pdf_page" if (original_path or pdf_path) else "raw_text_chunk"
    evidence = _build_evidence(source_id=evidence_source_id, source_kind=evidence_kind)
    evidence_ids = [item["evidence_id"] for item in evidence]

    extracted_name = _extract_candidate_name(source_text)
    applied_rank_raw = str(rank or "").strip() or None
    applied_rank_normalized = _normalize_rank(analyzer, applied_rank_raw)
    applied_ship_types = []
    if folder_metadata:
        metadata_entry = folder_metadata.get(resolved_filename) or folder_metadata.get(str(filename or "")) or {}
        if isinstance(metadata_entry, Mapping):
            applied_ship_types = metadata_entry.get("applied_ship_types") or []
    if not isinstance(applied_ship_types, list):
        applied_ship_types = []

    candidate_facts = {
        "schema_version": CANDIDATE_FACTS_SCHEMA_VERSION,
        "source": _build_source_identity(
            candidate_id=candidate_id,
            filename=resolved_filename,
            source_origin=source_origin,
            detected_layout=detected_layout,
            source_text=source_text,
        ),
        "identity": {
            "candidate_name": {
                "value": extracted_name,
                "presence": _presence_for_value(extracted_name),
                "confidence": "high" if extracted_name else "low",
                "evidence_ids": evidence_ids,
            },
            "dob": {
                "value": None,
                "presence": "unobserved_unknown",
                "confidence": "low",
                "evidence_ids": evidence_ids,
                "extraction": {
                    "extractor": SOURCE_NAME,
                    "parser_version": DEFAULT_PARSER_VERSION,
                    "method": "fallback",
                },
            },
        },
        "rank": {
            "value": applied_rank_normalized,
            "presence": _presence_for_value(applied_rank_normalized),
            "confidence": "high" if applied_rank_normalized else "low",
            "evidence_ids": evidence_ids,
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
            "applied_ship_types": applied_ship_types,
        },
        "derived": {},
        "evidence": evidence,
        "extraction": {
            "parser_version": DEFAULT_PARSER_VERSION,
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
    return normalize_candidate_facts_v1(candidate_facts)


def extract_candidate_facts(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    if args:
        if len(args) < 4:
            raise TypeError("extract_candidate_facts requires analyzer, filename, rank, and chunks when positional args are used")
        analyzer, filename, rank, chunks = args[:4]
        extra_args = args[4:]
        if extra_args:
            raise TypeError("unexpected positional arguments for extract_candidate_facts")
    else:
        analyzer = kwargs.pop("analyzer", None)
        filename = kwargs.pop("filename", None)
        rank = kwargs.pop("rank", "")
        chunks = kwargs.pop("chunks", [])
        if filename is None and not kwargs.get("raw_text") and not kwargs.get("pdf_path"):
            raise TypeError("extract_candidate_facts requires filename or raw_text/pdf_path")

    return build_candidate_facts_v1(
        analyzer,
        str(filename or "candidate"),
        str(rank or ""),
        chunks,
        original_path=kwargs.get("original_path"),
        text_cache=kwargs.get("text_cache"),
        folder_metadata=kwargs.get("folder_metadata"),
        source_origin=kwargs.get("source_origin", DEFAULT_SOURCE_ORIGIN),
        detected_layout=kwargs.get("detected_layout", DEFAULT_DETECTED_LAYOUT),
        raw_text=kwargs.get("raw_text"),
        pdf_path=kwargs.get("pdf_path"),
    )
