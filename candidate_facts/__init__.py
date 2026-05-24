"""Candidate-facts foundation package."""

from .schema import (
    CANDIDATE_FACTS_SCHEMA_VERSION,
    CandidateFactsValidationResult,
    normalize_candidate_facts_v1,
    validate_candidate_facts_v1,
)
from .audit import build_candidate_resume_facts_audit_metadata
from .orchestrator import build_candidate_facts_v1
from .repository import CandidateFactsRepository
from .storage import (
    build_candidate_resume_facts_identity,
    build_transient_facts_id,
    ensure_single_current_candidate_resume_facts_row,
    select_current_candidate_resume_facts_rows,
)
from .persistence import (
    build_candidate_resume_facts_row,
    build_candidate_resume_facts_row_id,
    persist_candidate_resume_facts,
    resolve_candidate_resume_facts_for_replay,
    select_candidate_resume_facts_row_by_identity,
    select_current_candidate_resume_facts_row,
    upsert_candidate_resume_facts_row,
)
from .validation_cache import (
    CandidateFactsValidationCache,
    build_candidate_facts_review_id,
    candidate_facts_validation_cache_base_dir,
)

__all__ = [
    "CANDIDATE_FACTS_SCHEMA_VERSION",
    "CandidateFactsValidationResult",
    "build_candidate_resume_facts_identity",
    "build_candidate_resume_facts_audit_metadata",
    "build_candidate_resume_facts_row",
    "build_candidate_resume_facts_row_id",
    "CandidateFactsValidationCache",
    "build_candidate_facts_v1",
    "build_candidate_facts_review_id",
    "CandidateFactsRepository",
    "build_transient_facts_id",
    "candidate_facts_validation_cache_base_dir",
    "ensure_single_current_candidate_resume_facts_row",
    "persist_candidate_resume_facts",
    "resolve_candidate_resume_facts_for_replay",
    "normalize_candidate_facts_v1",
    "select_candidate_resume_facts_row_by_identity",
    "select_current_candidate_resume_facts_row",
    "select_current_candidate_resume_facts_rows",
    "upsert_candidate_resume_facts_row",
    "validate_candidate_facts_v1",
]
