"""Thin in-memory repository adapter for candidate facts workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, MutableSequence, Sequence

from .audit import build_candidate_resume_facts_audit_metadata
from .orchestrator import build_candidate_facts_v1
from .persistence import (
    persist_candidate_resume_facts,
    resolve_candidate_resume_facts_for_replay,
)


@dataclass
class CandidateFactsRepository:
    """Small in-memory repository for building, persisting, and replaying facts rows."""

    rows: list[Dict[str, Any]] = field(default_factory=list)

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
            "persist": persist_result,
            "replay": resolution,
            "audit": audit,
        }
