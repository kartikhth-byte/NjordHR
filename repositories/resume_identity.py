from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


VERIFIED_CONTENT_HASH_PROVENANCE = "verified_content_hash"
LEGACY_PATH_FALLBACK_PROVENANCE = "legacy_path_fallback"
EMPTY_INPUT_PROVENANCE = "empty_input"
LEGACY_UNKNOWN_PROVENANCE = "legacy_unknown"


@dataclass(frozen=True)
class ResumeIdentity:
    """Legacy resume identifier plus provenance for safe future identity linking."""

    resume_id: str
    alias_provenance: str
    content_hash: str = ""

    @property
    def is_authoritative_content_alias(self) -> bool:
        return (
            self.alias_provenance == VERIFIED_CONTENT_HASH_PROVENANCE
            and bool(self.content_hash)
            and self.resume_id == self.content_hash
        )


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
    return stable_resume_identity(file_path).resume_id


def stable_resume_identity(file_path: Any) -> ResumeIdentity:
    content_hash = file_bytes_sha1(file_path)
    if content_hash:
        return ResumeIdentity(
            resume_id=content_hash,
            alias_provenance=VERIFIED_CONTENT_HASH_PROVENANCE,
            content_hash=content_hash,
        )

    normalized = _normalize_path_text(file_path)
    if not normalized:
        return ResumeIdentity(
            resume_id=hashlib.sha1(b"").hexdigest(),
            alias_provenance=EMPTY_INPUT_PROVENANCE,
        )
    return ResumeIdentity(
        resume_id=hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest(),
        alias_provenance=LEGACY_PATH_FALLBACK_PROVENANCE,
    )


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
