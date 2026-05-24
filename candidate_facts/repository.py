"""Thin in-memory repository adapter for candidate facts workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, MutableSequence, Sequence

from .audit import build_candidate_resume_facts_audit_metadata
from .orchestrator import build_candidate_facts_v1
from .persistence import (
    persist_candidate_resume_facts,
    resolve_candidate_resume_facts_for_replay,
)
from .validation_cache import CandidateFactsValidationCache


@dataclass
class CandidateFactsRepository:
    """Small in-memory repository for building, persisting, and replaying facts rows."""

    rows: list[Dict[str, Any]] = field(default_factory=list)
    validation_cache_dir: str | None = None
    validation_cache: CandidateFactsValidationCache | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.validation_cache is None and self.validation_cache_dir:
            self.validation_cache = CandidateFactsValidationCache(base_dir=self.validation_cache_dir)

    def build_candidate_facts(
        self,
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
        return build_candidate_facts_v1(
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

    def persist_candidate_facts(
        self,
        *,
        candidate_resume_id: str,
        resume_blob_id: str,
        candidate_facts: Mapping[str, Any],
        parser_version: str,
        facts_revision: str,
        row_id: str | None = None,
        acceptable_extraction_statuses: Iterable[str] | None = None,
        extraction_warnings: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        result = persist_candidate_resume_facts(
            self.rows,
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
            row_id=row_id,
            acceptable_extraction_statuses=acceptable_extraction_statuses,
            extraction_warnings=extraction_warnings,
        )
        self.rows = list(result["rows"])
        return result

    def replay_candidate_facts(
        self,
        *,
        candidate_resume_id: str,
        schema_version: str,
        parser_version: str,
        facts_revision: str,
        candidate_resume_facts_id: str | None = None,
    ) -> Dict[str, Any]:
        return resolve_candidate_resume_facts_for_replay(
            self.rows,
            candidate_resume_id=candidate_resume_id,
            schema_version=schema_version,
            parser_version=parser_version,
            facts_revision=facts_revision,
            candidate_resume_facts_id=candidate_resume_facts_id,
        )

    def audit_candidate_facts(
        self,
        *,
        candidate_resume_id: str,
        schema_version: str,
        parser_version: str,
        facts_revision: str,
        candidate_resume_facts_id: str | None = None,
    ) -> Dict[str, Any]:
        resolution = self.replay_candidate_facts(
            candidate_resume_id=candidate_resume_id,
            schema_version=schema_version,
            parser_version=parser_version,
            facts_revision=facts_revision,
            candidate_resume_facts_id=candidate_resume_facts_id,
        )
        return build_candidate_resume_facts_audit_metadata(
            resolution.get("row"),
            resolution=resolution,
        )

    def capture_candidate_facts_for_review(
        self,
        analyzer: Any,
        filename: str,
        rank: str,
        chunks: Any,
        *,
        candidate_resume_id: str,
        resume_blob_id: str,
        parser_version: str,
        facts_revision: str,
        original_path: str | None = None,
        text_cache: Mapping[str, str] | None = None,
        folder_metadata: Mapping[str, Any] | None = None,
        source_origin: str | None = None,
        detected_layout: str | None = None,
    ) -> Dict[str, Any]:
        candidate_facts = self.build_candidate_facts(
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
        review_item = self.capture_normalized_candidate_facts_for_review(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
        )["review_item"]
        return {
            "candidate_facts": candidate_facts,
            "review_item": review_item,
        }

    def capture_normalized_candidate_facts_for_review(
        self,
        *,
        candidate_resume_id: str,
        resume_blob_id: str,
        candidate_facts: Mapping[str, Any],
        parser_version: str,
        facts_revision: str,
    ) -> Dict[str, Any]:
        if self.validation_cache is None:
            raise RuntimeError("validation cache is not configured")
        review_item = self.validation_cache.capture_candidate_facts_for_review(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
        )
        return {
            "candidate_facts": candidate_facts,
            "review_item": review_item,
        }

    def list_candidate_facts_review_items(self, *, review_status: str | None = None) -> list[Dict[str, Any]]:
        if self.validation_cache is None:
            return []
        return self.validation_cache.list_review_items(review_status=review_status)

    def approve_candidate_facts_review_item(
        self,
        record_id: str,
        *,
        reviewed_by: str = "",
        review_notes: str = "",
    ) -> Dict[str, Any]:
        if self.validation_cache is None:
            raise RuntimeError("validation cache is not configured")
        return self.validation_cache.approve_review_item(
            record_id,
            reviewed_by=reviewed_by,
            review_notes=review_notes,
        )

    def reject_candidate_facts_review_item(
        self,
        record_id: str,
        *,
        reviewed_by: str = "",
        review_notes: str = "",
    ) -> Dict[str, Any]:
        if self.validation_cache is None:
            raise RuntimeError("validation cache is not configured")
        return self.validation_cache.reject_review_item(
            record_id,
            reviewed_by=reviewed_by,
            review_notes=review_notes,
        )

    def promote_candidate_facts_review_item(
        self,
        record_id: str,
        *,
        acceptable_extraction_statuses: Iterable[str] | None = None,
    ) -> Dict[str, Any]:
        if self.validation_cache is None:
            raise RuntimeError("validation cache is not configured")
        result = self.validation_cache.promote_review_item_to_persisted(
            self.rows,
            record_id,
            acceptable_extraction_statuses=acceptable_extraction_statuses,
        )
        self.rows = list(result["persist"]["rows"])
        return result

    def build_persist_replay_audit(
        self,
        analyzer: Any,
        filename: str,
        rank: str,
        chunks: Any,
        *,
        candidate_resume_id: str,
        resume_blob_id: str,
        parser_version: str,
        facts_revision: str,
        original_path: str | None = None,
        text_cache: Mapping[str, str] | None = None,
        folder_metadata: Mapping[str, Any] | None = None,
        source_origin: str | None = None,
        detected_layout: str | None = None,
        acceptable_extraction_statuses: Iterable[str] | None = None,
        extraction_warnings: Sequence[str] | None = None,
        review_capture_callback: Callable[[Dict[str, Any], Dict[str, Any]], Any] | None = None,
    ) -> Dict[str, Any]:
        candidate_facts = self.build_candidate_facts(
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
        review_capture = None
        review_capture_error = ""
        if callable(review_capture_callback):
            capture_context = {
                "candidate_resume_id": candidate_resume_id,
                "resume_blob_id": resume_blob_id,
                "parser_version": parser_version,
                "facts_revision": facts_revision,
                "original_path": original_path,
                "source_origin": source_origin,
                "detected_layout": detected_layout,
            }
            try:
                review_capture = review_capture_callback(deepcopy(candidate_facts), capture_context)
            except Exception as exc:
                review_capture_error = f"{type(exc).__name__}: {exc}"
        persist_result = self.persist_candidate_facts(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
            acceptable_extraction_statuses=acceptable_extraction_statuses,
            extraction_warnings=extraction_warnings,
        )
        resolution = self.replay_candidate_facts(
            candidate_resume_id=candidate_resume_id,
            schema_version=str(candidate_facts.get("schema_version") or "candidate_facts.v1"),
            parser_version=parser_version,
            facts_revision=facts_revision,
            candidate_resume_facts_id=str(persist_result["row"].get("id") or ""),
        )
        audit = build_candidate_resume_facts_audit_metadata(
            resolution.get("row"),
            resolution=resolution,
        )
        return {
            "candidate_facts": candidate_facts,
            "review_capture": review_capture,
            "review_capture_error": review_capture_error,
            "persist": persist_result,
            "replay": resolution,
            "audit": audit,
        }
