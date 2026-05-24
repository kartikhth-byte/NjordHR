import hashlib
import json
import os

from repositories.feedback_repo import FeedbackRepo
from repositories.registry_repo import RegistryRepo
from repositories.dual_write_state_store import DualWriteStateStore


class DualWriteAIRegistryRepo(RegistryRepo):
    def __init__(self, primary_repo, secondary_repo, idempotency_db_path, read_repo=None):
        self.primary_repo = primary_repo
        self.secondary_repo = secondary_repo
        self.read_repo = read_repo or primary_repo
        self._state = DualWriteStateStore(idempotency_db_path)

    @staticmethod
    def _canonical_key(file_path, last_modified, resume_id):
        payload = {
            "file_path": str(file_path),
            "last_modified": float(last_modified),
            "resume_id": str(resume_id),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"ai_file_registry:{digest}"

    def generate_resume_id(self, file_path):
        return self.primary_repo.generate_resume_id(file_path)

    def needs_processing(self, file_path, last_modified):
        preferred = self.read_repo.needs_processing(file_path, last_modified)
        if self.read_repo is self.secondary_repo and preferred:
            primary = self.primary_repo.needs_processing(file_path, last_modified)
            if not primary:
                return False
        return preferred

    def upsert_file_record(self, file_path, last_modified, resume_id):
        key = self._canonical_key(file_path, last_modified, resume_id)
        if self._state.is_complete(key):
            print(f"[DUAL-WRITE] Skipping duplicate ai_file_registry write for key={key}")
            return True

        if not self._state.is_primary_done(key):
            primary_ok = self.primary_repo.upsert_file_record(file_path, last_modified, resume_id)
            if primary_ok is False:
                return False
            self._state.mark_primary_done(key)

        if not self._state.is_secondary_done(key):
            secondary_ok = self.secondary_repo.upsert_file_record(file_path, last_modified, resume_id)
            if secondary_ok is False:
                print(f"[DUAL-WRITE] Secondary ai_file_registry write failed for key={key}")
                return True
            self._state.mark_secondary_done(key)
        return True

    def get_resume_id(self, file_path):
        preferred = self.read_repo.get_resume_id(file_path)
        if self.read_repo is self.secondary_repo and not str(preferred or "").strip():
            return self.primary_repo.get_resume_id(file_path)
        return preferred

    def get_csv_stats(self):
        stats = {}
        getter = getattr(self.primary_repo, "get_csv_stats", None)
        if callable(getter):
            stats = getter()
        if isinstance(stats, dict):
            stats = dict(stats)
            stats["dual_write_idempotency_keys"] = self._state.count()
            stats["write_mode"] = "dual_write"
            stats["read_mode"] = type(self.read_repo).__name__
        return stats


class DualWriteAIFeedbackStore(FeedbackRepo):
    def __init__(self, primary_repo, secondary_repo, idempotency_db_path, read_repo=None):
        self.primary_repo = primary_repo
        self.secondary_repo = secondary_repo
        self.read_repo = read_repo or primary_repo
        self._state = DualWriteStateStore(idempotency_db_path)

    @staticmethod
    def _canonical_key(filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes):
        payload = {
            "filename": str(filename),
            "query": str(query),
            "llm_decision": str(llm_decision),
            "llm_reason": str(llm_reason),
            "llm_confidence": llm_confidence,
            "user_decision": str(user_decision),
            "user_notes": str(user_notes),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"ai_feedback:{digest}"

    def add_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes=""):
        key = self._canonical_key(filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes)
        if self._state.is_complete(key):
            print(f"[DUAL-WRITE] Skipping duplicate ai_feedback write for key={key}")
            return True

        if not self._state.is_primary_done(key):
            primary_ok = self.primary_repo.add_feedback(
                filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes
            )
            if primary_ok is False:
                return False
            self._state.mark_primary_done(key)

        if not self._state.is_secondary_done(key):
            secondary_ok = self.secondary_repo.add_feedback(
                filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes
            )
            if secondary_ok is False:
                print(f"[DUAL-WRITE] Secondary ai_feedback write failed for key={key}")
                return True
            self._state.mark_secondary_done(key)
        return True

    def get_recent_feedback(self, query, limit=5):
        preferred = self.read_repo.get_recent_feedback(query, limit=limit)
        if self.read_repo is self.secondary_repo and not preferred:
            return self.primary_repo.get_recent_feedback(query, limit=limit)
        return preferred
