import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from repositories.csv_feedback_repo import CSVFeedbackStore
from repositories.csv_registry_repo import CSVFileRegistry
from repositories.resume_identity import (
    LEGACY_UNKNOWN_PROVENANCE,
    VERIFIED_CONTENT_HASH_PROVENANCE,
)


class CsvRepoAdapterTests(unittest.TestCase):
    def test_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "registry.db")
            repo = CSVFileRegistry(db_path)
            try:
                file_path = "/tmp/resume-a.pdf"
                resume_id = repo.generate_resume_id(file_path)

                self.assertTrue(repo.needs_processing(file_path, 123.0))
                repo.upsert_file_record(file_path, 123.0, resume_id)
                self.assertFalse(repo.needs_processing(file_path, 122.0))
                self.assertEqual(repo.get_resume_id(file_path), resume_id)
            finally:
                repo.close()

    def test_registry_resume_id_is_content_stable_across_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = CSVFileRegistry(os.path.join(temp_dir, "registry.db"))
            try:
                first = Path(temp_dir) / "a" / "resume.pdf"
                second = Path(temp_dir) / "b" / "resume.pdf"
                first.parent.mkdir(parents=True, exist_ok=True)
                second.parent.mkdir(parents=True, exist_ok=True)
                first.write_bytes(b"%PDF-1.4\nsame-content")
                second.write_bytes(b"%PDF-1.4\nsame-content")

                self.assertEqual(repo.generate_resume_id(str(first)), repo.generate_resume_id(str(second)))
            finally:
                repo.close()

    def test_registry_persists_resume_identity_provenance_for_readable_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = CSVFileRegistry(os.path.join(temp_dir, "registry.db"))
            try:
                resume_path = Path(temp_dir) / "resume.pdf"
                resume_path.write_bytes(b"%PDF-1.4\nidentity-content")
                resume_id = repo.generate_resume_id(str(resume_path))

                repo.upsert_file_record(str(resume_path), 123.0, resume_id)
                record = repo.get_resume_identity_record(str(resume_path))

                self.assertEqual(record["resume_id"], resume_id)
                self.assertEqual(record["alias_provenance"], VERIFIED_CONTENT_HASH_PROVENANCE)
                self.assertEqual(record["content_hash"], resume_id)
                self.assertTrue(record["candidate_scope_id"])
                self.assertTrue(record["is_authoritative_content_alias"])
            finally:
                repo.close()

    def test_registry_reuses_candidate_scope_id_for_exact_content_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = CSVFileRegistry(os.path.join(temp_dir, "registry.db"))
            try:
                first = Path(temp_dir) / "first.pdf"
                second = Path(temp_dir) / "second.pdf"
                first.write_bytes(b"%PDF-1.4\nsame-identity")
                second.write_bytes(b"%PDF-1.4\nsame-identity")

                first_id = repo.generate_resume_id(str(first))
                second_id = repo.generate_resume_id(str(second))
                repo.upsert_file_record(str(first), 1.0, first_id)
                repo.upsert_file_record(str(second), 1.0, second_id)

                first_record = repo.get_resume_identity_record(str(first))
                second_record = repo.get_resume_identity_record(str(second))
                self.assertEqual(first_record["content_hash"], second_record["content_hash"])
                self.assertEqual(first_record["candidate_scope_id"], second_record["candidate_scope_id"])
            finally:
                repo.close()

    def test_registry_does_not_reuse_candidate_scope_id_from_unverified_content_hash_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = CSVFileRegistry(os.path.join(temp_dir, "registry.db"))
            try:
                resume_path = Path(temp_dir) / "resume.pdf"
                resume_path.write_bytes(b"%PDF-1.4\nsame-content")
                content_hash = repo.generate_resume_id(str(resume_path))
                legacy_scope_id = "legacy-scope"

                with repo.lock:
                    repo.conn.execute(
                        """
                        INSERT INTO files (
                            file_path, last_modified, resume_id, resume_id_provenance,
                            content_hash, candidate_scope_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "legacy.pdf",
                            1.0,
                            "legacy-resume-id",
                            LEGACY_UNKNOWN_PROVENANCE,
                            content_hash,
                            legacy_scope_id,
                        ),
                    )
                    repo.conn.commit()

                repo.upsert_file_record(str(resume_path), 2.0, content_hash)
                record = repo.get_resume_identity_record(str(resume_path))

                self.assertEqual(record["content_hash"], content_hash)
                self.assertEqual(record["alias_provenance"], VERIFIED_CONTENT_HASH_PROVENANCE)
                self.assertNotEqual(record["candidate_scope_id"], legacy_scope_id)
            finally:
                repo.close()

    def test_registry_old_schema_migration_preserves_rows_and_adds_identity_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "registry.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("""
                    CREATE TABLE files (
                        file_path TEXT PRIMARY KEY,
                        last_modified REAL NOT NULL,
                        resume_id TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT INTO files (file_path, last_modified, resume_id) VALUES (?, ?, ?)",
                    ("legacy.pdf", 10.0, "legacy-id"),
                )
                conn.commit()
            finally:
                conn.close()

            repo = CSVFileRegistry(db_path)
            try:
                self.assertFalse(repo.needs_processing("legacy.pdf", 9.0))
                self.assertEqual(repo.get_resume_id("legacy.pdf"), "legacy-id")

                record = repo.get_resume_identity_record("legacy.pdf")
                self.assertEqual(record["resume_id"], "legacy-id")
                self.assertEqual(record["alias_provenance"], LEGACY_UNKNOWN_PROVENANCE)
                self.assertEqual(record["content_hash"], "")
                self.assertTrue(record["candidate_scope_id"])
                self.assertFalse(record["is_authoritative_content_alias"])
            finally:
                repo.close()

    def test_feedback_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "feedback.db")
            repo = CSVFeedbackStore(db_path)
            try:
                repo.add_feedback(
                    "resume-a.pdf",
                    "2nd engineer tanker",
                    "good",
                    "matched well",
                    0.93,
                    "approve",
                    "looks good",
                )

                rows = repo.get_recent_feedback("2nd engineer", limit=5)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], "resume-a.pdf")
                self.assertEqual(rows[0][2], "approve")
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
