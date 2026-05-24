import sqlite3
import tempfile
import unittest

from repositories.candidate_event_repo import CandidateEventRepo
from repositories.dual_write_ai_store_repo import DualWriteAIRegistryRepo, DualWriteAIFeedbackStore
from repositories.registry_repo import RegistryRepo
from repositories.feedback_repo import FeedbackRepo


class _RegistryStub(RegistryRepo):
    def __init__(self, generated="resume-123", needs=True, resume_id="resume-123", fail_writes=False, fail_first_write=False):
        self.generated = generated
        self.needs = needs
        self.resume_id = resume_id
        self.fail_writes = fail_writes
        self.fail_first_write = fail_first_write
        self.upserts = []
        self.write_calls = 0

    def generate_resume_id(self, file_path):
        return self.generated

    def needs_processing(self, file_path, last_modified):
        return self.needs

    def upsert_file_record(self, file_path, last_modified, resume_id):
        self.write_calls += 1
        if self.fail_writes or (self.fail_first_write and self.write_calls == 1):
            return False
        self.upserts.append((file_path, last_modified, resume_id))
        return True

    def get_resume_id(self, file_path):
        return self.resume_id


class _FeedbackStub(FeedbackRepo):
    def __init__(self, rows=None, fail_writes=False, fail_first_write=False):
        self.rows = list(rows or [])
        self.writes = []
        self.fail_writes = fail_writes
        self.fail_first_write = fail_first_write
        self.write_calls = 0

    def add_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes=""):
        self.write_calls += 1
        if self.fail_writes or (self.fail_first_write and self.write_calls == 1):
            return False
        self.writes.append((filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes))
        return True

    def get_recent_feedback(self, query, limit=5):
        return list(self.rows)


class DualWriteAIStoreRepoTests(unittest.TestCase):
    def test_registry_dual_write_deduplicates_repeated_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _RegistryStub()
            secondary = _RegistryStub()
            repo = DualWriteAIRegistryRepo(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency.db",
            )

            self.assertTrue(repo.upsert_file_record("/tmp/a.pdf", 123.0, "resume-123"))
            self.assertTrue(repo.upsert_file_record("/tmp/a.pdf", 123.0, "resume-123"))

            self.assertEqual(len(primary.upserts), 1)
            self.assertEqual(len(secondary.upserts), 1)

    def test_registry_dual_write_recovers_secondary_failure_without_repeating_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _RegistryStub()
            secondary = _RegistryStub(fail_first_write=True)
            repo = DualWriteAIRegistryRepo(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency_retry.db",
            )

            self.assertTrue(repo.upsert_file_record("/tmp/a.pdf", 123.0, "resume-123"))
            self.assertTrue(repo.upsert_file_record("/tmp/a.pdf", 123.0, "resume-123"))

            self.assertEqual(len(primary.upserts), 1)
            self.assertEqual(len(secondary.upserts), 1)
            self.assertEqual(secondary.write_calls, 2)

    def test_registry_dual_write_migrates_legacy_idempotency_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_db = f"{temp_dir}/idempotency.db"
            legacy_key = DualWriteAIRegistryRepo._canonical_key("/tmp/a.pdf", 123.0, "resume-123")
            conn = sqlite3.connect(state_db)
            try:
                conn.execute(
                    "CREATE TABLE idempotency_keys (key TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO idempotency_keys(key, created_at) VALUES (?, ?)",
                    (legacy_key, "2026-01-01T00:00:00Z"),
                )
                conn.commit()
            finally:
                conn.close()

            primary = _RegistryStub()
            secondary = _RegistryStub()
            repo = DualWriteAIRegistryRepo(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=state_db,
            )

            self.assertTrue(repo.upsert_file_record("/tmp/a.pdf", 123.0, "resume-123"))
            self.assertEqual(len(primary.upserts), 0)
            self.assertEqual(len(secondary.upserts), 0)
            self.assertGreaterEqual(repo.get_csv_stats()["dual_write_idempotency_keys"], 1)

    def test_registry_dual_write_uses_primary_fallback_for_missing_secondary_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _RegistryStub(needs=False, resume_id="primary-resume")
            secondary = _RegistryStub(needs=True, resume_id="")
            repo = DualWriteAIRegistryRepo(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency.db",
                read_repo=secondary,
            )

            self.assertFalse(repo.needs_processing("/tmp/a.pdf", 123.0))
            self.assertEqual(repo.get_resume_id("/tmp/a.pdf"), "primary-resume")

    def test_feedback_dual_write_deduplicates_repeated_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _FeedbackStub()
            secondary = _FeedbackStub()
            repo = DualWriteAIFeedbackStore(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency.db",
            )

            self.assertTrue(repo.add_feedback("a.pdf", "query", "good", "reason", 0.9, "approve", "notes"))
            self.assertTrue(repo.add_feedback("a.pdf", "query", "good", "reason", 0.9, "approve", "notes"))

            self.assertEqual(len(primary.writes), 1)
            self.assertEqual(len(secondary.writes), 1)

    def test_feedback_dual_write_recovers_secondary_failure_without_repeating_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _FeedbackStub()
            secondary = _FeedbackStub(fail_first_write=True)
            repo = DualWriteAIFeedbackStore(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency_feedback_retry.db",
            )

            self.assertTrue(repo.add_feedback("a.pdf", "query", "good", "reason", 0.9, "approve", "notes"))
            self.assertTrue(repo.add_feedback("a.pdf", "query", "good", "reason", 0.9, "approve", "notes"))

            self.assertEqual(len(primary.writes), 1)
            self.assertEqual(len(secondary.writes), 1)
            self.assertEqual(secondary.write_calls, 2)

    def test_feedback_dual_write_migrates_legacy_idempotency_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_db = f"{temp_dir}/idempotency_feedback.db"
            legacy_key = DualWriteAIFeedbackStore._canonical_key("a.pdf", "query", "good", "reason", 0.9, "approve", "notes")
            conn = sqlite3.connect(state_db)
            try:
                conn.execute(
                    "CREATE TABLE idempotency_keys (key TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO idempotency_keys(key, created_at) VALUES (?, ?)",
                    (legacy_key, "2026-01-01T00:00:00Z"),
                )
                conn.commit()
            finally:
                conn.close()

            primary = _FeedbackStub()
            secondary = _FeedbackStub()
            repo = DualWriteAIFeedbackStore(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=state_db,
            )

            self.assertTrue(repo.add_feedback("a.pdf", "query", "good", "reason", 0.9, "approve", "notes"))
            self.assertEqual(len(primary.writes), 0)
            self.assertEqual(len(secondary.writes), 0)
            self.assertTrue(repo._state.is_complete(legacy_key))

    def test_feedback_dual_write_falls_back_to_primary_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primary = _FeedbackStub(rows=[("a.pdf", "good", "approve", "reason", "notes")])
            secondary = _FeedbackStub(rows=[])
            repo = DualWriteAIFeedbackStore(
                primary_repo=primary,
                secondary_repo=secondary,
                idempotency_db_path=f"{temp_dir}/idempotency.db",
                read_repo=secondary,
            )

            rows = repo.get_recent_feedback("query", limit=5)
            self.assertEqual(rows, primary.rows)


if __name__ == "__main__":
    unittest.main()
