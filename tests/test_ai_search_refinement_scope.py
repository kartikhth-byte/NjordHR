import json
import os
import tempfile
import unittest
from unittest.mock import patch

import backend_server
from repositories.search_scope_repo import SQLiteSearchScopeRepository


class _FakeAnalyzer:
    def resolve_candidate_scope_snapshot(self, _target_folder, candidate_scope_ids, **_kwargs):
        return {
            "requested_count": len(candidate_scope_ids),
            "resolved_count": len(candidate_scope_ids),
            "changed_content_count": 0,
            "stale_count": 0,
            "unresolvable_count": 0,
            "duplicate_count": 0,
            "resolved_candidate_scope_ids": list(candidate_scope_ids),
            "changed_members": [],
        }

    def run_analysis_stream(self, *_args, **_kwargs):
        yield {
            "type": "complete",
            "verified_matches": [
                {
                    "filename": "candidate-a.pdf",
                    "resume_id": "resume-a",
                    "candidate_scope_id": "candidate-scope-a",
                    "content_hash": "content-a",
                    "confidence": 1.0,
                    "facts_version": "candidate_facts.v2",
                    "hard_filter_reasons": [{"reason_code": "PASSPORT_VALID"}],
                }
            ],
            "uncertain_matches": [],
            "unknown_matches": [],
            "hard_filter_audit": [],
            "hard_filter_summary": {
                "scanned": 1,
                "passed": 1,
                "failed": 0,
                "unknown": 0,
                "matched": 1,
            },
            "message": "complete",
        }


def _sse_events(response):
    payload = response.get_data(as_text=True)
    events = []
    for block in payload.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block[len("data: "):]))
    return events


class AISearchRefinementScopeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_scope_repo = backend_server.search_scope_repo
        backend_server.search_scope_repo = SQLiteSearchScopeRepository(
            os.path.join(self.temp_dir.name, "scope.db")
        )
        self.rank_folder = os.path.join(self.temp_dir.name, "Chief_Engineer")
        os.makedirs(self.rank_folder, exist_ok=True)

    def tearDown(self):
        backend_server.search_scope_repo.close()
        backend_server.search_scope_repo = self.old_scope_repo
        self.temp_dir.cleanup()

    def _client(self):
        client = backend_server.app.test_client()
        with client.session_transaction() as sess:
            sess["username"] = "recruiter"
            sess["role"] = "recruiter"
            sess["user_id"] = "local:test-recruiter"
        return client

    def _save_parent_scope(self, search_session_id="parent-search"):
        backend_server.search_scope_repo.complete_search_session(
            search_session_id=search_session_id,
            actor_user_id="local:test-recruiter",
            actor_username="recruiter",
            actor_role="recruiter",
            rank_folder="Chief_Engineer",
            applied_ship_type="Bulk Carrier",
            experienced_ship_type="Tanker",
            prompt="has valid passport",
            memberships=[{
                "candidate_scope_id": "candidate-scope-a",
                "content_hash_at_event": "content-a",
                "filename": "candidate-a.pdf",
                "resume_id": "resume-a",
            }],
        )

    def test_root_stream_complete_saves_refinement_scope(self):
        client = self._client()
        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_FakeAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                },
            )

        events = _sse_events(response)
        complete_event = next(event for event in events if event.get("type") == "complete")
        refinement = complete_event.get("refinement") or {}
        self.assertTrue(refinement.get("available"))
        self.assertEqual(refinement.get("candidate_scope_member_count"), 1)
        self.assertTrue(complete_event.get("search_session_id"))

        preflight = backend_server.search_scope_repo.preflight_parent_scope(
            complete_event["search_session_id"],
            actor_user_id="local:test-recruiter",
        )
        self.assertTrue(preflight["success"])
        self.assertEqual(preflight["requested_count"], 1)

    def test_missing_parent_scope_request_does_not_fall_back_to_root_search(self):
        client = self._client()
        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "parent_search_session_id": "parent-search",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "error")
        self.assertEqual(events[0].get("error_code"), "REFINEMENT_PARENT_NOT_FOUND")
        build_analyzer.assert_not_called()

    def test_refinement_inherits_parent_context_and_persists_child_lineage(self):
        self._save_parent_scope()
        client = self._client()
        captured = {}

        class _CapturingAnalyzer(_FakeAnalyzer):
            def run_analysis_stream(
                self,
                rank_folder,
                prompt,
                applied_ship_type=None,
                experienced_ship_type=None,
                **kwargs,
            ):
                captured.update({
                    "rank_folder": rank_folder,
                    "prompt": prompt,
                    "applied_ship_type": applied_ship_type,
                    "experienced_ship_type": experienced_ship_type,
                    "candidate_scope_ids": kwargs.get("candidate_scope_ids"),
                    "candidate_scope_memberships": kwargs.get("candidate_scope_memberships"),
                })
                yield from super().run_analysis_stream()

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_CapturingAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership under pressure",
                    "parent_search_session_id": "parent-search",
                },
            )

        events = _sse_events(response)
        complete_event = next(event for event in events if event.get("type") == "complete")
        self.assertEqual(captured["rank_folder"], "Chief_Engineer")
        self.assertEqual(captured["applied_ship_type"], "Bulk Carrier")
        self.assertEqual(captured["experienced_ship_type"], "Tanker")
        self.assertEqual(captured["candidate_scope_ids"], ["candidate-scope-a"])
        self.assertEqual(
            captured["candidate_scope_memberships"][0]["candidate_scope_id"],
            "candidate-scope-a",
        )
        self.assertEqual(complete_event["search_session"]["search_mode"], "refinement")
        self.assertEqual(complete_event["search_session"]["parent_search_session_id"], "parent-search")
        self.assertEqual(complete_event["search_session"]["refinement_depth"], 1)

        child = backend_server.search_scope_repo.get_session(
            complete_event["search_session_id"],
            actor_user_id="local:test-recruiter",
        )
        self.assertEqual(child["search_mode"], "refinement")
        self.assertEqual(child["parent_search_session_id"], "parent-search")
        self.assertEqual(child["root_search_session_id"], "parent-search")
        self.assertEqual(child["rank_folder"], "Chief_Engineer")

    def test_refinement_rejects_client_context_override_before_analyzer_runs(self):
        self._save_parent_scope()
        client = self._client()
        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership under pressure",
                    "rank_folder": "Master",
                    "parent_search_session_id": "parent-search",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "error")
        self.assertEqual(events[0].get("error_code"), "REFINEMENT_CONTEXT_MISMATCH")
        build_analyzer.assert_not_called()

    def test_changed_content_refinement_requires_and_consumes_bound_acknowledgement(self):
        self._save_parent_scope()
        client = self._client()

        class _ChangedAnalyzer(_FakeAnalyzer):
            def resolve_candidate_scope_snapshot(self, _target_folder, candidate_scope_ids, **_kwargs):
                return {
                    "requested_count": 1,
                    "resolved_count": 1,
                    "changed_content_count": 1,
                    "stale_count": 0,
                    "unresolvable_count": 0,
                    "duplicate_count": 0,
                    "resolved_candidate_scope_ids": list(candidate_scope_ids),
                    "changed_members": [{
                        "candidate_scope_id": "candidate-scope-a",
                        "parent_content_hash": "content-a",
                        "current_content_hash": "content-b",
                    }],
                }

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_ChangedAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            preflight = client.get("/ai_search/refinement_scope/parent-search/preflight").get_json()
            fingerprint = preflight["scope_summary"]["changed_content_set_fingerprint"]

            missing_ack = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership",
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-1",
                },
            )
            self.assertEqual(
                _sse_events(missing_ack)[0]["error_code"],
                "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED",
            )

            ack_response = client.post(
                "/ai_search/refinement_scope/changed_content_acknowledgements",
                json={
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-2",
                    "changed_content_set_fingerprint": fingerprint,
                },
            )
            acknowledgement_id = ack_response.get_json()["acknowledgement_id"]
            accepted = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership",
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-2",
                    "changed_content_acknowledgement_id": acknowledgement_id,
                },
            )
            self.assertTrue(any(event.get("type") == "complete" for event in _sse_events(accepted)))

            replayed = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership",
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-2",
                    "changed_content_acknowledgement_id": acknowledgement_id,
                },
            )
            self.assertEqual(
                _sse_events(replayed)[0]["error_code"],
                "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED",
            )

    def test_recovery_draft_is_same_actor_and_sanitized(self):
        client = self._client()
        long_filename = f"candidate-{'x' * 300}.pdf"
        long_scope_id = "scope-" + ("a" * 90)
        long_content_hash = "ABCDEF" * 30
        warning_codes = [f"LINEAGE_CODE_{idx}_{'X' * 80}" for idx in range(12)]
        evidence_badges = [f"badge_{idx}_{'y' * 80}" for idx in range(12)]
        response = client.put(
            "/ai_search/recovery_draft",
            json={
                "tab_id": "tab_recovery_1",
                "draft": {
                    "schema_version": "ai_search_recovery.v1",
                    "active_tab": "search",
                    "secret": "must-not-survive",
                    "search_state": {
                        "prompt": "has valid passport",
                        "active_search_step_index": 99,
                        "current_completed_results": {
                            "verified_matches": [{
                                "filename": long_filename,
                                "candidate_scope_id": long_scope_id,
                                "content_hash": long_content_hash,
                                "result_bucket": "verified_match",
                                "confidence": 0.75,
                                "lineage_warning_codes": warning_codes,
                                "evidence_review_badges": evidence_badges,
                                "reason": "raw resume-derived detail",
                                "raw_text": "must-not-survive",
                            }],
                            "unknown_matches": [{
                                "filename": "needs-review.pdf",
                                "result_bucket": "unsupported-free-text",
                                "confidence": 2.0,
                            }],
                        },
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        loaded = client.get("/ai_search/recovery_draft").get_json()["draft"]["draft"]
        self.assertNotIn("secret", loaded)
        self.assertEqual(loaded["search_state"]["active_search_step_index"], 9)
        card = loaded["search_state"]["current_completed_results"]["verified_matches"][0]
        self.assertNotIn("reason", card)
        self.assertNotIn("raw_text", card)
        self.assertEqual(len(card["filename"]), 255)
        self.assertEqual(len(card["candidate_scope_id"]), 64)
        self.assertEqual(card["content_hash"], long_content_hash.lower()[:128])
        self.assertEqual(card["result_bucket"], "verified_match")
        self.assertEqual(card["confidence"], 0.75)
        self.assertEqual(len(card["lineage_warning_codes"]), 10)
        self.assertTrue(all(len(code) <= 64 for code in card["lineage_warning_codes"]))
        self.assertEqual(len(card["evidence_review_badges"]), 10)
        self.assertTrue(all(len(code) <= 64 for code in card["evidence_review_badges"]))

        unknown_card = loaded["search_state"]["current_completed_results"]["unknown_matches"][0]
        self.assertEqual(unknown_card["result_bucket"], "needs_review")
        self.assertIsNone(unknown_card["confidence"])

    def test_recovery_draft_without_completed_results_stays_empty(self):
        client = self._client()
        response = client.put(
            "/ai_search/recovery_draft",
            json={
                "tab_id": "tab_recovery_empty",
                "draft": {
                    "schema_version": "ai_search_recovery.v1",
                    "active_tab": "search",
                    "search_state": {
                        "prompt": "",
                        "current_completed_results": None,
                        "search_chain": [
                            {"prompt": "invalid empty step"},
                            {"prompt": "invalid list results", "results": []},
                        ],
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        loaded = client.get("/ai_search/recovery_draft").get_json()["draft"]["draft"]
        self.assertIsNone(loaded["search_state"]["current_completed_results"])
        self.assertEqual(loaded["search_state"]["search_chain"], [])


if __name__ == "__main__":
    unittest.main()
