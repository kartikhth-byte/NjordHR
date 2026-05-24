import os
import tempfile
import unittest

from repositories.csv_feedback_repo import CSVFeedbackStore
from repositories.csv_registry_repo import CSVFileRegistry


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
