from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


def _normalize_path_text(file_path: Any) -> str:
    raw = str(file_path or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/").rstrip("/")
    normalized = re.sub(r"/+", "/", normalized)
    return normalized


def file_bytes_sha1(file_path: Any) -> str:
    path = Path(str(file_path or "").strip())
    try:
        if not path.exists() or not path.is_file():
            return ""
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return ""


def stable_resume_id(file_path: Any) -> str:
    content_hash = file_bytes_sha1(file_path)
    if content_hash:
        return content_hash

    normalized = _normalize_path_text(file_path)
    if not normalized:
        return hashlib.sha1(b"").hexdigest()
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()


def normalize_text_signature(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def build_resume_fingerprint(
    *,
    source_path: Any = None,
    raw_text: str | None = None,
    chunks: Iterable[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    fallback_id: str | None = None,
) -> str:
    metadata = metadata if isinstance(metadata, Mapping) else {}

    for key in ("resume_fingerprint", "content_hash"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value.lower()

    if source_path:
        source_hash = file_bytes_sha1(source_path)
        if source_hash:
            return source_hash

    text_parts: list[str] = []
    if raw_text:
        text_parts.append(normalize_text_signature(raw_text))

    for chunk in chunks or []:
        chunk_meta = chunk.get("metadata") if isinstance(chunk, Mapping) else {}
        if not isinstance(chunk_meta, Mapping):
            continue
        chunk_text = str(chunk_meta.get("raw_text") or "").strip()
        if chunk_text:
            text_parts.append(normalize_text_signature(chunk_text))

    if text_parts:
        joined = "\n".join(part for part in text_parts if part)
        if joined:
            return hashlib.sha1(joined.encode("utf-8", "ignore")).hexdigest()

    fallback_text = _normalize_path_text(fallback_id or source_path)
    if fallback_text:
        return hashlib.sha1(fallback_text.encode("utf-8", "ignore")).hexdigest()

    return ""
