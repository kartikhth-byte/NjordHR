import os
import tempfile
import unittest
from unittest.mock import patch

from repositories.supabase_feedback_repo import SupabaseFeedbackStore
from repositories.supabase_registry_repo import SupabaseFileRegistry


class _FakeResponse:
    def __init__(self, status_code=200, text="[]", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data if json_data is not None else []

    def json(self):
        return self._json_data


class SupabaseRepoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.env = {key: os.environ.get(key) for key in ["SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"]}
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

    def tearDown(self):
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_registry_round_trip_uses_supabase_rest(self):
        calls = []

        def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
            calls.append({
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            })
            if method == "GET" and params and params.get("select") == "last_modified":
                return _FakeResponse(json_data=[])
            if method == "POST":
                return _FakeResponse(text="")
            if method == "GET" and params and params.get("select") == "resume_id":
                return _FakeResponse(json_data=[{"resume_id": "resume-123"}])
            return _FakeResponse(json_data=[])

        with patch("repositories.supabase_registry_repo.requests.request", side_effect=fake_request):
            repo = SupabaseFileRegistry()
            self.assertNotEqual(repo.generate_resume_id("/tmp/a.pdf"), repo.generate_resume_id("a.pdf"))
            self.assertTrue(repo.needs_processing("/tmp/a.pdf", 10.0))
            repo.upsert_file_record("/tmp/a.pdf", 10.0, "resume-123")
            self.assertEqual(repo.get_resume_id("/tmp/a.pdf"), "resume-123")

        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(call["url"].startswith("https://example.supabase.co/rest/v1/ai_file_registry") for call in calls))

    def test_feedback_round_trip_uses_supabase_rest(self):
        calls = []

        def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
            calls.append({
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            })
            if method == "POST":
                return _FakeResponse(text="")
            return _FakeResponse(json_data=[{
                "filename": "resume-a.pdf",
                "llm_decision": "good",
                "user_decision": "approve",
                "llm_reason": "matched well",
                "user_notes": "looks good",
            }])

        with patch("repositories.supabase_feedback_repo.requests.request", side_effect=fake_request):
            repo = SupabaseFeedbackStore()
            repo.add_feedback("resume-a.pdf", "2nd engineer", "good", "matched well", 0.93, "approve", "looks good")
            rows = repo.get_recent_feedback("2nd engineer", limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "resume-a.pdf")
        self.assertEqual(rows[0][2], "approve")
        self.assertTrue(any(call["method"] == "POST" for call in calls))
        self.assertTrue(any(call["method"] == "GET" for call in calls))


if __name__ == "__main__":
    unittest.main()
