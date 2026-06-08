import unittest

from candidate_facts.supabase_store import SupabaseCandidateFactsStore


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = "" if body is None else str(body)

    def json(self):
        return self._body


class CandidateFactsSupabaseStoreTests(unittest.TestCase):
    def test_promote_candidate_resume_facts_row_upserts_current_row(self):
        calls = []

        def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
            calls.append({
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "timeout": timeout,
            })
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/rest/v1/rpc/njordhr_promote_candidate_resume_facts"))
            return _FakeResponse({
                "id": "candidate_resume_facts:123",
                "is_current_for_resume": True,
            }, 200)

        store = SupabaseCandidateFactsStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )
        import candidate_facts.supabase_store as supabase_store_module

        original_request = supabase_store_module.requests.request
        supabase_store_module.requests.request = fake_request
        try:
            result = store.promote_candidate_resume_facts_row({
                "id": "candidate_resume_facts:123",
                "candidate_id": "candidate-1",
                "candidate_resume_id": "resume-1",
                "resume_blob_id": "blob-1",
                "schema_version": "candidate_facts.v1",
                "parser_version": "generic_pdf.v1",
                "facts_revision": "rev-1",
                "candidate_facts_hash": "deadbeef",
                "facts_json": {"schema_version": "candidate_facts.v1"},
                "extraction_status": "complete",
                "extraction_warnings": [],
                "is_current_for_resume": True,
                "created_at": "2026-05-25T00:00:00Z",
            })
        finally:
            supabase_store_module.requests.request = original_request

        self.assertTrue(result["success"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["json"]["p_candidate_facts_hash"], "deadbeef")
        self.assertEqual(calls[0]["json"]["p_candidate_resume_id"], "resume-1")
        self.assertEqual(calls[0]["json"]["p_id"], "candidate_resume_facts:123")

    def test_service_role_key_is_hidden_from_repr(self):
        store = SupabaseCandidateFactsStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )
        self.assertNotIn("service-role-key", repr(store))

    def test_extraction_warnings_must_be_list(self):
        store = SupabaseCandidateFactsStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )
        with self.assertRaises(ValueError):
            store._build_payload({
                "id": "candidate_resume_facts:123",
                "candidate_id": "candidate-1",
                "candidate_resume_id": "resume-1",
                "resume_blob_id": "blob-1",
                "facts_json": {"schema_version": "candidate_facts.v1"},
                "extraction_warnings": {"warning": "not a list"},
                "is_current_for_resume": True,
            })


if __name__ == "__main__":
    unittest.main()
