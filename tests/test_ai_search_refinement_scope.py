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


class _DuplicateTerminalAnalyzer(_FakeAnalyzer):
    def run_analysis_stream(self, *_args, **_kwargs):
        yield from super().run_analysis_stream(*_args, **_kwargs)
        yield {
            "type": "graceful_failure",
            "verified_matches": [],
            "uncertain_matches": [],
            "unknown_matches": [],
            "message": "duplicate terminal event",
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

    def _client(self, *, username="recruiter", role="recruiter", user_id="local:test-recruiter"):
        client = backend_server.app.test_client()
        with client.session_transaction() as sess:
            sess["username"] = username
            sess["role"] = role
            sess["user_id"] = user_id
        return client

    def _save_parent_scope(
        self,
        search_session_id="parent-search",
        rank_folder="Chief_Engineer",
        present_rank="chief_officer",
        content_hash_at_event="content-a",
        lineage_warning_codes=None,
        availability_filter=None,
    ):
        context = {
            "present_rank": present_rank,
            "experience_ship_type_filter": {
                "type": "experience_ship_type",
                "match_mode": "any_of",
                "items": [{
                    "ship_family": "bulk carrier",
                    "minimum_months": 12,
                    "years_back": 3,
                    "contract_count": None,
                }],
            },
            "engine_experience_filter": {
                "type": "engine_experience",
                "match_mode": "any_of",
                "items": [{
                    "engine_family": "wingd_x_engines",
                    "minimum_months": None,
                    "years_back": None,
                    "contract_count": 2,
                }],
            },
            "vessel_tonnage_filter": {
                "type": "vessel_tonnage",
                "min_value": 30000,
                "max_value": None,
                "unit": "dwt",
                "years_back": 4,
            },
            "coc_issue_authority_filter": {
                "type": "coc_issue_authority",
                "authorities": ["india_dg_shipping", "uk_mca"],
            },
        }
        if availability_filter is not None:
            context["availability_filter"] = availability_filter
        backend_server.search_scope_repo.complete_search_session(
            search_session_id=search_session_id,
            actor_user_id="local:test-recruiter",
            actor_username="recruiter",
            actor_role="recruiter",
            rank_folder=rank_folder,
            applied_ship_type="Bulk Carrier",
            experienced_ship_type="Tanker",
            prompt="has valid passport",
            context=context,
            memberships=[{
                "candidate_scope_id": "candidate-scope-a",
                "content_hash_at_event": content_hash_at_event,
                "filename": "candidate-a.pdf",
                "resume_id": "resume-a",
                "lineage_warning_codes": list(lineage_warning_codes or []),
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

    def test_duplicate_running_search_request_returns_status_without_analyzer(self):
        client = self._client()
        fingerprint = backend_server._ai_search_request_fingerprint(
            prompt="has valid passport",
            rank_folder="Chief_Engineer",
        )
        backend_server.search_scope_repo.claim_search_request(
            search_request_id="request-running",
            actor_user_id="local:test-recruiter",
            request_fingerprint=fingerprint,
            search_session_id="existing-search-session",
            request={"prompt": "has valid passport", "rank_folder": "Chief_Engineer"},
        )

        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-running",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_IN_PROGRESS")
        self.assertEqual(events[0].get("search_session_id"), "existing-search-session")
        build_analyzer.assert_not_called()

    def test_completed_search_request_returns_summary_without_replay(self):
        client = self._client()
        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_FakeAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            first_response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-complete",
                },
            )
        self.assertTrue(any(event.get("type") == "complete" for event in _sse_events(first_response)))

        with patch("backend_server._build_analyzer") as build_analyzer:
            replay_response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-complete",
                },
            )

        events = _sse_events(replay_response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_ALREADY_COMPLETE")
        self.assertEqual(events[0].get("delivery_mode"), "metadata_only")
        self.assertFalse(events[0].get("replay_available"))
        self.assertEqual((events[0].get("summary") or {}).get("verified_count"), 1)
        self.assertTrue((events[0].get("summary") or {}).get("refinement_available"))
        self.assertEqual((events[0].get("summary") or {}).get("candidate_scope_member_count"), 1)
        build_analyzer.assert_not_called()

    def test_refinement_acknowledgement_id_is_part_of_request_fingerprint(self):
        first = backend_server._ai_search_request_fingerprint(
            prompt="tighten the previous search",
            rank_folder="Chief_Engineer",
            parent_search_session_id="parent-search",
            changed_content_acknowledgement_id="ack-one",
        )
        second = backend_server._ai_search_request_fingerprint(
            prompt="tighten the previous search",
            rank_folder="Chief_Engineer",
            parent_search_session_id="parent-search",
            changed_content_acknowledgement_id="ack-two",
        )
        self.assertNotEqual(first, second)

    def test_duplicate_terminal_event_does_not_mask_completed_request(self):
        client = self._client()
        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_DuplicateTerminalAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-duplicate-terminal",
                },
            )

        events = _sse_events(response)
        self.assertEqual([event.get("type") for event in events].count("complete"), 1)
        self.assertEqual([event.get("type") for event in events].count("graceful_failure"), 1)
        stored = backend_server.search_scope_repo.claim_search_request(
            search_request_id="request-duplicate-terminal",
            actor_user_id="local:test-recruiter",
            request_fingerprint=backend_server._ai_search_request_fingerprint(
                prompt="has valid passport",
                rank_folder="Chief_Engineer",
            ),
            search_session_id="new-session",
            request={"prompt": "has valid passport", "rank_folder": "Chief_Engineer"},
        )
        self.assertEqual(stored.get("request_status"), "SEARCH_REQUEST_ALREADY_COMPLETE")

    def test_complete_mark_failure_returns_store_unavailable_without_complete_event(self):
        client = self._client()
        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_FakeAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
            patch.object(
                backend_server.search_scope_repo,
                "complete_search_request",
                return_value=False,
            ),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-complete-mark-false",
                },
            )

        events = _sse_events(response)
        self.assertFalse(any(event.get("type") == "complete" for event in events))
        self.assertEqual(events[-1].get("type"), "request_status")
        self.assertEqual(events[-1].get("request_status"), "SEARCH_REQUEST_STORE_UNAVAILABLE")

    def test_complete_mark_exception_returns_store_unavailable_without_complete_event(self):
        client = self._client()
        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_FakeAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
            patch.object(
                backend_server.search_scope_repo,
                "complete_search_request",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-complete-mark-raises",
                },
            )

        events = _sse_events(response)
        self.assertFalse(any(event.get("type") == "complete" for event in events))
        self.assertEqual(events[-1].get("type"), "request_status")
        self.assertEqual(events[-1].get("request_status"), "SEARCH_REQUEST_STORE_UNAVAILABLE")

    def test_fail_mark_failure_after_validation_returns_store_unavailable(self):
        client = self._client()
        with (
            patch("backend_server._build_analyzer") as build_analyzer,
            patch.object(
                backend_server.search_scope_repo,
                "fail_search_request",
                return_value=False,
            ),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "../Chief_Engineer",
                    "search_request_id": "request-fail-mark-false",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_STORE_UNAVAILABLE")
        build_analyzer.assert_not_called()

    def test_fail_mark_exception_after_validation_returns_store_unavailable(self):
        client = self._client()
        with (
            patch("backend_server._build_analyzer") as build_analyzer,
            patch.object(
                backend_server.search_scope_repo,
                "fail_search_request",
                side_effect=RuntimeError("db unavailable"),
            ),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "../Chief_Engineer",
                    "search_request_id": "request-fail-mark-raises",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_STORE_UNAVAILABLE")
        build_analyzer.assert_not_called()

    def test_claim_store_unavailable_returns_retryable_request_status(self):
        client = self._client()
        with (
            patch.object(
                backend_server.search_scope_repo,
                "claim_search_request",
                side_effect=RuntimeError("db unavailable"),
            ),
            patch("backend_server._build_analyzer") as build_analyzer,
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-claim-raises",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_STORE_UNAVAILABLE")
        self.assertTrue(events[0].get("retryable"))
        build_analyzer.assert_not_called()

    def test_abandoned_stream_marks_request_failed(self):
        client = self._client()

        class _StreamingAnalyzer(_FakeAnalyzer):
            def run_analysis_stream(self, *args, **kwargs):
                yield {"type": "status", "message": "started"}
                yield from super().run_analysis_stream(*args, **kwargs)

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_StreamingAnalyzer()),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-abandoned",
                },
                buffered=False,
            )
            first_chunk = next(response.response)
            if isinstance(first_chunk, bytes):
                first_chunk = first_chunk.decode("utf-8")
            self.assertIn('"type": "status"', first_chunk)
            response.close()

        with patch("backend_server._build_analyzer") as build_analyzer:
            replay = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-abandoned",
                },
            )

        events = _sse_events(replay)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_ALREADY_FAILED")
        self.assertEqual(events[0].get("error_code"), "AI_SEARCH_REQUEST_ABANDONED")
        build_analyzer.assert_not_called()

    def test_reused_search_request_id_with_different_content_is_rejected(self):
        client = self._client()
        fingerprint = backend_server._ai_search_request_fingerprint(
            prompt="has valid passport",
            rank_folder="Chief_Engineer",
        )
        backend_server.search_scope_repo.claim_search_request(
            search_request_id="request-conflict",
            actor_user_id="local:test-recruiter",
            request_fingerprint=fingerprint,
            search_session_id="existing-search-session",
            request={"prompt": "has valid passport", "rank_folder": "Chief_Engineer"},
        )

        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has tanker experience",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-conflict",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "error")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_ID_CONFLICT")
        self.assertEqual(events[0].get("error_code"), "SEARCH_REQUEST_ID_CONFLICT")
        build_analyzer.assert_not_called()

    def test_cross_actor_search_request_id_reuse_is_rejected(self):
        fingerprint = backend_server._ai_search_request_fingerprint(
            prompt="has valid passport",
            rank_folder="Chief_Engineer",
        )
        backend_server.search_scope_repo.claim_search_request(
            search_request_id="request-cross-actor",
            actor_user_id="local:first-actor",
            request_fingerprint=fingerprint,
            search_session_id="existing-search-session",
            request={"prompt": "has valid passport", "rank_folder": "Chief_Engineer"},
        )
        client = self._client(username="other", user_id="local:second-actor")

        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has valid passport",
                    "rank_folder": "Chief_Engineer",
                    "search_request_id": "request-cross-actor",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "error")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_ID_CONFLICT")
        build_analyzer.assert_not_called()

    def test_refinement_request_fingerprint_ignores_matching_client_context(self):
        self._save_parent_scope()
        client = self._client()
        fingerprint = backend_server._ai_search_request_fingerprint(
            prompt="strong leadership under pressure",
            parent_search_session_id="parent-search",
        )
        backend_server.search_scope_repo.claim_search_request(
            search_request_id="request-refinement-running",
            actor_user_id="local:test-recruiter",
            request_fingerprint=fingerprint,
            search_session_id="existing-refinement-session",
            request={
                "prompt": "strong leadership under pressure",
                "parent_search_session_id": "parent-search",
            },
        )

        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership under pressure",
                    "parent_search_session_id": "parent-search",
                    "rank_folder": "Chief_Engineer",
                    "applied_ship_type": "Bulk Carrier",
                    "experienced_ship_type": "Tanker",
                    "search_request_id": "request-refinement-running",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "request_status")
        self.assertEqual(events[0].get("request_status"), "SEARCH_REQUEST_IN_PROGRESS")
        build_analyzer.assert_not_called()

    def test_refinement_context_match_ignores_availability_reference_date(self):
        self._save_parent_scope(
            availability_filter={
                "type": "availability",
                "version": "v1",
                "value_type": "relative_days",
                "status": None,
                "available_by_date": None,
                "available_from_date": None,
                "available_until_date": None,
                "relative_days": 30,
                "resolved_reference_date": "2026-04-06",
                "display_value": "available within 30 days",
            }
        )
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
                    "prompt": "strong leadership under pressure",
                    "parent_search_session_id": "parent-search",
                    "availability_filter": json.dumps({
                        "type": "availability",
                        "version": "v1",
                        "value_type": "relative_days",
                        "status": None,
                        "available_by_date": None,
                        "available_from_date": None,
                        "available_until_date": None,
                        "relative_days": 30,
                        "resolved_reference_date": "2026-04-07",
                        "display_value": "client text ignored",
                    }),
                },
            )

        events = _sse_events(response)
        self.assertTrue(any(event.get("type") == "complete" for event in events))
        self.assertFalse(any(event.get("error_code") == "REFINEMENT_CONTEXT_MISMATCH" for event in events))

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

    def test_expired_parent_scope_request_does_not_fall_back_to_root_search(self):
        self._save_parent_scope()
        backend_server.search_scope_repo.conn.execute(
            "UPDATE search_session_lineage SET membership_expires_at=? WHERE search_session_id=?",
            ("2000-01-01T00:00:00+00:00", "parent-search"),
        )
        backend_server.search_scope_repo.conn.commit()

        client = self._client()
        with patch("backend_server._build_analyzer") as build_analyzer:
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership under pressure",
                    "parent_search_session_id": "parent-search",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("type"), "error")
        self.assertEqual(events[0].get("error_code"), "REFINEMENT_PARENT_EXPIRED")
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
                experience_ship_type_filter=None,
                engine_experience_filter=None,
                **kwargs,
            ):
                captured.update({
                    "rank_folder": rank_folder,
                    "prompt": prompt,
                    "applied_ship_type": applied_ship_type,
                    "experienced_ship_type": experienced_ship_type,
                    "experience_ship_type_filter": experience_ship_type_filter,
                    "engine_experience_filter": engine_experience_filter,
                    "coc_issue_authority_filter": kwargs.get("coc_issue_authority_filter"),
                    "present_rank": kwargs.get("present_rank"),
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
        self.assertEqual(captured["present_rank"], "chief_officer")
        self.assertEqual(
            captured["experience_ship_type_filter"],
            {
                    "type": "experience_ship_type",
                    "match_mode": "any_of",
                    "items": [{
                    "ship_family": "bulk carrier",
                    "minimum_months": 12,
                    "years_back": 3,
                    "contract_count": None,
                }],
            },
        )
        self.assertEqual(
            captured["engine_experience_filter"],
            {
                "type": "engine_experience",
                "match_mode": "any_of",
                "items": [{
                    "engine_family": "wingd_x_engines",
                    "minimum_months": None,
                    "years_back": None,
                    "contract_count": 2,
                }],
            },
        )
        self.assertEqual(
            captured["coc_issue_authority_filter"],
            {
                "type": "coc_issue_authority",
                "authorities": ["india_dg_shipping", "uk_mca"],
            },
        )
        self.assertEqual(captured["candidate_scope_ids"], ["candidate-scope-a"])
        self.assertEqual(
            captured["candidate_scope_memberships"][0]["candidate_scope_id"],
            "candidate-scope-a",
        )
        self.assertEqual(complete_event["search_session"]["search_mode"], "refinement")
        self.assertEqual(complete_event["search_session"]["parent_search_session_id"], "parent-search")
        self.assertEqual(complete_event["search_session"]["refinement_depth"], 1)
        self.assertEqual(complete_event["search_context"]["present_rank"], "chief_officer")
        self.assertEqual(
            complete_event["search_context"]["coc_issue_authority_filter"],
            {
                "type": "coc_issue_authority",
                "authorities": ["india_dg_shipping", "uk_mca"],
            },
        )

        child = backend_server.search_scope_repo.get_session(
            complete_event["search_session_id"],
            actor_user_id="local:test-recruiter",
        )
        self.assertEqual(child["search_mode"], "refinement")
        self.assertEqual(child["parent_search_session_id"], "parent-search")
        self.assertEqual(child["root_search_session_id"], "parent-search")
        self.assertEqual(child["rank_folder"], "Chief_Engineer")
        self.assertEqual((child["context"] or {}).get("present_rank"), "chief_officer")
        self.assertEqual(
            (child["context"] or {}).get("coc_issue_authority_filter"),
            {
                "type": "coc_issue_authority",
                "authorities": ["india_dg_shipping", "uk_mca"],
            },
        )

    def test_refinement_of_cross_folder_present_rank_parent_uses_candidate_scope(self):
        self._save_parent_scope(rank_folder="", present_rank="chief_officer")
        client = self._client()
        captured = {}

        class _CapturingAnalyzer(_FakeAnalyzer):
            def resolve_candidate_scope_snapshot(self, target_folder, candidate_scope_ids, **kwargs):
                captured["preflight_target_folder"] = target_folder
                return super().resolve_candidate_scope_snapshot(
                    target_folder,
                    candidate_scope_ids,
                    **kwargs,
                )

            def run_analysis_stream(self, rank_folder, prompt, **kwargs):
                captured["rank_folder"] = rank_folder
                captured["present_rank"] = kwargs.get("present_rank")
                captured["candidate_scope_ids"] = kwargs.get("candidate_scope_ids")
                captured["candidate_scope_memberships"] = kwargs.get("candidate_scope_memberships")
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
        self.assertEqual(captured["preflight_target_folder"], self.temp_dir.name)
        self.assertEqual(captured["rank_folder"], "")
        self.assertEqual(captured["present_rank"], "chief_officer")
        self.assertEqual(captured["candidate_scope_ids"], ["candidate-scope-a"])
        self.assertEqual(
            captured["candidate_scope_memberships"][0]["candidate_scope_id"],
            "candidate-scope-a",
        )
        self.assertEqual(complete_event["search_session"]["search_mode"], "refinement")
        self.assertEqual(complete_event["search_context"]["rank_folder"], "")
        self.assertEqual(complete_event["search_context"]["present_rank"], "chief_officer")

    def test_refinement_lineage_warning_persists_into_next_refinement(self):
        self._save_parent_scope()
        client = self._client()
        warning_code = "EARLIER_CONDITIONS_NOT_RECERTIFIED"
        captured_second_parent_memberships = {}

        class _WarningAnalyzer(_FakeAnalyzer):
            def run_analysis_stream(self, *args, **kwargs):
                for event in super().run_analysis_stream(*args, **kwargs):
                    if event.get("type") == "complete":
                        event["verified_matches"][0]["lineage_warning_codes"] = [warning_code]
                    yield event

        class _SecondRefinementAnalyzer(_FakeAnalyzer):
            def run_analysis_stream(self, *args, **kwargs):
                captured_second_parent_memberships["memberships"] = kwargs.get(
                    "candidate_scope_memberships"
                )
                yield from super().run_analysis_stream(*args, **kwargs)

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch(
                "backend_server._build_analyzer",
                side_effect=[_FakeAnalyzer(), _WarningAnalyzer()],
            ),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            first_response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has tanker experience",
                    "parent_search_session_id": "parent-search",
                },
            )

        first_complete = next(
            event for event in _sse_events(first_response) if event.get("type") == "complete"
        )
        child_session_id = first_complete["search_session_id"]
        child_memberships = backend_server.search_scope_repo.get_scope_memberships(
            child_session_id,
            actor_user_id="local:test-recruiter",
        )
        self.assertIn(warning_code, child_memberships[0]["lineage_warning_codes"])

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch(
                "backend_server._build_analyzer",
                side_effect=[_FakeAnalyzer(), _SecondRefinementAnalyzer()],
            ),
            patch("backend_server._record_supabase_telemetry", return_value=None),
            patch("backend_server._schedule_search_prompt_audit", return_value=None),
        ):
            second_response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "has basic coc",
                    "parent_search_session_id": child_session_id,
                },
            )

        self.assertTrue(
            any(event.get("type") == "complete" for event in _sse_events(second_response))
        )
        second_parent_memberships = captured_second_parent_memberships["memberships"]
        self.assertIn(warning_code, second_parent_memberships[0]["lineage_warning_codes"])

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

            failed_request_ack_response = client.post(
                "/ai_search/refinement_scope/changed_content_acknowledgements",
                json={
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-1",
                    "changed_content_set_fingerprint": fingerprint,
                },
            )
            failed_request_acknowledgement_id = failed_request_ack_response.get_json()["acknowledgement_id"]
            failed_retry_with_ack = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership",
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-1",
                    "changed_content_acknowledgement_id": failed_request_acknowledgement_id,
                },
            )
            self.assertEqual(
                _sse_events(failed_retry_with_ack)[0]["request_status"],
                "SEARCH_REQUEST_ID_CONFLICT",
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
                _sse_events(replayed)[0]["request_status"],
                "SEARCH_REQUEST_ALREADY_COMPLETE",
            )

    def test_refinement_requires_acknowledgement_when_parent_hash_was_legacy_empty(self):
        self._save_parent_scope(content_hash_at_event="")
        client = self._client()
        test_case = self

        class _LegacyEmptyHashChangedAnalyzer(_FakeAnalyzer):
            def resolve_candidate_scope_snapshot(self, _target_folder, candidate_scope_ids, **kwargs):
                memberships = kwargs.get("candidate_scope_memberships") or []
                test_case.assertEqual(memberships[0].get("content_hash_at_event"), "")
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
                        "parent_content_hash": "",
                        "current_content_hash": "current-content-hash",
                    }],
                }

        with (
            patch("backend_server._active_download_root", return_value=self.temp_dir.name),
            patch("backend_server._build_analyzer", return_value=_LegacyEmptyHashChangedAnalyzer()),
        ):
            response = client.get(
                "/analyze_stream",
                query_string={
                    "prompt": "strong leadership",
                    "parent_search_session_id": "parent-search",
                    "search_request_id": "request-legacy-empty",
                },
            )

        events = _sse_events(response)
        self.assertEqual(events[0].get("error_code"), "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED")

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
                        "present_rank": "chief_officer",
                        "coc_issue_authority_filter": {
                            "type": "coc_issue_authority",
                            "authorities": ["DG Shipping India", "not_real", "MCA UK"],
                            "ignored": "must-not-survive",
                        },
                        "vessel_tonnage_filter": {
                            "min_value": 50000,
                            "max_value": 80000,
                            "unit": "gt_grt",
                            "ignored": "must-not-survive",
                            "years_back": 2,
                        },
                        "availability_filter": {
                            "type": "availability",
                            "version": "v1",
                            "value_type": "relative_days",
                            "status": None,
                            "available_by_date": None,
                            "available_from_date": None,
                            "available_until_date": None,
                            "relative_days": 30,
                            "resolved_reference_date": "2026-04-06",
                            "display_value": "available within 30 days",
                            "ignored": "must-not-survive",
                        },
                        "experience_ship_type_filter": {
                            "type": "experience_ship_type",
                            "match_mode": "any_of",
                            "items": [{
                                "ship_family": "tanker",
                                "minimum_months": 12,
                                "years_back": 3,
                                "ignored": "must-not-survive",
                            }],
                        },
                        "engine_experience_filter": {
                            "type": "engine_experience",
                            "match_mode": "any_of",
                            "items": [{
                                "engine_family": "wingd_x_engines",
                                "contract_count": 2,
                                "ignored": "must-not-survive",
                            }],
                        },
                        "active_search_step_index": 99,
                        "current_completed_results": {
                            "search_context": {
                                "rank_folder": "2nd Engineer",
                                "present_rank": "2nd_engineer",
                                "applied_ship_type": "Oil Tanker",
                                "experienced_ship_type": "Any",
                                "experience_ship_type_filter": {
                                    "type": "experience_ship_type",
                                    "match_mode": "any_of",
                                    "items": [{
                                        "ship_family": "bulk carrier",
                                        "contract_count": 3,
                                    }],
                                },
                                "engine_experience_filter": {
                                    "type": "engine_experience",
                                    "match_mode": "any_of",
                                    "items": [{
                                        "engine_family": "man_b_w_me",
                                        "years_back": 2,
                                    }],
                                },
                                "vessel_tonnage_filter": {
                                    "min_value": 30000,
                                    "max_value": None,
                                    "unit": "dwt",
                                    "years_back": 4,
                                },
                                "coc_issue_authority_filter": {
                                    "type": "coc_issue_authority",
                                    "authorities": ["MCA UK"],
                                    "ignored": "must-not-survive",
                                },
                                "availability_filter": {
                                    "type": "availability",
                                    "version": "v1",
                                    "value_type": "window",
                                    "status": None,
                                    "available_by_date": None,
                                    "available_from_date": "2026-04-01",
                                    "available_until_date": "2026-05-01",
                                    "relative_days": None,
                                    "resolved_reference_date": "2026-04-06",
                                    "display_value": "available between 2026-04-01 and 2026-05-01",
                                    "ignored": "must-not-survive",
                                },
                                "raw_nested": {"secret": "must-not-survive"},
                            },
                            "verified_matches": [{
                                "filename": long_filename,
                                "candidate_scope_id": long_scope_id,
                                "content_hash": long_content_hash,
                                "downloaded_rank_folder": "Chief_Officer",
                                "result_bucket": "verified_match",
                                "confidence": 0.75,
                                "lineage_warning_codes": warning_codes,
                                "evidence_review_badges": evidence_badges,
                                "reason": "raw resume-derived detail",
                                "raw_text": "must-not-survive",
                            }],
                            "unknown_matches": [{
                                "filename": "needs-review.pdf",
                                "downloaded_rank_folder": "../../etc",
                                "result_bucket": "unsupported-free-text",
                                "confidence": 2.0,
                                "needs_review_rank_summary": "Could not determine current/present rank from this resume.",
                                "needs_review_availability_summary": "Could not determine candidate availability reliably from the resume.",
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
        self.assertEqual(loaded["search_state"]["present_rank"], "chief_officer")
        self.assertEqual(
            loaded["search_state"]["vessel_tonnage_filter"],
            {
                "type": "vessel_tonnage",
                "min_value": 50000,
                "max_value": 80000,
                "unit": "gt_grt",
                "years_back": 2,
            },
        )
        self.assertEqual(
            loaded["search_state"]["coc_issue_authority_filter"],
            {
                "type": "coc_issue_authority",
                "authorities": ["india_dg_shipping", "uk_mca"],
            },
        )
        self.assertEqual(
            loaded["search_state"]["availability_filter"],
            {
                "type": "availability",
                "version": "v1",
                "value_type": "relative_days",
                "status": None,
                "available_by_date": None,
                "available_from_date": None,
                "available_until_date": None,
                "relative_days": 30,
                "resolved_reference_date": "2026-04-06",
                "display_value": "available within 30 days",
            },
        )
        self.assertEqual(
            loaded["search_state"]["experience_ship_type_filter"],
            {
                "type": "experience_ship_type",
                "match_mode": "any_of",
                "items": [{
                    "ship_family": "tanker",
                    "minimum_months": 12,
                    "years_back": 3,
                    "contract_count": None,
                }],
            },
        )
        self.assertEqual(
            loaded["search_state"]["engine_experience_filter"],
            {
                "type": "engine_experience",
                "match_mode": "any_of",
                "items": [{
                    "engine_family": "wingd_x_engines",
                    "minimum_months": None,
                    "years_back": None,
                    "contract_count": 2,
                }],
            },
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["present_rank"],
            "2nd_engineer",
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["vessel_tonnage_filter"],
            {
                "type": "vessel_tonnage",
                "min_value": 30000,
                "max_value": None,
                "unit": "dwt",
                "years_back": 4,
            },
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["coc_issue_authority_filter"],
            {
                "type": "coc_issue_authority",
                "authorities": ["uk_mca"],
            },
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["availability_filter"],
            {
                "type": "availability",
                "version": "v1",
                "value_type": "window",
                "status": None,
                "available_by_date": None,
                "available_from_date": "2026-04-01",
                "available_until_date": "2026-05-01",
                "relative_days": None,
                "resolved_reference_date": "2026-04-06",
                "display_value": "available between 2026-04-01 and 2026-05-01",
            },
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["experience_ship_type_filter"],
            {
                    "type": "experience_ship_type",
                    "match_mode": "any_of",
                    "items": [{
                    "ship_family": "bulk carrier",
                    "minimum_months": None,
                    "years_back": None,
                    "contract_count": 3,
                }],
            },
        )
        self.assertEqual(
            loaded["search_state"]["current_completed_results"]["search_context"]["engine_experience_filter"],
            {
                "type": "engine_experience",
                "match_mode": "any_of",
                "items": [{
                    "engine_family": "man_b_w_me",
                    "minimum_months": None,
                    "years_back": 2,
                    "contract_count": None,
                }],
            },
        )
        self.assertNotIn(
            "raw_nested",
            loaded["search_state"]["current_completed_results"]["search_context"],
        )
        card = loaded["search_state"]["current_completed_results"]["verified_matches"][0]
        self.assertNotIn("reason", card)
        self.assertNotIn("raw_text", card)
        self.assertEqual(len(card["filename"]), 255)
        self.assertEqual(len(card["candidate_scope_id"]), 64)
        self.assertEqual(card["content_hash"], long_content_hash.lower()[:128])
        self.assertEqual(card["downloaded_rank_folder"], "Chief_Officer")
        self.assertEqual(card["result_bucket"], "verified_match")
        self.assertEqual(card["confidence"], 0.75)
        self.assertEqual(len(card["lineage_warning_codes"]), 10)
        self.assertTrue(all(len(code) <= 64 for code in card["lineage_warning_codes"]))
        self.assertEqual(len(card["evidence_review_badges"]), 10)
        self.assertTrue(all(len(code) <= 64 for code in card["evidence_review_badges"]))

        unknown_card = loaded["search_state"]["current_completed_results"]["unknown_matches"][0]
        self.assertEqual(unknown_card["downloaded_rank_folder"], "")
        self.assertEqual(unknown_card["result_bucket"], "needs_review")
        self.assertIsNone(unknown_card["confidence"])
        self.assertEqual(
            unknown_card["needs_review_rank_summary"],
            "Could not determine current/present rank from this resume.",
        )
        self.assertEqual(
            unknown_card["needs_review_availability_summary"],
            "Could not determine candidate availability reliably from the resume.",
        )

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
