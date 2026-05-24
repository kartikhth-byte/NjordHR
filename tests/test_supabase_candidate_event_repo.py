import json
import tempfile
import unittest
from unittest.mock import patch

from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo


class _FakeResponse:
    def __init__(self, rows):
        self._rows = rows
        self.text = json.dumps(rows)
        self.status_code = 200

    def json(self):
        return self._rows


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

    def test_latest_status_paginates_across_pages(self):
        calls = []

        def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
            calls.append(params or {})
            offset = int((params or {}).get("offset", 0) or 0)
            limit = int((params or {}).get("limit", 1000) or 1000)
            if offset == 0:
                rows = [{
                    "candidate_external_id": "1001",
                    "filename": "resume-1001.pdf",
                    "resume_url": "storage://resumes/1001/resume-1001.pdf",
                    "event_type": "initial_verification",
                    "status": "New",
                    "notes": "",
                    "rank_applied_for": "Chief_Officer",
                    "search_ship_type": "Bulk Carrier",
                    "ai_search_prompt": "",
                    "ai_match_reason": "",
                    "name": "Candidate One",
                    "present_rank": "Chief Officer",
                    "email": "one@example.com",
                    "country": "India",
                    "mobile_no": "+911111111111",
                    "created_at": "2026-01-02T00:00:00Z",
                }]
            elif offset == 1:
                rows = [{
                    "candidate_external_id": "1002",
                    "filename": "resume-1002.pdf",
                    "resume_url": "storage://resumes/1002/resume-1002.pdf",
                    "event_type": "initial_verification",
                    "status": "New",
                    "notes": "",
                    "rank_applied_for": "Chief_Officer",
                    "search_ship_type": "Bulk Carrier",
                    "ai_search_prompt": "",
                    "ai_match_reason": "",
                    "name": "Candidate Two",
                    "present_rank": "Chief Officer",
                    "email": "two@example.com",
                    "country": "India",
                    "mobile_no": "+922222222222",
                    "created_at": "2026-01-01T00:00:00Z",
                }]
            else:
                rows = []
            return _FakeResponse(rows[:limit])

        with tempfile.TemporaryDirectory() as audit_dir:
            with patch("repositories.supabase_candidate_event_repo.requests.request", side_effect=fake_request):
                repo = SupabaseCandidateEventRepo(
                    supabase_url="https://example.supabase.co",
                    service_role_key="sb_secret_test",
                    audit_base_folder=audit_dir,
                )
                repo.PAGE_SIZE = 1
                latest = repo.get_latest_status_per_candidate()

        self.assertEqual([str(v) for v in latest["Candidate_ID"].tolist()], ["1001", "1002"])
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(calls[0]["offset"], 0)
        self.assertEqual(calls[1]["offset"], 1)

    def test_log_event_does_not_invent_local_resume_url_by_default(self):
        calls = []

        def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
            calls.append({"method": method, "url": url, "json": json, "params": params})
            return _FakeResponse([])

        with tempfile.TemporaryDirectory() as audit_dir:
            with patch("repositories.supabase_candidate_event_repo.requests.request", side_effect=fake_request):
                repo = SupabaseCandidateEventRepo(
                    supabase_url="https://example.supabase.co",
                    service_role_key="sb_secret_test",
                    audit_base_folder=audit_dir,
                )
                ok = repo.log_event(
                    candidate_id="123",
                    filename="Chief_Officer_123.pdf",
                    event_type="initial_verification",
                    rank_applied_for="Chief_Officer",
                    extracted_data={"email": "test@example.com"},
                    resume_url="",
                )

        self.assertTrue(ok)
        event_call = next(call for call in calls if call["method"] == "POST" and call["url"].endswith("/rest/v1/candidate_events"))
        self.assertEqual(event_call["json"][0]["resume_url"], "")

    def test_log_event_can_opt_in_to_local_resume_url_fallback(self):
        calls = []

        def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
            calls.append({"method": method, "url": url, "json": json, "params": params})
            return _FakeResponse([])

        with tempfile.TemporaryDirectory() as audit_dir:
            with patch("repositories.supabase_candidate_event_repo.requests.request", side_effect=fake_request):
                repo = SupabaseCandidateEventRepo(
                    supabase_url="https://example.supabase.co",
                    service_role_key="sb_secret_test",
                    audit_base_folder=audit_dir,
                    allow_local_resume_url_fallback=True,
                )
                ok = repo.log_event(
                    candidate_id="123",
                    filename="Chief_Officer_123.pdf",
                    event_type="initial_verification",
                    rank_applied_for="Chief_Officer",
                    extracted_data={"email": "test@example.com"},
                    resume_url="",
                )

        self.assertTrue(ok)
        event_call = next(call for call in calls if call["method"] == "POST" and call["url"].endswith("/rest/v1/candidate_events"))
        self.assertIn("/get_resume/Chief_Officer/Chief_Officer_123.pdf", event_call["json"][0]["resume_url"])


if __name__ == "__main__":
    unittest.main()
