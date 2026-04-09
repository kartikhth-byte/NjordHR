import tempfile
import unittest

from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo


class SupabaseCandidateEventRepoTests(unittest.TestCase):
    def test_ai_search_audit_methods_use_local_audit_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = SupabaseCandidateEventRepo(
                supabase_url="https://example.supabase.co",
                service_role_key="sb_secret_test",
                audit_base_folder=temp_dir,
            )

            ok = repo.log_ai_search_audit(
                search_session_id="search-1",
                candidate_id="123",
                filename="Chief_Officer_123.pdf",
                facts_version="2.0",
                rank_applied_for="Chief Officer",
                ai_prompt="having valid US visa",
                applied_ship_type_filter="bulk carrier",
                experienced_ship_type_filter="bulk carrier",
                hard_filter_decision="PASS",
                reason_codes="US_VISA_VALID",
                reason_messages="US Visa (USA) is valid until 2028-06-26.",
                llm_reached=True,
                result_bucket="verified_match",
            )

            self.assertTrue(ok)
            rows = repo.get_ai_search_audit_rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Candidate_ID"], "123")
            self.assertEqual(rows[0]["Facts_Version"], "2.0")
            self.assertEqual(rows[0]["Result_Bucket"], "verified_match")


if __name__ == "__main__":
    unittest.main()
