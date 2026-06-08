"""Thin in-memory repository adapter for candidate facts workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Dict, Iterable, Mapping, MutableSequence, Sequence

from .audit import build_candidate_resume_facts_audit_metadata
from .local_row_store import load_candidate_resume_facts_rows, save_candidate_resume_facts_rows
from .orchestrator import build_candidate_facts_v1
from .persistence import (
    persist_candidate_resume_facts,
    resolve_candidate_resume_facts_for_replay,
    select_candidate_resume_facts_row_by_identity,
)
from .supabase_store import SupabaseCandidateFactsStore
from .validation_cache import CandidateFactsValidationCache


@dataclass
class CandidateFactsRepository:
    """Small in-memory repository for building, persisting, and replaying facts rows."""

    rows: list[Dict[str, Any]] = field(default_factory=list)
    validation_cache_dir: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_timeout_seconds: int = 20
    validation_cache: CandidateFactsValidationCache | None = field(default=None, repr=False, compare=False)
    supabase_store: SupabaseCandidateFactsStore | None = field(default=None, repr=False, compare=False)
    _rows_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.validation_cache is None and self.validation_cache_dir:
            self.validation_cache = CandidateFactsValidationCache(base_dir=self.validation_cache_dir)
        if (
            self.supabase_store is None
            and self.supabase_url
            and self.supabase_service_role_key
        ):
            self.supabase_store = SupabaseCandidateFactsStore(
                supabase_url=self.supabase_url,
                service_role_key=self.supabase_service_role_key,
                timeout_seconds=self.supabase_timeout_seconds,
            )
        if self.validation_cache_dir:
            with self._rows_lock:
                self.rows = load_candidate_resume_facts_rows(self.validation_cache_dir)

    def _save_rows(self) -> None:
        if self.validation_cache_dir:
            save_candidate_resume_facts_rows(self.validation_cache_dir, self.rows)

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
        with self._rows_lock:
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
            self._save_rows()
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
        with self._rows_lock:
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
        review_alignment_report: Mapping[str, Any] | None = None,
        review_alignment_status: str | None = None,
        review_alignment_mismatch_count: int | None = None,
        review_alignment_mismatches: Sequence[Mapping[str, Any]] | None = None,
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
            review_alignment_report=review_alignment_report,
            review_alignment_status=review_alignment_status,
            review_alignment_mismatch_count=review_alignment_mismatch_count,
            review_alignment_mismatches=review_alignment_mismatches,
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
        review_alignment_report: Mapping[str, Any] | None = None,
        review_alignment_status: str | None = None,
        review_alignment_mismatch_count: int | None = None,
        review_alignment_mismatches: Sequence[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        if self.validation_cache is None:
            raise RuntimeError("validation cache is not configured")
        review_item = self.validation_cache.capture_candidate_facts_for_review(
            candidate_resume_id=candidate_resume_id,
            resume_blob_id=resume_blob_id,
            candidate_facts=candidate_facts,
            parser_version=parser_version,
            facts_revision=facts_revision,
            review_alignment_report=review_alignment_report,
            review_alignment_status=review_alignment_status,
            review_alignment_mismatch_count=review_alignment_mismatch_count,
            review_alignment_mismatches=review_alignment_mismatches,
        )
        return {
            "candidate_facts": candidate_facts,
            "review_item": review_item,
        }

    def list_candidate_facts_review_items(self, *, review_status: str | None = None) -> list[Dict[str, Any]]:
        if self.validation_cache is None:
            return []
        return self.validation_cache.list_review_items(review_status=review_status)

    def list_candidate_facts_review_summaries(self, *, review_status: str | None = None) -> list[Dict[str, Any]]:
        if self.validation_cache is None:
            return []
        return self.validation_cache.list_review_item_summaries(review_status=review_status)

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
        with self._rows_lock:
            record = self.validation_cache.get_review_item(record_id)
            if record is None:
                raise KeyError(record_id)
            if str(record.get("review_status") or "") != "approved":
                raise ValueError("candidate facts must be approved before persistence")
            alignment_status = str(record.get("review_alignment_status") or "").strip().lower()
            alignment_checked = bool(record.get("review_alignment_checked"))
            if not alignment_checked:
                raise ValueError("candidate facts review must include an explicit alignment report before persistence")
            if alignment_status != "match":
                mismatch_count = int(record.get("review_alignment_mismatch_count") or 0)
                if alignment_status == "mismatch":
                    raise ValueError(
                        f"candidate facts review diverges from live search facts for {mismatch_count} match-affecting field(s)"
                    )
                raise ValueError("candidate facts review must be explicitly marked as a matching alignment before persistence")

            existing_row = select_candidate_resume_facts_row_by_identity(
                self.rows,
                candidate_resume_id=str(record.get("candidate_resume_id") or ""),
                schema_version=str(record.get("schema_version") or ""),
                parser_version=str(record.get("parser_version") or ""),
                facts_revision=str(record.get("facts_revision") or ""),
                candidate_facts_hash=str(record.get("candidate_facts_hash") or ""),
            )
            if existing_row is not None and bool(existing_row.get("is_current_for_resume")):
                persist_result = {
                    "rows": list(self.rows),
                    "row": dict(existing_row),
                    "current_row": dict(existing_row),
                    "committed": True,
                }
            else:
                persist_result = persist_candidate_resume_facts(
                    self.rows,
                    candidate_resume_id=str(record.get("candidate_resume_id") or ""),
                    resume_blob_id=str(record.get("resume_blob_id") or ""),
                    candidate_facts=record.get("candidate_facts") or {},
                    parser_version=str(record.get("parser_version") or ""),
                    facts_revision=str(record.get("facts_revision") or ""),
                    candidate_facts_hash=str(record.get("candidate_facts_hash") or ""),
                    acceptable_extraction_statuses=acceptable_extraction_statuses,
                    extraction_warnings=list((record.get("candidate_facts") or {}).get("extraction", {}).get("warnings") or []),
                )
                self.rows = list(persist_result["rows"])
                self._save_rows()

            persistence_status = "persisted" if persist_result.get("committed") else "persisted_non_current"
            supabase_result = {
                "status": "not_configured",
                "row_id": "",
                "error": "",
            }
            warnings: list[str] = []
            if persist_result.get("committed") and self.supabase_store is not None:
                try:
                    supabase_result = self.supabase_store.promote_candidate_resume_facts_row(persist_result["row"])
                    if not bool(supabase_result.get("committed")):
                        supabase_result = {
                            "status": "not_current",
                            "row_id": str((supabase_result.get("row") or {}).get("id") or ""),
                            "error": "",
                        }
                        warnings.append("Supabase candidate facts row was written but not marked current.")
                except Exception as exc:
                    supabase_result = {
                        "status": "failed",
                        "row_id": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    warnings.append(f"Supabase candidate facts sync failed: {exc}")
            elif persist_result.get("committed") and self.supabase_store is None:
                supabase_result = {
                    "status": "not_configured",
                    "row_id": "",
                    "error": "",
                }
            elif not persist_result.get("committed") and self.supabase_store is not None:
                supabase_result = {
                    "status": "skipped_non_current",
                    "row_id": "",
                    "error": "",
                }
            updated_review_item = self.validation_cache._update_record(
                record_id,
                {
                    "persistence_status": persistence_status,
                    "persistence_row_id": str((persist_result.get("row") or {}).get("id") or ""),
                    "supabase_persistence_status": str(supabase_result.get("status") or "not_configured"),
                    "supabase_row_id": str(supabase_result.get("row_id") or ""),
                    "supabase_error": str(supabase_result.get("error") or ""),
                    "supabase_synced": str(supabase_result.get("status") or "") == "persisted",
                },
            )
            return {
                "review_item": updated_review_item,
                "persist": persist_result,
                "supabase": supabase_result,
                "supabase_synced": str(supabase_result.get("status") or "") == "persisted",
                "warnings": warnings,
            }

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
        with self._rows_lock:
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
