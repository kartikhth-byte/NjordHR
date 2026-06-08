import unittest

from query_understanding.supabase_telemetry_store import SupabaseTelemetryStore


class QueryUnderstandingTelemetryStoreTests(unittest.TestCase):
    def test_build_payload_keeps_prompt_audit_fields_structured(self):
        store = SupabaseTelemetryStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )
        payload = store.build_payload(
            telemetry_kind="prompt_audit",
            category="query_understanding",
            status="ok",
            summary="shadow=enabled llm=llm comparisons=3",
            payload={"comparison_outcomes": ["equivalent", "regression"]},
            prompt_hash="abc123",
            prompt_text="2nd engineer with valid passport",
            actor_role="recruiter",
            actor_username="demo",
            session_id="session-1",
        )

        self.assertEqual(payload["telemetry_kind"], "prompt_audit")
        self.assertEqual(payload["category"], "query_understanding")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["prompt_hash"], "abc123")
        self.assertEqual(payload["actor_role"], "recruiter")
        self.assertEqual(payload["actor_username"], "demo")
        self.assertEqual(payload["session_id"], "session-1")
        self.assertEqual(payload["payload"]["comparison_outcomes"], ["equivalent", "regression"])
        self.assertIn("shadow=enabled", payload["summary"])

    def test_list_prompt_audit_summaries_uses_summary_view(self):
        store = SupabaseTelemetryStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )

        captured = {}

        def fake_request(method, path, *, json_body=None, params=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params or {}
            return [{
                "prompt_hash": "abc123",
                "total_count": 12,
                "issue_count": 2,
                "ok_count": 10,
                "disabled_count": 0,
                "first_seen_at": "2025-05-01T00:00:00Z",
                "last_seen_at": "2025-05-25T00:00:00Z",
            }]

        store._request = fake_request  # type: ignore[assignment]

        rows = store.list_prompt_audit_summaries(limit=7)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/rest/v1/njordhr_telemetry_prompt_audit_summary")
        self.assertEqual(captured["params"]["limit"], 7)
        self.assertEqual(rows[0]["prompt_hash"], "abc123")
        self.assertEqual(rows[0]["total_count"], 12)

    def test_list_prompt_audit_summaries_supports_offset(self):
        store = SupabaseTelemetryStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )

        captured = {}

        def fake_request(method, path, *, json_body=None, params=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params or {}
            return []

        store._request = fake_request  # type: ignore[assignment]
        store.list_prompt_audit_summaries(limit=7, offset=14)
        self.assertEqual(captured["params"]["limit"], 7)
        self.assertEqual(captured["params"]["offset"], 14)

    def test_get_prompt_audit_totals_uses_aggregate_view(self):
        store = SupabaseTelemetryStore(
            supabase_url="https://example.supabase.co",
            service_role_key="service-role-key",
        )

        captured = {}

        def fake_request(method, path, *, json_body=None, params=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params or {}
            return [{
                "total_count": 16,
                "issue_count": 2,
                "ok_count": 12,
                "disabled_count": 2,
                "prompt_hash_count": 2,
            }]

        store._request = fake_request  # type: ignore[assignment]

        totals = store.get_prompt_audit_totals()
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/rest/v1/njordhr_telemetry_prompt_audit_totals")
        self.assertEqual(totals["total_count"], 16)
        self.assertEqual(totals["issue_count"], 2)


if __name__ == "__main__":
    unittest.main()
