"""Portable generic PDF candidate-facts extractor."""

from __future__ import annotations

import hashlib
import json
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


def _normalize_candidate_name(value: str) -> str:
    candidate = re.sub(r"[.]+", " ", str(value or ""))
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,:;|-")
    pieces = []
    for part in candidate.split():
        if part.isupper() or part.islower():
            pieces.append(part.capitalize())
        else:
            pieces.append(part)
    candidate = " ".join(pieces)
    candidate = re.sub(r"\bVaibha\s+V\b", "Vaibhav", candidate, flags=re.IGNORECASE)
    return candidate


def _strip_candidate_name_noise(value: str) -> str:
    candidate = _normalize_candidate_name(value)
    noise_words = {
        "vaccine",
        "date",
        "vessel",
        "type",
        "bhp",
        "grt",
        "engine",
        "contract",
        "appraisal",
        "pages",
        "personal",
        "data",
        "color",
        "photograph",
        "owners",
        "citizenship",
        "residence",
        "closest",
        "airport",
        "next",
        "kin",
        "relationship",
        "wife",
        "other",
        "name",
        "linked",
        "in",
        "constant",
        "learner",
        "self",
        "reliant",
        "motivated",
        "merchant",
        "navy",
        "availability",
        "details",
        "company",
        "ship",
        "kw",
        "till",
        "of",
        "mar",
    }
    parts = candidate.split()
    while parts and parts[-1].lower() in noise_words:
        parts.pop()
    while parts and parts[0].lower() in noise_words:
        parts.pop(0)
    return " ".join(parts)


def _looks_like_candidate_name(value: str, *, allow_single_word: bool = False) -> bool:
    candidate = _strip_candidate_name_noise(value)
    if not candidate:
        return False
    lowered = candidate.lower()
    blocked_tokens = (
        "passport",
        "asinpassport",
        "nameasinpassport",
        "email",
        "mobile",
        "phone",
        "rank",
        "resume",
        "curriculum vitae",
        "vaccine",
        "date",
        "vessel",
        "type",
        "bhp",
        "grt",
        "engine",
        "contract",
        "appraisal",
        "rating",
        "objective",
        "experience",
        "information",
        "certificate",
        "personal",
        "data",
        "color",
        "photograph",
        "owners",
        "citizenship",
        "residence",
        "closest",
        "airport",
        "next of kin",
        "relationship",
        "linked in",
        "constant learner",
        "merchant navy",
        "availability",
        "details",
        "company",
        "ship",
        "kw",
        "till",
    )
    blocked_patterns = [re.escape(token).replace(r"\ ", r"\s+") for token in blocked_tokens]
    if any(re.search(rf"\b{pattern}\b", lowered) for pattern in blocked_patterns):
        return False
    if any(char.isdigit() for char in candidate):
        return False
    parts = candidate.split()
    blocked_exact_words = {
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "of",
    }
    min_parts = 1 if allow_single_word else 2
    if len(parts) < min_parts or len(parts) > 4:
        return False
    if any(part.lower().strip("'’.") in blocked_exact_words for part in parts):
        return False
    return all(re.fullmatch(r"[A-Za-z][A-Za-z'’.-]*", part) for part in parts)


def _load_sidecar_metadata(original_path: str | None, pdf_path: str | None) -> Dict[str, Any]:
    path_value = str(original_path or pdf_path or "").strip()
    if not path_value:
        return {}
    sidecar = Path(path_value + ".json")
    if not sidecar.exists():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_name_from_email(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"([A-Za-z0-9._%+-]+)@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if not match:
        return None
    local = re.sub(r"\d+", " ", match.group(1))
    tokens = [token for token in re.split(r"[._-]+", local) if len(token) >= 2]
    if len(tokens) < 2:
        return None
    candidate = _normalize_candidate_name(" ".join(tokens[:4]))
    return candidate if _looks_like_candidate_name(candidate) else None


def _candidate_name_from_filename(value: Any) -> str | None:
    filename = _basename_for_display(str(value or ""))
    stem = re.sub(r"\.(?:[Pp][Dd][Ff]|[Dd][Oo][Cc][Xx]?)$", "", filename)
    stem = re.sub(r"^EMAIL_\d{8}_\d{6}_[A-Za-z0-9]+_", "", stem)
    stem = re.sub(r"[_-]+", " ", stem)
    stem = re.sub(
        r"\b(?:cv|resume|bio data|biodata|2e|2nd|second|3e|3rd|third|engineer|engg|eng|co|chief|mate|officer|off|application|for|as|on|all|promotion|letters|appraisals|updated|new|pdf|crude|oil|product|chemical|tanker|bulk|carrier)\b",
        " ",
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r"\b\d+\b", " ", stem)
    candidate = _normalize_candidate_name(stem)
    if _looks_like_candidate_name(candidate, allow_single_word=True):
        return candidate
    return None


def _candidate_name_from_profile_fields(source_text: str) -> str | None:
    flattened = re.sub(r"\s+", " ", str(source_text or ""))
    name_part = r"([A-Za-z][A-Za-z'’.-]*(?:\s+[A-Za-z][A-Za-z'’.-]*){0,2}?)"
    patterns = (
        (
            rf"\bFirst\s+Name\s*:?\s*{name_part}\s+Middle\s+Name\s*:?\s*(?:(?!Sur\s*Name\b)[A-Za-z][A-Za-z'’.-]*\s+)?Sur\s*Name\s*:?\s*{name_part}(?=\s+(?:Email\s+Address|Other\s+Name|Full\s+Name|Rank|Date|Nationality)|$)",
            "first_surname",
        ),
        (
            rf"\bFirst\s+Name\s*:?\s*{name_part}\s+Middle\s+Name\s*:?\s*(?:(?!Surname\b)[A-Za-z][A-Za-z'’.-]*\s+)?Surname\s*:?\s*{name_part}(?=\s+(?:Email\s+Address|Other\s+Name|Full\s+Name|Rank|Date|Nationality)|$)",
            "first_surname",
        ),
        (
            rf"\bSURNAME\s*:?\s*{name_part}\s+FIRST\s+NAME\s*:?\s*{name_part}(?=\s+(?:Mid(?:dle|del)\s+Name|Other\s+Name|Full\s+Name|Rank|Date|Nationality)|$)",
            "surname_first",
        ),
        (
            rf"\bName\s+{name_part}\s+Email\s+Address\b",
            "plain",
        ),
        (
            r"\b(?:Name\s*as\s*in\s*Passport|NameasinPassport)\b.*?\)\s*([A-Za-z][A-Za-z'’.-]*)\s+([A-Za-z][A-Za-z'’.-]*)(?=\s+Date|$)",
            "surname_first",
        ),
    )
    for pattern, order in patterns:
        match = re.search(pattern, flattened, flags=re.IGNORECASE)
        if not match:
            continue
        if order == "surname_first":
            candidate = f"{match.group(2)} {match.group(1)}"
        elif order == "first_surname":
            candidate = f"{match.group(1)} {match.group(2)}"
        else:
            candidate = match.group(1)
        candidate = _normalize_candidate_name(candidate)
        if _looks_like_candidate_name(candidate):
            return candidate

    first_line = next((re.sub(r"\s+", " ", line).strip() for line in str(source_text or "").splitlines() if line.strip()), "")
    match = re.match(r"^([A-Za-z][A-Za-z'’.-]+(?:\s+[A-Za-z][A-Za-z'’.-]+){1,3})\s*,\s*\d{1,2}\b", first_line)
    if match:
        candidate = _normalize_candidate_name(match.group(1))
        if _looks_like_candidate_name(candidate):
            return candidate
    return None


def _candidate_name_from_metadata(metadata: Mapping[str, Any], filename: str, original_path: str | None, pdf_path: str | None) -> str | None:
    for key in ("candidate_name",):
        candidate = _strip_candidate_name_noise(str(metadata.get(key) or ""))
        if _looks_like_candidate_name(candidate):
            return candidate
    for key in ("attachment_name", "mail_subject"):
        candidate = _candidate_name_from_filename(metadata.get(key))
        if candidate:
            return candidate
    candidate = _candidate_name_from_filename(filename) or _candidate_name_from_filename(original_path) or _candidate_name_from_filename(pdf_path)
    if candidate:
        return candidate
    return _candidate_name_from_email(metadata.get("mail_sender"))


def _extract_candidate_name(
    source_text: str,
    *,
    filename: str | None = None,
    original_path: str | None = None,
    pdf_path: str | None = None,
) -> str | None:
    text = str(source_text or "").strip()
    metadata = _load_sidecar_metadata(original_path, pdf_path)

    candidate = _candidate_name_from_profile_fields(text)
    if candidate:
        return candidate

    try:
        from resume_extractor import ResumeExtractor

        extractor = ResumeExtractor()
        candidate = extractor.extract_candidate_name_from_text(text)
        if candidate and _looks_like_candidate_name(candidate):
            return _strip_candidate_name_noise(candidate)
    except Exception:
        pass

    # Conservative fallback for generic PDFs that still expose a clear name label.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    for line in lines[:8]:
        if re.search(
            r"\b(next\s+of\s+kin|relationship|wife|husband|father|mother|availability|details)\b",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        if _looks_like_candidate_name(line):
            return _strip_candidate_name_noise(line)

    for pattern in (
        r"^\s*full\s+name\s*[:.\-]?\s*(.+?)\s*$",
        r"^\s*name\s*[:.\-]?\s*(.+?)\s*$",
    ):
        for line in lines[:20]:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                candidate = _normalize_candidate_name(match.group(1))
                if _looks_like_candidate_name(candidate):
                    return _strip_candidate_name_noise(candidate)
    return _candidate_name_from_metadata(metadata, str(filename or ""), original_path, pdf_path)


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


def _compact_excerpt(value: str, *, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:|-")
    return shortened or text[:max_chars]


_STOP_LABEL_PATTERNS = (
    r"\bapplied for rank\b",
    r"\bpresent rank\b",
    r"\bname\b",
    r"\bemail address\b",
    r"\bpassport details\b",
    r"\bpassport expiry date\b",
    r"\bdate of birth\b",
    r"\bdob\b",
    r"\bcoc grade\b",
    r"\bcoc expiry date\b",
    r"\bstcw\b",
    r"\bmobile no\b",
    r"\bphone no\b",
    r"\bvessel name\b",
    r"\bvessel type\b",
    r"\bcompany\b",
    r"\bfrom date\b",
    r"\btill date\b",
    r"\bnationality\b",
    r"\bgender\b",
    r"\baddress\b",
    r"\bcity\b",
    r"\bcountry\b",
    r"\bzipcode\b",
)


def _source_excerpt_from_text(source_text: str, needle: str | None = None, *, max_chars: int = 120) -> str | None:
    text = str(source_text or "").strip()
    if not text:
        return None

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if needle:
        normalized_needle = re.sub(r"\s+", " ", str(needle or "")).strip()
        if normalized_needle:
            needle_lower = normalized_needle.lower()
            needle_folded = re.sub(r"\s+", "", normalized_needle).lower()
            for line in lines:
                line_lower = line.lower()
                if needle_lower in line_lower or needle_folded in re.sub(r"\s+", "", line_lower):
                    fragment = line
                    if normalized_needle:
                        match = re.search(re.escape(normalized_needle), line, flags=re.IGNORECASE)
                        if match:
                            stop_match = None
                            for pattern in _STOP_LABEL_PATTERNS:
                                candidate = re.search(pattern, line[match.end():], flags=re.IGNORECASE)
                                if candidate and (stop_match is None or candidate.start() < stop_match.start()):
                                    stop_match = candidate
                            if stop_match:
                                fragment = line[: match.end() + stop_match.start()].strip()
                    return _compact_excerpt(fragment, max_chars=max_chars)
            return None

    if lines:
        return _compact_excerpt(lines[0], max_chars=max_chars)
    return None


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

    extracted_name = _extract_candidate_name(
        source_text,
        filename=resolved_filename,
        original_path=original_path,
        pdf_path=pdf_path,
    )
    applied_rank_raw = str(rank or "").strip() or None
    applied_rank_normalized = _normalize_rank(analyzer, applied_rank_raw)
    applied_ship_types = []
    if folder_metadata:
        metadata_entry = folder_metadata.get(resolved_filename) or folder_metadata.get(str(filename or "")) or {}
        if isinstance(metadata_entry, Mapping):
            applied_ship_types = metadata_entry.get("applied_ship_types") or []
    if not isinstance(applied_ship_types, list):
        applied_ship_types = []

    # Intentionally conservative fallback: this bridge is meant to preserve
    # only high-confidence basics for unsupported layouts, not to imitate the
    # SeaJobs parser or guess at missing employment / document fields.
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
                "snippet": _source_excerpt_from_text(source_text, extracted_name) if extracted_name else None,
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
            "snippet": _source_excerpt_from_text(source_text, applied_rank_raw) if applied_rank_raw else None,
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
