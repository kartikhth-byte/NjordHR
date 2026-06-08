import unittest

from repositories.candidate_event_repo import CandidateEventRepo
from repositories.feedback_repo import FeedbackRepo
from repositories.registry_repo import RegistryRepo


class RepositoryInterfaceTests(unittest.TestCase):
    def test_candidate_event_repo_contracts_exist(self):
        expected = {
            "log_event",
            "get_latest_status_per_candidate",
            "get_candidate_history",
            "log_status_change",
            "log_note_added",
            "get_rank_counts",
            "get_csv_stats",
            "log_ai_search_audit",
            "get_ai_search_audit_rows",
        }
        self.assertTrue(expected.issubset(set(CandidateEventRepo.__abstractmethods__)))

    def test_feedback_repo_contracts_exist(self):
        self.assertEqual(FeedbackRepo.__abstractmethods__, {"add_feedback", "get_recent_feedback"})

    def test_registry_repo_contracts_exist(self):
        self.assertEqual(
            RegistryRepo.__abstractmethods__,
            {"generate_resume_id", "needs_processing", "upsert_file_record", "get_resume_id"},
        )


if __name__ == "__main__":
    unittest.main()
