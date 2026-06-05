import os
import tempfile
import unittest

from repositories.search_scope_repo import SQLiteSearchScopeRepository


class SearchScopeRepositoryTests(unittest.TestCase):
    def test_complete_search_session_persists_preflightable_verified_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SQLiteSearchScopeRepository(os.path.join(temp_dir, "scope.db"))
            try:
                repo.complete_search_session(
                    search_session_id="search-1",
                    actor_user_id="local:user-1",
                    actor_username="recruiter",
                    actor_role="recruiter",
                    rank_folder="Chief_Engineer",
                    prompt="has valid passport",
                    input_scope={"eligible_population_count": 3, "evaluated_count": 3},
                    output={"verified_count": 2, "candidate_scope_member_count": 2},
                    memberships=[
                        {
                            "candidate_scope_id": "candidate-a",
                            "content_hash_at_event": "hash-a",
                            "filename": "a.pdf",
                            "resume_id": "resume-a",
                            "decision_mode": "deterministic",
                            "facts_version": "candidate_facts.v2",
                            "reason_codes": ["PASSPORT_VALID"],
                        },
                        {
                            "candidate_scope_id": "candidate-b",
                            "content_hash_at_event": "hash-b",
                            "filename": "b.pdf",
                            "resume_id": "resume-b",
                            "decision_mode": "mixed",
                        },
                    ],
                )

                preflight = repo.preflight_parent_scope("search-1", actor_user_id="local:user-1")
                self.assertTrue(preflight["success"])
                self.assertTrue(preflight["available"])
                self.assertEqual(preflight["requested_count"], 2)
                self.assertEqual(preflight["resolved_count"], 2)
                self.assertEqual(preflight["rank_folder"], "Chief_Engineer")

                parent_scope = repo.get_refinement_parent_scope(
                    "search-1",
                    actor_user_id="local:user-1",
                )
                self.assertTrue(parent_scope["success"])
                self.assertEqual(parent_scope["candidate_scope_ids"], ["candidate-a", "candidate-b"])
                self.assertEqual(parent_scope["memberships"][0]["content_hash_at_event"], "hash-a")
            finally:
                repo.close()

    def test_preflight_rejects_wrong_actor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SQLiteSearchScopeRepository(os.path.join(temp_dir, "scope.db"))
            try:
                repo.complete_search_session(
                    search_session_id="search-1",
                    actor_user_id="local:user-1",
                    actor_username="recruiter",
                    actor_role="recruiter",
                    rank_folder="Chief_Engineer",
                    memberships=[{"candidate_scope_id": "candidate-a"}],
                )

                preflight = repo.preflight_parent_scope("search-1", actor_user_id="local:user-2")
                self.assertFalse(preflight["success"])
                self.assertEqual(preflight["error_code"], "REFINEMENT_PARENT_NOT_FOUND")
            finally:
                repo.close()

    def test_empty_verified_scope_is_not_refinable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SQLiteSearchScopeRepository(os.path.join(temp_dir, "scope.db"))
            try:
                repo.complete_search_session(
                    search_session_id="search-empty",
                    actor_user_id="local:user-1",
                    actor_username="recruiter",
                    actor_role="recruiter",
                    rank_folder="Chief_Engineer",
                    memberships=[],
                )

                preflight = repo.preflight_parent_scope("search-empty", actor_user_id="local:user-1")
                self.assertFalse(preflight["success"])
                self.assertFalse(preflight["available"])
                self.assertEqual(preflight["error_code"], "REFINEMENT_SCOPE_EMPTY")
            finally:
                repo.close()

    def test_changed_content_acknowledgement_is_bound_and_single_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SQLiteSearchScopeRepository(os.path.join(temp_dir, "scope.db"))
            try:
                acknowledgement = repo.issue_changed_content_acknowledgement(
                    actor_user_id="local:user-1",
                    parent_search_session_id="parent-1",
                    search_request_id="request-1",
                    changed_content_set_fingerprint="fingerprint-1",
                )
                self.assertFalse(repo.consume_changed_content_acknowledgement(
                    acknowledgement_id=acknowledgement["acknowledgement_id"],
                    actor_user_id="local:user-1",
                    parent_search_session_id="parent-1",
                    search_request_id="wrong-request",
                    changed_content_set_fingerprint="fingerprint-1",
                ))
                self.assertTrue(repo.consume_changed_content_acknowledgement(
                    acknowledgement_id=acknowledgement["acknowledgement_id"],
                    actor_user_id="local:user-1",
                    parent_search_session_id="parent-1",
                    search_request_id="request-1",
                    changed_content_set_fingerprint="fingerprint-1",
                ))
                self.assertFalse(repo.consume_changed_content_acknowledgement(
                    acknowledgement_id=acknowledgement["acknowledgement_id"],
                    actor_user_id="local:user-1",
                    parent_search_session_id="parent-1",
                    search_request_id="request-1",
                    changed_content_set_fingerprint="fingerprint-1",
                ))
            finally:
                repo.close()

    def test_recovery_drafts_are_actor_scoped_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SQLiteSearchScopeRepository(os.path.join(temp_dir, "scope.db"))
            try:
                for index in range(4):
                    repo.save_recovery_draft(
                        actor_user_id="local:user-1",
                        tab_id=f"tab-{index}",
                        draft={"schema_version": "ai_search_recovery.v1", "index": index},
                    )
                latest = repo.load_latest_recovery_draft(actor_user_id="local:user-1")
                self.assertEqual(latest["draft"]["index"], 3)
                self.assertIsNone(repo.load_latest_recovery_draft(actor_user_id="local:user-2"))
                count = repo.conn.execute(
                    "SELECT COUNT(*) FROM ai_search_recovery_draft WHERE actor_user_id=?",
                    ("local:user-1",),
                ).fetchone()[0]
                self.assertEqual(count, 3)
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
