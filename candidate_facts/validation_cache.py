"""Portable local validation cache for extracted candidate facts.

This cache stores normalized candidate facts as reviewable JSONL records before
they are promoted into the repository's persisted row set. It is intentionally
OS-portable and keeps raw PDF text out of the cached payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from .persistence import persist_candidate_resume_facts
from .review_summary import build_candidate_facts_review_summary
from .schema import normalize_candidate_facts_v1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_review_alignment_state(
    review_alignment_report: Mapping[str, Any] | None,
    review_alignment_status: str | None,
) -> tuple[bool, str]:
    report_present = isinstance(review_alignment_report, Mapping) and bool(review_alignment_report)
    normalized_status = str(review_alignment_status or "").strip().lower()
    if normalized_status not in {"match", "mismatch"}:
        normalized_status = "not_checked"
    if not report_present:
        return False, "not_checked"
    return normalized_status in {"match", "mismatch"}, normalized_status


def candidate_facts_validation_cache_base_dir(home: str | None = None, system: str | None = None) -> str:
    home_dir = os.path.abspath(os.path.expanduser(home or "~"))
    system_name = (system or platform.system()).lower()
    if system_name == "darwin":
        return os.path.join(home_dir, "Library", "Application Support", "NjordHR", "candidate_facts")
    if system_name == "windows":
        appdata = os.getenv("APPDATA", home_dir)
        return os.path.join(appdata, "NjordHR", "candidate_facts")
    return os.path.join(home_dir, ".config", "njordhr", "candidate_facts")


def build_candidate_facts_review_id(
    *,
    candidate_resume_id: str,
    resume_blob_id: str,
    schema_version: str,
    parser_version: str,
    facts_revision: str,
    candidate_facts_hash: str,
) -> str:
    payload = "::".join(
        [
            candidate_resume_id,
            resume_blob_id,
            schema_version,
            parser_version,
            facts_revision,
            candidate_facts_hash,
        ]
    )
    return f"candidate_facts_review:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def build_candidate_facts_content_hash(candidate_facts: Mapping[str, Any]) -> str:
    payload = json.dumps(candidate_facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sort_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("updated_at") or record.get("created_at") or ""),
        str(record.get("candidate_resume_id") or ""),
        str(record.get("id") or ""),
    )


class CandidateFactsValidationCache:
    """File-backed queue of candidate facts awaiting human review."""

    filename = "validation_queue.jsonl"

    def __init__(self, base_dir: str | None = None):
        self.base_dir = os.path.abspath(os.path.expanduser(base_dir or candidate_facts_validation_cache_base_dir()))
        self.path = os.path.join(self.base_dir, self.filename)
        self._lock = threading.RLock()

    def _load_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _save_records(self, records: Iterable[Mapping[str, Any]]) -> None:
        os.makedirs(self.base_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="candidate_facts_validation_", suffix=".jsonl", dir=self.base_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, sort_keys=True))
                    fh.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    def _write_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            records = self._load_records()
            record_id = str(record.get("id") or "")
            records = [existing for existing in records if str(existing.get("id") or "") != record_id]
            records.append(dict(record))
            records.sort(key=_sort_key, reverse=True)
            self._save_records(records)
            return dict(record)

    def _write_record_and_supersede_pending(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            records = self._load_records()
            record_id = str(record.get("id") or "")
            candidate_resume_id = str(record.get("candidate_resume_id") or "")
            resume_blob_id = str(record.get("resume_blob_id") or "")
            now = _utc_now_iso()
            updated: List[Dict[str, Any]] = []
            for existing in records:
                existing_record = dict(existing)
                if str(existing_record.get("id") or "") == record_id:
                    continue
                same_resume = (
                    candidate_resume_id
                    and resume_blob_id
                    and str(existing_record.get("candidate_resume_id") or "") == candidate_resume_id
                    and str(existing_record.get("resume_blob_id") or "") == resume_blob_id
                )
                if same_resume and str(existing_record.get("review_status") or "") == "pending_review":
                    existing_record["review_status"] = "superseded"
                    existing_record["review_notes"] = "Superseded by newer extraction review item."
                    existing_record["reviewed_at"] = now
                    existing_record["updated_at"] = now
                updated.append(existing_record)
            updated.append(dict(record))
            updated.sort(key=_sort_key, reverse=True)
            self._save_records(updated)
            return dict(record)

    def _update_record(self, record_id: str, patch: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            records = self._load_records()
            updated: List[Dict[str, Any]] = []
            found: Dict[str, Any] | None = None
            for record in records:
                if str(record.get("id") or "") == record_id:
                    merged = dict(record)
                    merged.update(dict(patch))
                    merged.setdefault("id", record_id)
                    merged["updated_at"] = _utc_now_iso()
                    found = merged
                    updated.append(merged)
                else:
                    updated.append(dict(record))
            if found is None:
                raise KeyError(record_id)
            updated.sort(key=_sort_key, reverse=True)
            self._save_records(updated)
            return found

    def capture_candidate_facts_for_review(
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
        review_alignment_mismatches: List[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        normalized = normalize_candidate_facts_v1(candidate_facts)
        candidate_facts_hash = build_candidate_facts_content_hash(normalized)
        review_alignment_report_value = deepcopy(review_alignment_report) if isinstance(review_alignment_report, Mapping) and review_alignment_report else None
        review_alignment_checked, normalized_review_alignment_status = _normalize_review_alignment_state(
            review_alignment_report_value,
            review_alignment_status,
        )
        record = {
            "id": build_candidate_facts_review_id(
                candidate_resume_id=candidate_resume_id,
                resume_blob_id=resume_blob_id,
                schema_version=str(normalized.get("schema_version") or "candidate_facts.v1"),
                parser_version=parser_version,
                facts_revision=facts_revision,
                candidate_facts_hash=candidate_facts_hash,
            ),
            "candidate_resume_id": candidate_resume_id,
            "resume_blob_id": resume_blob_id,
            "candidate_id": str((normalized.get("source") or {}).get("candidate_id") or candidate_resume_id),
            "schema_version": str(normalized.get("schema_version") or "candidate_facts.v1"),
            "parser_version": parser_version,
            "facts_revision": facts_revision,
            "candidate_facts_hash": candidate_facts_hash,
            "review_status": "pending_review",
            "persistence_status": "not_persisted",
            "candidate_facts": deepcopy(normalized),
            "validation": deepcopy(normalized.get("validation") or {}),
            "extraction_status": str((normalized.get("extraction") or {}).get("status") or "failed"),
            "review_notes": "",
            "reviewed_by": "",
            "reviewed_at": "",
            "persistence_row_id": "",
            "supabase_persistence_status": "not_configured",
            "supabase_row_id": "",
            "supabase_error": "",
            "review_alignment_report": review_alignment_report_value,
            "review_alignment_checked": review_alignment_checked,
            "review_alignment_status": normalized_review_alignment_status,
            "review_alignment_mismatch_count": int(review_alignment_mismatch_count or 0),
            "review_alignment_mismatches": deepcopy(review_alignment_mismatches or []),
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        return self._write_record_and_supersede_pending(record)

    def _summarize_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        candidate_facts = record.get("candidate_facts") if isinstance(record.get("candidate_facts"), Mapping) else {}
        source = candidate_facts.get("source") if isinstance(candidate_facts, Mapping) else {}
        identity = candidate_facts.get("identity") if isinstance(candidate_facts, Mapping) else {}
        rank = candidate_facts.get("rank") if isinstance(candidate_facts, Mapping) else {}
        extraction = candidate_facts.get("extraction") if isinstance(candidate_facts, Mapping) else {}
        warnings = list((extraction or {}).get("warnings") or [])
        evidence = list(candidate_facts.get("evidence") or []) if isinstance(candidate_facts, Mapping) else []
        review_summary = build_candidate_facts_review_summary(candidate_facts if isinstance(candidate_facts, Mapping) else {})
        return {
            "id": record.get("id"),
            "candidate_resume_id": record.get("candidate_resume_id"),
            "resume_blob_id": record.get("resume_blob_id"),
            "candidate_id": record.get("candidate_id"),
            "schema_version": record.get("schema_version"),
            "parser_version": record.get("parser_version"),
            "facts_revision": record.get("facts_revision"),
            "candidate_facts_hash": record.get("candidate_facts_hash"),
            "review_status": record.get("review_status"),
            "persistence_status": record.get("persistence_status"),
            "extraction_status": record.get("extraction_status"),
            "reviewed_by": record.get("reviewed_by"),
            "reviewed_at": record.get("reviewed_at"),
            "persistence_row_id": record.get("persistence_row_id"),
            "supabase_persistence_status": record.get("supabase_persistence_status"),
            "supabase_row_id": record.get("supabase_row_id"),
            "supabase_error": record.get("supabase_error"),
            "review_alignment_checked": bool(record.get("review_alignment_checked")),
            "review_alignment_status": record.get("review_alignment_status") or "not_checked",
            "review_alignment_mismatch_count": record.get("review_alignment_mismatch_count") or 0,
            "review_alignment_mismatches": deepcopy(record.get("review_alignment_mismatches") or []),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "candidate_facts_summary": {
                "candidate_name": ((identity or {}).get("candidate_name") or {}).get("value") or "",
                "rank": ((rank or {}).get("value") or ""),
                "source_origin": (source or {}).get("source_origin") or "",
                "detected_layout": (source or {}).get("detected_layout") or "",
                "warning_count": len(warnings),
                "evidence_count": len(evidence),
                "extraction_status": str((extraction or {}).get("status") or record.get("extraction_status") or "failed"),
                "supabase_persistence_status": record.get("supabase_persistence_status") or "not_configured",
                "key_fact_count": review_summary.get("key_fact_count", 0),
                "missing_key_fact_count": review_summary.get("missing_key_fact_count", 0),
                "low_confidence_key_fact_count": review_summary.get("low_confidence_key_fact_count", 0),
                "review_alignment_checked": bool(record.get("review_alignment_checked")),
                "review_alignment_status": record.get("review_alignment_status") or "not_checked",
                "review_alignment_mismatch_count": record.get("review_alignment_mismatch_count") or 0,
            },
            "candidate_facts_review_summary": review_summary,
        }

    def list_review_items(self, *, review_status: str | None = None) -> List[Dict[str, Any]]:
        with self._lock:
            records = [dict(record) for record in self._load_records()]
        if review_status:
            records = [record for record in records if str(record.get("review_status") or "") == review_status]
        return sorted(records, key=_sort_key, reverse=True)

    def list_review_item_summaries(self, *, review_status: str | None = None) -> List[Dict[str, Any]]:
        return [self._summarize_record(record) for record in self.list_review_items(review_status=review_status)]

    def get_review_item(self, record_id: str) -> Dict[str, Any] | None:
        with self._lock:
            for record in self._load_records():
                if str(record.get("id") or "") == record_id:
                    item = dict(record)
                    item["candidate_facts_review_summary"] = build_candidate_facts_review_summary(
                        item.get("candidate_facts") if isinstance(item.get("candidate_facts"), Mapping) else {}
                    )
                    return item
        return None

    def approve_review_item(
        self,
        record_id: str,
        *,
        reviewed_by: str = "",
        review_notes: str = "",
    ) -> Dict[str, Any]:
        record = self.get_review_item(record_id)
        if record is None:
            raise KeyError(record_id)
        if str(record.get("review_status") or "") != "pending_review":
            raise ValueError("only pending review items can be approved")
        return self._update_record(
            record_id,
            {
                "review_status": "approved",
                "reviewed_by": reviewed_by,
                "review_notes": review_notes,
                "reviewed_at": _utc_now_iso(),
            },
        )

    def reject_review_item(
        self,
        record_id: str,
        *,
        reviewed_by: str = "",
        review_notes: str = "",
    ) -> Dict[str, Any]:
        record = self.get_review_item(record_id)
        if record is None:
            raise KeyError(record_id)
        if str(record.get("review_status") or "") != "pending_review":
            raise ValueError("only pending review items can be rejected")
        return self._update_record(
            record_id,
            {
                "review_status": "rejected",
                "persistence_status": "not_persisted",
                "reviewed_by": reviewed_by,
                "review_notes": review_notes,
                "reviewed_at": _utc_now_iso(),
            },
        )

    def promote_review_item_to_persisted(
        self,
        rows: List[Mapping[str, Any]],
        record_id: str,
        *,
        acceptable_extraction_statuses: Iterable[str] | None = None,
    ) -> Dict[str, Any]:
        record = self.get_review_item(record_id)
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

        persist_result = persist_candidate_resume_facts(
            rows,
            candidate_resume_id=str(record.get("candidate_resume_id") or ""),
            resume_blob_id=str(record.get("resume_blob_id") or ""),
            candidate_facts=record.get("candidate_facts") or {},
            parser_version=str(record.get("parser_version") or ""),
            facts_revision=str(record.get("facts_revision") or ""),
            candidate_facts_hash=str(record.get("candidate_facts_hash") or ""),
            acceptable_extraction_statuses=acceptable_extraction_statuses,
            extraction_warnings=list((record.get("candidate_facts") or {}).get("extraction", {}).get("warnings") or []),
        )
        persistence_status = "persisted" if persist_result.get("committed") else "persisted_non_current"
        updated = self._update_record(
            record_id,
            {
                "persistence_status": persistence_status,
                "persistence_row_id": str((persist_result.get("row") or {}).get("id") or ""),
            },
        )
        return {
            "review_item": updated,
            "persist": persist_result,
        }
