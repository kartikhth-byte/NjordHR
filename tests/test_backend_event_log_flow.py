import io
import hashlib
import json
import os
import sys
import types
import zipfile
import tempfile
import unittest
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from werkzeug.security import generate_password_hash


def _stub_external_modules():
    """Stub heavy optional modules so backend_server import is test-safe."""
    if 'scraper_engine' not in sys.modules:
        scraper_module = types.ModuleType('scraper_engine')

        class DummyScraper:
            def __init__(self, *_args, **_kwargs):
                self.driver = None

            def quit(self):
                return None

        scraper_module.Scraper = DummyScraper
        sys.modules['scraper_engine'] = scraper_module

    if 'ai_analyzer' not in sys.modules:
        analyzer_module = types.ModuleType('ai_analyzer')

        class DummyAnalyzer:
            def __init__(self, *_args, **_kwargs):
                pass

            def run_analysis(self, *_args, **_kwargs):
                return {"success": True, "verified_matches": [], "uncertain_matches": [], "message": "ok"}

            def run_analysis_stream(self, *_args, **_kwargs):
                yield {"type": "complete", "verified_matches": [], "uncertain_matches": [], "message": "ok"}

            def store_feedback(self, *_args, **_kwargs):
                return None

        analyzer_module.Analyzer = DummyAnalyzer
        sys.modules['ai_analyzer'] = analyzer_module


_stub_external_modules()
_prev_use_supabase_db = os.environ.get("USE_SUPABASE_DB")
os.environ["USE_SUPABASE_DB"] = "false"
import backend_server  # noqa: E402
from csv_manager import CSVManager  # noqa: E402


class BackendEventLogFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.download_root = self.base / "Source"
        self.verified_root = self.base / "Verified_Resumes"
        self.candidate_facts_cache_root = self.base / "candidate-facts-cache"
        self.rank = "Chief_Officer"
        self.rank_dir = self.download_root / self.rank
        self.rank_dir.mkdir(parents=True, exist_ok=True)

        backend_server.settings['Default_Download_Folder'] = str(self.download_root)
        backend_server.csv_manager = CSVManager(base_folder=str(self.verified_root))

        def fake_extract(pdf_path, candidate_id=None, match_reason=""):
            return {
                "candidate_id": str(candidate_id or ""),
                "resume": os.path.basename(pdf_path),
                "name": "Test Candidate",
                "present_rank": "Chief Officer",
                "email": "test@example.com",
                "country": "India",
                "mobile_no": "+911234567890",
                "ai_match_reason": match_reason,
                "extraction_status": "Success",
            }

        backend_server.resume_extractor.extract_resume_data = fake_extract
        backend_server.scraper_session = None
        backend_server.seajobs_last_activity_at = None
        self.prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(
            backend_server.feature_flags,
            use_supabase_db=False,
            use_dual_write=False,
            use_supabase_reads=False,
            use_local_agent=False,
            use_cloud_export=False,
        )
        self.prev_admin_token = os.environ.get("NJORDHR_ADMIN_TOKEN")
        os.environ["NJORDHR_ADMIN_TOKEN"] = "test-admin-token"
        self.prev_config_path = os.environ.get("NJORDHR_CONFIG_PATH")
        self.prev_candidate_facts_cache_dir = os.environ.get("NJORDHR_CANDIDATE_FACTS_CACHE_DIR")
        os.environ["NJORDHR_CANDIDATE_FACTS_CACHE_DIR"] = str(self.candidate_facts_cache_root)
        self.temp_config_path = str(self.base / "config.test.ini")
        with open(self.temp_config_path, "w", encoding="utf-8") as fh:
            backend_server.config.write(fh)
        os.environ["NJORDHR_CONFIG_PATH"] = self.temp_config_path
        backend_server.candidate_facts_repo = None
        self.client = backend_server.app.test_client()
        with self.client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["role"] = "admin"

    def tearDown(self):
        backend_server.scraper_session = None
        backend_server.seajobs_last_activity_at = None
        backend_server.feature_flags = self.prev_feature_flags
        if self.prev_admin_token is None:
            os.environ.pop("NJORDHR_ADMIN_TOKEN", None)
        else:
            os.environ["NJORDHR_ADMIN_TOKEN"] = self.prev_admin_token
        if self.prev_config_path is None:
            os.environ.pop("NJORDHR_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_CONFIG_PATH"] = self.prev_config_path
        if self.prev_candidate_facts_cache_dir is None:
            os.environ.pop("NJORDHR_CANDIDATE_FACTS_CACHE_DIR", None)
        else:
            os.environ["NJORDHR_CANDIDATE_FACTS_CACHE_DIR"] = self.prev_candidate_facts_cache_dir
        backend_server.candidate_facts_repo = None
        self.temp_dir.cleanup()
        backend_server.cloud_auth_state_cache.update({"ts": 0, "mode": "local", "reason": "not_checked"})

    def _write_fake_resume(self, filename):
        path = self.rank_dir / filename
        path.write_bytes(b"%PDF-1.4 fake resume content")
        return path

    def _read_master_csv(self):
        master = self.verified_root / "verified_resumes.csv"
        self.assertTrue(master.exists(), "Master CSV should exist")
        return pd.read_csv(master, keep_default_na=False)

    def _agent_settings_response(self, download_folder):
        class DummyResponse:
            status_code = 200

            def json(self_inner):
                return {"success": True, "settings": {"download_folder": str(download_folder)}}

        return DummyResponse()

    def _agent_json_response(self, payload, status_code=200):
        class DummyResponse:
            def __init__(self, body, code):
                self._body = body
                self.status_code = code

            def json(self_inner):
                return self_inner._body

        return DummyResponse(payload, status_code)

    def _candidate_facts_payload(self):
        return {
            "schema_version": "candidate_facts.v1",
            "source": {
                "resume_id": "candidate-resume-1",
                "candidate_id": "candidate-1",
                "source_origin": "manual_upload",
                "detected_layout": "unknown",
                "file_name": "resume.pdf",
                "content_hash": "abc123",
            },
            "identity": {
                "candidate_name": {
                    "value": "Jane Doe",
                    "presence": "observed_true",
                    "confidence": "high",
                    "evidence_ids": ["ev-1"],
                },
            },
            "rank": {
                "value": "2nd_engineer",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
            },
            "documents": [],
            "certificates": [],
            "endorsements": [],
            "courses": [],
            "contracts": [],
            "rank_experience": [],
            "engine_experience": [],
            "vessel_experience": [],
            "application": {"applied_ship_types": []},
            "derived": {},
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "source_kind": "raw_text_chunk",
                    "source_id": "resume-1/chunk-1",
                }
            ],
            "extraction": {
                "parser_version": "generic_pdf.v1",
                "status": "partial",
                "minimums_satisfied": [],
                "minimums_missing": [],
                "provenance": {
                    "mode": "semantic_chunk",
                    "raw_text_version": "v1",
                    "chunk_index_version": "v1",
                    "fallback_reason": "generic_fallback",
                },
                "warnings": ["generic_candidate_facts_fallback"],
            },
        }

    def test_initial_verification_logs_events_without_file_copy(self):
        self._write_fake_resume("Chief_Officer_1001.pdf")
        self._write_fake_resume("Chief_Officer_1002.pdf")
        self._write_fake_resume("Chief_Officer_1003.pdf")

        payload = {
            "rank_folder": self.rank,
            "filenames": [
                "Chief_Officer_1001.pdf",
                "Chief_Officer_1002.pdf",
                "Chief_Officer_1003.pdf",
            ],
            "match_data": {
                "Chief_Officer_1001.pdf": {"reason": "Matched A", "confidence": 0.9},
                "Chief_Officer_1002.pdf": {"reason": "Matched B", "confidence": 0.9},
            },
            "ai_prompt": "valid US visa and tanker experience",
        }

        resp = self.client.post("/verify_resumes", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["processed"], 3)
        self.assertEqual(body["csv_exports"], 3)

        df = self._read_master_csv()
        self.assertEqual(len(df), 3)
        self.assertEqual(set(df["Event_Type"].tolist()), {"initial_verification"})
        self.assertEqual(set(df["Candidate_ID"].astype(str).tolist()), {"1001", "1002", "1003"})

        self.assertFalse((self.verified_root / self.rank).exists(), "No physical verified resume folder should be created")

    def test_ui_vendor_assets_route_serves_local_runtime_js(self):
        resp = self.client.get("/ui_vendor/react.development.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"ReactVersion", resp.data)

    def test_status_and_notes_append_new_events(self):
        self._write_fake_resume("Chief_Officer_2001.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_2001.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        with self.client.session_transaction() as sess:
            sess["username"] = "recruiter"
            sess["role"] = "recruiter"

        status_resp = self.client.post("/update_status", json={"candidate_id": "2001", "status": "Contacted"})
        self.assertEqual(status_resp.status_code, 200)
        self.assertTrue(status_resp.get_json()["success"])

        notes_resp = self.client.post("/add_notes", json={"candidate_id": "2001", "notes": "Candidate replied by email"})
        self.assertEqual(notes_resp.status_code, 200)
        self.assertTrue(notes_resp.get_json()["success"])

        history_resp = self.client.get("/get_candidate_history/2001")
        self.assertEqual(history_resp.status_code, 200)
        history = history_resp.get_json()["history"]
        event_types = [row["Event_Type"] for row in history]
        self.assertIn("initial_verification", event_types)
        self.assertIn("status_change", event_types)
        self.assertIn("note_added", event_types)

    def test_status_transition_blocks_non_admin_revert_or_skip(self):
        self._write_fake_resume("Chief_Officer_2101.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_2101.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        with self.client.session_transaction() as sess:
            sess["username"] = "recruiter"
            sess["role"] = "recruiter"
        invalid_resp = self.client.post(
            "/update_status",
            json={"candidate_id": "2101", "status": "Mail Sent (handoff complete)"}
        )
        self.assertEqual(invalid_resp.status_code, 403)
        self.assertFalse(invalid_resp.get_json()["success"])

    def test_status_transition_admin_override_allowed(self):
        self._write_fake_resume("Chief_Officer_2201.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_2201.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        with self.client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["role"] = "admin"
        resp = self.client.post(
            "/update_status",
            json={"candidate_id": "2201", "status": "Mail Sent (handoff complete)"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    def test_dashboard_archive_split_and_rank_scope(self):
        self._write_fake_resume("Chief_Officer_2601.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_2601.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        with self.client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["role"] = "admin"
        self.client.post("/update_status", json={"candidate_id": "2601", "status": "Mail Sent (handoff complete)"})

        active_resp = self.client.get("/get_dashboard_data?view=master")
        self.assertEqual(active_resp.status_code, 200)
        active_data = active_resp.get_json().get("data", [])
        self.assertFalse(any(str(row.get("candidate_id")) == "2601" for row in active_data))

        archive_resp = self.client.get("/get_dashboard_data?view=archive")
        self.assertEqual(archive_resp.status_code, 200)
        archive_data = archive_resp.get_json().get("data", [])
        self.assertTrue(any(str(row.get("candidate_id")) == "2601" for row in archive_data))

        active_ranks = self.client.get("/get_available_ranks?scope=active").get_json().get("ranks", [])
        archive_ranks = self.client.get("/get_available_ranks?scope=archive").get_json().get("ranks", [])
        active_count = next((r["count"] for r in active_ranks if r["rank"] == self.rank), 0)
        archive_count = next((r["count"] for r in archive_ranks if r["rank"] == self.rank), 0)
        self.assertGreaterEqual(archive_count, 1)
        self.assertGreaterEqual(active_count, 0)

    def test_dashboard_archive_visible_to_admin_manager_recruiter(self):
        self._write_fake_resume("Chief_Officer_2602.pdf")
        with self.client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["role"] = "admin"
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_2602.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        self.client.post("/update_status", json={"candidate_id": "2602", "status": "Mail Sent (handoff complete)"})

        for role in ("admin", "manager", "recruiter"):
            with self.client.session_transaction() as sess:
                sess["username"] = role
                sess["role"] = role
            archive_resp = self.client.get("/get_dashboard_data?view=archive")
            self.assertEqual(archive_resp.status_code, 200)
            archive_data = archive_resp.get_json().get("data", [])
            self.assertTrue(any(str(row.get("candidate_id")) == "2602" for row in archive_data))
            ranks_resp = self.client.get("/get_available_ranks?scope=archive")
            self.assertEqual(ranks_resp.status_code, 200)

    def test_session_health_idle_timeout_disconnects_scraper_session(self):
        class FakeScraper:
            def __init__(self):
                self.driver = object()
                self.quit_called = False

            def quit(self):
                self.quit_called = True

        fake_scraper = FakeScraper()
        backend_server.scraper_session = fake_scraper
        backend_server.seajobs_last_activity_at = time.time() - 360

        resp = self.client.get("/session_health")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body.get("success"))
        self.assertFalse(body.get("connected"))
        self.assertIn("inactivity", str(body.get("message", "")).lower())
        self.assertTrue(fake_scraper.quit_called)
        self.assertIsNone(backend_server.scraper_session)

    def test_session_health_skips_idle_disconnect_for_local_agent(self):
        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        backend_server.scraper_session = object()
        backend_server.seajobs_last_activity_at = time.time() - 360

        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "success": True,
                    "health": {
                        "active": True,
                        "valid": True,
                        "otp_pending": False,
                        "otp_expired": False,
                        "reason": "Session valid",
                    },
                }

        try:
            with patch.object(backend_server, "_disconnect_seajobs_best_effort") as disconnect_mock, \
                patch("backend_server.requests.request", return_value=DummyResponse()) as request_mock:
                resp = self.client.get("/session_health")
        finally:
            backend_server.feature_flags = prev_feature_flags
            backend_server.scraper_session = None

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body.get("success"))
        self.assertTrue(body.get("connected"))
        disconnect_mock.assert_not_called()
        request_mock.assert_called_once()

    def test_logout_disconnects_seajobs_session(self):
        class FakeScraper:
            def __init__(self):
                self.driver = object()
                self.quit_called = False

            def quit(self):
                self.quit_called = True

        fake_scraper = FakeScraper()
        backend_server.scraper_session = fake_scraper
        backend_server.seajobs_last_activity_at = time.time()
        with self.client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["role"] = "admin"

        logout_resp = self.client.post("/auth/logout")
        self.assertEqual(logout_resp.status_code, 200)
        self.assertTrue(logout_resp.get_json().get("success"))
        self.assertTrue(fake_scraper.quit_called)
        self.assertIsNone(backend_server.scraper_session)

        me_resp = self.client.get("/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        self.assertFalse(me_resp.get_json().get("authenticated"))

    def test_reverify_same_candidate_logs_resume_updated(self):
        resume = self._write_fake_resume("Chief_Officer_3001.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_3001.pdf"],
            "match_data": {},
            "ai_prompt": "first",
        })

        resume.write_bytes(b"%PDF-1.4 updated fake resume content")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_3001.pdf"],
            "match_data": {},
            "ai_prompt": "second",
        })

        df = self._read_master_csv()
        candidate_rows = df[df["Candidate_ID"].astype(str) == "3001"]
        self.assertEqual(len(candidate_rows), 2)
        self.assertEqual(candidate_rows.iloc[0]["Event_Type"], "initial_verification")
        self.assertEqual(candidate_rows.iloc[1]["Event_Type"], "resume_updated")

    def test_reverify_new_filename_deletes_old_local_version(self):
        old_name = "Chief_Officer_3301_2026-02-25_10-00-00.pdf"
        new_name = "Chief_Officer_3301_2026-02-26_10-00-00.pdf"
        self._write_fake_resume(old_name)
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": [old_name],
            "match_data": {},
            "ai_prompt": "first",
        })
        self._write_fake_resume(new_name)
        resp = self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": [new_name],
            "match_data": {},
            "ai_prompt": "second",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(resp.get_json().get("stale_versions_deleted"), 1)
        self.assertFalse((self.rank_dir / old_name).exists())
        self.assertTrue((self.rank_dir / new_name).exists())

    def test_verify_resumes_requires_email_identifier(self):
        def missing_email_extract(pdf_path, candidate_id=None, match_reason=""):
            return {
                "candidate_id": str(candidate_id or ""),
                "resume": os.path.basename(pdf_path),
                "name": "Test Candidate",
                "present_rank": "Chief Officer",
                "email": "",
                "country": "India",
                "mobile_no": "+911234567890",
                "ai_match_reason": match_reason,
                "extraction_status": "Success",
            }

        backend_server.resume_extractor.extract_resume_data = missing_email_extract
        self._write_fake_resume("Chief_Officer_3401.pdf")
        resp = self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_3401.pdf"],
            "match_data": {},
            "ai_prompt": "prompt",
        })
        self.assertEqual(resp.status_code, 500)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("Missing required email", " ".join(body.get("errors", [])))

    def test_export_zip_contains_selected_csv_and_resumes(self):
        self._write_fake_resume("Chief_Officer_4001.pdf")
        self._write_fake_resume("Chief_Officer_4002.pdf")
        self.client.post("/verify_resumes", json={
            "rank_folder": self.rank,
            "filenames": ["Chief_Officer_4001.pdf", "Chief_Officer_4002.pdf"],
            "match_data": {},
            "ai_prompt": "export prompt",
        })

        resp = self.client.post("/export_resumes", json={"candidate_ids": ["4001", "4002"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/zip")

        zip_bytes = io.BytesIO(resp.data)
        with zipfile.ZipFile(zip_bytes) as archive:
            names = set(archive.namelist())
            self.assertIn("selected_candidates.csv", names)
            self.assertIn(f"resumes/{self.rank}/Chief_Officer_4001.pdf", names)
            self.assertIn(f"resumes/{self.rank}/Chief_Officer_4002.pdf", names)

            csv_data = archive.read("selected_candidates.csv").decode("utf-8")
            self.assertIn("Candidate_ID", csv_data)
            self.assertIn("4001", csv_data)
            self.assertIn("4002", csv_data)

    def test_session_health_reports_disconnected_without_session(self):
        backend_server.scraper_session = None
        resp = self.client.get("/session_health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["connected"])
        self.assertFalse(data["health"]["valid"])

    def test_session_health_reports_custom_scraper_health(self):
        class DummySession:
            driver = object()

            def get_session_health(self):
                return {
                    "active": True,
                    "valid": False,
                    "otp_pending": True,
                    "otp_expired": True,
                    "reason": "OTP expired"
                }

        backend_server.scraper_session = DummySession()
        resp = self.client.get("/session_health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["connected"])
        self.assertFalse(data["health"]["valid"])
        self.assertTrue(data["health"]["otp_expired"])

    def test_runtime_config_reports_backend_mode(self):
        fake_health = {
            "success": True,
            "status": "ok",
            "sync": {
                "pending": 1,
                "queue_path": "/tmp/pending_sync_queue.json",
                "reconnect_wakeup_pending": False,
                "last_resume_upload": {
                    "upload_status": "uploaded",
                    "resume_source": "cloud_synced",
                    "resume_upload_status": "uploaded",
                    "resume_storage_path": "storage://resumes/Chief_Officer/1001/resume.pdf",
                    "resume_checksum_sha256": "abc123",
                    "duplicate": False,
                    "message": "",
                },
            },
        }

        class DummyResponse:
            status_code = 200

            def json(self):
                return fake_health

        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        try:
            with patch("backend_server.requests.get", return_value=DummyResponse()) as mock_get:
                old_base = os.environ.get("NJORDHR_AGENT_BASE_URL")
                os.environ["NJORDHR_AGENT_BASE_URL"] = "http://127.0.0.1:5053"
                try:
                    resp = self.client.get("/config/runtime")
                finally:
                    if old_base is None:
                        os.environ.pop("NJORDHR_AGENT_BASE_URL", None)
                    else:
                        os.environ["NJORDHR_AGENT_BASE_URL"] = old_base
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn(data["persistence_backend"], {"csv", "supabase"})
        self.assertIn("feature_flags", data)
        self.assertIn("use_supabase_db", data["feature_flags"])
        self.assertIn("use_supabase_reads", data["feature_flags"])
        self.assertIn("cloud_api", data)
        self.assertIn("ready", data["cloud_api"])
        self.assertEqual(data["cloud_api"]["service_name"], "njordhr-cloud-api")
        self.assertIn("local_agent", data)
        self.assertEqual(data["local_agent"]["base_url"], "http://127.0.0.1:5053")
        self.assertEqual(data["local_agent"]["last_resume_upload"]["resume_storage_path"], "storage://resumes/Chief_Officer/1001/resume.pdf")
        self.assertEqual(data["local_agent"]["last_resume_upload"]["upload_status"], "uploaded")
        mock_get.assert_called_once()

    def test_runtime_config_exposes_local_agent_summary_without_session(self):
        fake_health = {
            "success": True,
            "status": "ok",
            "sync": {
                "pending": 0,
                "queue_path": "/tmp/pending_sync_queue.json",
                "reconnect_wakeup_pending": False,
                "last_resume_upload": {
                    "upload_status": "uploaded",
                    "resume_source": "cloud_synced",
                    "resume_upload_status": "uploaded",
                    "resume_storage_path": "storage://resumes/Chief_Officer/9002/live_upload_check_2.pdf",
                    "resume_checksum_sha256": "e3bec64fb8669f3ae7970c1672b9b1f368133a7c8fcf5d1e518f27e5386f892b",
                    "duplicate": False,
                    "message": "",
                },
            },
        }

        class DummyResponse:
            status_code = 200

            def json(self):
                return fake_health

        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        try:
            with self.client.session_transaction() as sess:
                sess.clear()
            with patch("backend_server.requests.get", return_value=DummyResponse()) as mock_get:
                resp = self.client.get("/config/runtime")
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("local_agent", data)
        self.assertTrue(data["local_agent"]["configured"])
        self.assertTrue(data["local_agent"]["reachable"])
        self.assertEqual(data["local_agent"]["last_resume_upload"]["resume_storage_path"], "storage://resumes/Chief_Officer/9002/live_upload_check_2.pdf")
        self.assertEqual(data["local_agent"]["last_resume_upload"]["upload_status"], "uploaded")
        self.assertIn("cloud_api", data)
        self.assertTrue(data["cloud_api"]["ready"])
        mock_get.assert_called_once()

    def test_build_analyzer_threads_feature_flags_into_analyzer(self):
        captured = {}

        class DummyAnalyzer:
            def __init__(self, config, *, feature_flags=None, store_bundle=None):
                captured["feature_flags"] = feature_flags
                captured["store_bundle"] = store_bundle
                captured["settings_folder"] = config.get("Settings", "Default_Download_Folder")

        original_analyzer = backend_server.Analyzer
        original_active_download_root = backend_server._active_download_root
        try:
            backend_server.Analyzer = DummyAnalyzer
            backend_server._active_download_root = lambda: self.download_root.as_posix()
            built = backend_server._build_analyzer()
            self.assertIsInstance(built, DummyAnalyzer)
            self.assertIs(captured["feature_flags"], backend_server.feature_flags)
            self.assertIsNone(captured["store_bundle"])
            self.assertEqual(captured["settings_folder"], self.download_root.as_posix())
        finally:
            backend_server.Analyzer = original_analyzer
            backend_server._active_download_root = original_active_download_root

    def test_runtime_ready_reports_unauthenticated_backend_identity(self):
        with self.client.session_transaction() as sess:
            sess.clear()

        old_port = os.environ.get("NJORDHR_PORT")
        old_agent_port = os.environ.get("NJORDHR_AGENT_RUNTIME_PORT")
        old_runtime_dir = os.environ.get("NJORDHR_RUNTIME_DIR")
        try:
            os.environ["NJORDHR_PORT"] = "5057"
            os.environ["NJORDHR_AGENT_RUNTIME_PORT"] = "5058"
            os.environ["NJORDHR_RUNTIME_DIR"] = self.temp_dir.name

            resp = self.client.get("/runtime/ready")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertTrue(data["backend_ready"])
            self.assertEqual(data["ports"]["backend_port"], 5057)
            self.assertEqual(data["ports"]["agent_port"], 5058)
            self.assertEqual(data["process_identity"]["runtime_dir"], os.path.abspath(self.temp_dir.name))
            self.assertEqual(data["process_identity"]["config_path"], os.path.abspath(self.temp_config_path))
            self.assertTrue(data["process_identity"]["project_dir"])
            self.assertIn("cloud_api", data)
            self.assertIn("ready_reason", data["cloud_api"])
        finally:
            if old_port is None:
                os.environ.pop("NJORDHR_PORT", None)
            else:
                os.environ["NJORDHR_PORT"] = old_port
            if old_agent_port is None:
                os.environ.pop("NJORDHR_AGENT_RUNTIME_PORT", None)
            else:
                os.environ["NJORDHR_AGENT_RUNTIME_PORT"] = old_agent_port
            if old_runtime_dir is None:
                os.environ.pop("NJORDHR_RUNTIME_DIR", None)
            else:
                os.environ["NJORDHR_RUNTIME_DIR"] = old_runtime_dir

    def test_runtime_ready_rejects_non_local_requests(self):
        resp = self.client.get("/runtime/ready", environ_base={"REMOTE_ADDR": "203.0.113.10"})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error"], "Forbidden")

    def test_local_agent_base_url_prefers_runtime_agent_fallbacks(self):
        old_base = os.environ.get("NJORDHR_AGENT_BASE_URL")
        old_url = os.environ.get("NJORDHR_AGENT_URL")
        old_runtime_port = os.environ.get("NJORDHR_AGENT_RUNTIME_PORT")
        old_agent_port = os.environ.get("NJORDHR_AGENT_PORT")
        try:
            os.environ.pop("NJORDHR_AGENT_BASE_URL", None)
            os.environ["NJORDHR_AGENT_URL"] = "http://127.0.0.1:5053"
            os.environ["NJORDHR_AGENT_RUNTIME_PORT"] = "5053"
            os.environ["NJORDHR_AGENT_PORT"] = "5051"
            self.assertEqual(backend_server._local_agent_base_url(), "http://127.0.0.1:5053")

            os.environ.pop("NJORDHR_AGENT_URL", None)
            self.assertEqual(backend_server._local_agent_base_url(), "http://127.0.0.1:5053")
        finally:
            if old_base is None:
                os.environ.pop("NJORDHR_AGENT_BASE_URL", None)
            else:
                os.environ["NJORDHR_AGENT_BASE_URL"] = old_base
            if old_url is None:
                os.environ.pop("NJORDHR_AGENT_URL", None)
            else:
                os.environ["NJORDHR_AGENT_URL"] = old_url
            if old_runtime_port is None:
                os.environ.pop("NJORDHR_AGENT_RUNTIME_PORT", None)
            else:
                os.environ["NJORDHR_AGENT_RUNTIME_PORT"] = old_runtime_port
            if old_agent_port is None:
                os.environ.pop("NJORDHR_AGENT_PORT", None)
            else:
                os.environ["NJORDHR_AGENT_PORT"] = old_agent_port

    def test_sensitive_endpoints_require_authentication(self):
        with self.client.session_transaction() as sess:
            sess.clear()

        verify_resp = self.client.post("/verify_resumes", json={"rank_folder": "X", "filenames": []})
        self.assertEqual(verify_resp.status_code, 403)
        self.assertFalse(verify_resp.get_json()["success"])

        resume_resp = self.client.get("/get_resume/Chief_Officer/Chief_Officer_9999.pdf")
        self.assertEqual(resume_resp.status_code, 403)

    def test_download_stream_reports_error_when_session_missing(self):
        self.client.post("/auth/login", json={"username": "admin", "password": "test-admin-token"})
        backend_server.scraper_session = None
        resp = self.client.get("/download_stream?rank=Chief_Officer&shipType=Bulk%20Carrier")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "error"', payload)
        self.assertIn("Website session is not active or has expired", payload)

    def test_download_stream_emits_complete_event_for_valid_session(self):
        self.client.post("/auth/login", json={"username": "admin", "password": "test-admin-token"})
        class DummySession:
            driver = object()

            def get_session_health(self):
                return {
                    "active": True,
                    "valid": True,
                    "otp_pending": False,
                    "otp_expired": False,
                    "reason": "Session valid"
                }

            def download_resumes(self, rank, ship_type, force_redownload, logger):
                logger.info(f"Downloading for {rank} / {ship_type} force={force_redownload}")
                return {"success": True, "message": "Download done", "log": []}

        backend_server.scraper_session = DummySession()
        resp = self.client.get("/download_stream?rank=Chief_Officer&shipType=Bulk%20Carrier&forceRedownload=true")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "started"', payload)
        self.assertIn('"type": "log"', payload)
        self.assertIn('"type": "complete"', payload)
        self.assertIn('"success": true', payload.lower())

    def test_resume_upload_pipeline_records_checksum_storage_and_status(self):
        file_bytes = b"%PDF-1.4 uploaded resume bytes"
        expected_checksum = hashlib.sha256(file_bytes).hexdigest()
        metadata = {
            "job_id": "job-123",
            "candidate_external_id": "1001",
            "rank_applied_for": "Chief Officer",
            "device_id": "device-123",
        }

        captured = {}

        def fake_append(kind, payload):
            captured["kind"] = kind
            captured["payload"] = payload

        with patch.object(
            backend_server,
            "_supabase_storage_upload",
            return_value=("storage://resumes/Chief_Officer/1001/Chief_Officer_1001.pdf", ""),
        ) as upload_mock, patch.object(backend_server, "_append_agent_sync_jsonl", side_effect=fake_append):
            resp = self.client.post(
                "/api/agent/resume-upload",
                data={
                    "metadata": json.dumps(metadata),
                    "file": (io.BytesIO(file_bytes), "Chief_Officer_1001.pdf"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["upload_status"], "uploaded")
        self.assertEqual(body["resume_source"], "cloud_synced")
        self.assertEqual(body["resume_upload_status"], "uploaded")
        self.assertEqual(body["resume_storage_path"], "storage://resumes/Chief_Officer/1001/Chief_Officer_1001.pdf")
        self.assertEqual(body["resume_checksum_sha256"], expected_checksum)
        upload_mock.assert_called_once()
        args, _kwargs = upload_mock.call_args
        self.assertEqual(args[1], "Chief_Officer/1001/Chief_Officer_1001.pdf")
        self.assertEqual(captured["kind"], "resume_upload")
        self.assertEqual(captured["payload"]["resume_storage_path"], "storage://resumes/Chief_Officer/1001/Chief_Officer_1001.pdf")
        self.assertEqual(captured["payload"]["resume_upload_status"], "uploaded")
        self.assertEqual(captured["payload"]["resume_checksum_sha256"], expected_checksum)
        self.assertEqual(captured["payload"]["resume_source"], "cloud_synced")
        self.assertEqual(captured["payload"]["object_path"], "Chief_Officer/1001/Chief_Officer_1001.pdf")

    def test_download_stream_forwards_local_agent_progress_events(self):
        queued = backend_server._translate_agent_stream_event({
            "type": "queued",
            "message": "Job queued",
            "data": {"stage": "queued", "percent": 0},
        })
        running = backend_server._translate_agent_stream_event({
            "type": "running",
            "message": "Job started",
            "data": {"stage": "running", "percent": 5},
        })
        progress = backend_server._translate_agent_stream_event({
            "type": "progress",
            "message": "Validated session",
            "data": {"stage": "preflight", "percent": 10},
        })
        log_event = backend_server._translate_agent_stream_event({
            "type": "log",
            "message": "Downloading for Chief Officer / Bulk Carrier",
            "data": {"level": "INFO"},
        })
        complete = backend_server._translate_agent_stream_event({
            "type": "complete",
            "message": "Download done",
            "data": {"result": {"success": True, "message": "Download done", "saved_files": []}},
        })

        self.assertEqual(queued["type"], "progress")
        self.assertEqual(queued["stage"], "queued")
        self.assertEqual(running["percent"], 5)
        self.assertEqual(progress["stage"], "preflight")
        self.assertEqual(log_event["type"], "log")
        self.assertEqual(log_event["line"], "Downloading for Chief Officer / Bulk Carrier")
        self.assertEqual(complete["type"], "complete")
        self.assertTrue(complete["success"])

    def test_get_rank_folder_ship_types_reads_manifest_metadata(self):
        manifest_path = self.rank_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "version": 1,
            "files": {
                "Chief_Officer_1001.pdf": {
                    "candidate_id": "1001",
                    "rank": self.rank,
                    "applied_ship_types": ["Bulk Carrier", "Tanker"],
                },
                "Chief_Officer_1002.pdf": {
                    "candidate_id": "1002",
                    "rank": self.rank,
                    "applied_ship_types": ["Bulk Carrier"],
                },
            }
        }), encoding="utf-8")

        resp = self.client.get(f"/get_rank_folder_ship_types?rank_folder={self.rank}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["ship_types"], ["Bulk Carrier", "Tanker"])

    def test_get_rank_folder_summaries_reports_pdf_counts(self):
        (self.rank_dir / "Chief_Officer_1001.pdf").write_bytes(b"%PDF-1.4")
        (self.rank_dir / "Chief_Officer_1002.pdf").write_bytes(b"%PDF-1.4")
        (self.rank_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
        other_rank = self.download_root / "2nd_Engineer"
        other_rank.mkdir(parents=True, exist_ok=True)
        (other_rank / "2nd_Engineer_2001.pdf").write_bytes(b"%PDF-1.4")

        resp = self.client.get("/get_rank_folder_summaries")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        summaries = {row["folder"]: row["pdf_count"] for row in body["folders"]}
        self.assertEqual(summaries["Chief_Officer"], 2)
        self.assertEqual(summaries["2nd_Engineer"], 1)

    def test_get_rank_folder_files_returns_sorted_pdfs_only(self):
        (self.rank_dir / "Chief_Officer_1002.pdf").write_bytes(b"%PDF-1.4")
        (self.rank_dir / "Chief_Officer_1001.pdf").write_bytes(b"%PDF-1.4")
        (self.rank_dir / "notes.txt").write_text("ignore me", encoding="utf-8")

        resp = self.client.get("/get_rank_folder_files?rank_folder=Chief_Officer")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["files"], ["Chief_Officer_1001.pdf", "Chief_Officer_1002.pdf"])

    def test_rank_folder_endpoints_prefer_local_agent_download_root(self):
        legacy_rank = self.download_root / "Chief_Officer"
        (legacy_rank / "Legacy.pdf").write_bytes(b"%PDF-1.4")

        agent_root = self.base / "AgentResumes"
        agent_rank = agent_root / "Chief_Officer"
        agent_rank.mkdir(parents=True, exist_ok=True)
        (agent_rank / "EmailResume.pdf").write_bytes(b"%PDF-1.4")

        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)

        with patch.object(
            backend_server,
            "_agent_request",
            return_value=self._agent_settings_response(agent_root),
        ):
            folders_resp = self.client.get("/get_rank_folders")
            files_resp = self.client.get("/get_rank_folder_files?rank_folder=Chief_Officer")
            summaries_resp = self.client.get("/get_rank_folder_summaries")

        self.assertEqual(folders_resp.status_code, 200)
        self.assertEqual(files_resp.status_code, 200)
        self.assertEqual(summaries_resp.status_code, 200)

        self.assertEqual(folders_resp.get_json()["folders"], ["Chief_Officer"])
        self.assertEqual(files_resp.get_json()["files"], ["EmailResume.pdf"])
        summaries = {row["folder"]: row["pdf_count"] for row in summaries_resp.get_json()["folders"]}
        self.assertEqual(summaries, {"Chief_Officer": 1})

    def test_preview_downloaded_resume_proxies_through_agent_when_local_agent_enabled(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        captured = {}

        class DummyResponse:
            status_code = 200
            content = b"%PDF-1.4 proxied preview"
            headers = {"Content-Type": "application/pdf"}
            text = ""

        def fake_requests_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            return DummyResponse()

        with patch.dict(os.environ, {"NJORDHR_AGENT_SYNC_TOKEN": "agent-preview-token"}, clear=False):
            with patch.object(backend_server.requests, "request", side_effect=fake_requests_request):
                resp = self.client.get(
                    "/preview_downloaded_resume/Chief_Officer/Chief-Officer_Bulk-Carrier_1001.pdf"
                )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"%PDF-1.4 proxied preview")
        self.assertEqual(captured["method"], "GET")
        self.assertIn("/preview_downloaded_resume/Chief_Officer/Chief-Officer_Bulk-Carrier_1001.pdf", captured["url"])
        self.assertEqual(captured["headers"].get("X-Device-Token"), "agent-preview-token")

    def test_admin_settings_loads_and_saves_mailbox_intake_settings_via_local_agent(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            if method == "GET" and path == "/settings":
                return self._agent_json_response({
                    "success": True,
                    "settings": {
                        "email_intake_enabled": True,
                        "email_intake_mailbox": "recruitment@njordships.com",
                        "email_intake_monitored_folder": "Inbox/NjordHR Resumes",
                        "email_intake_processed_folder": "Inbox/NjordHR Processed",
                        "email_intake_failed_folder": "Inbox/NjordHR Failed",
                        "email_intake_poll_interval_seconds": 90,
                        "outlook_client_id": "client-123",
                        "outlook_tenant_id": "organizations",
                    },
                })
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
            get_resp = self.client.get("/admin/settings", headers={"X-Admin-Token": "test-admin-token"})
            post_resp = self.client.post(
                "/admin/settings",
                headers={"X-Admin-Token": "test-admin-token"},
                json={"settings": {
                    "email_intake_enabled": True,
                    "email_intake_mailbox": "crewing@example.com",
                    "email_intake_monitored_folder": "Inbox/NjordHR Resumes",
                    "email_intake_poll_interval_seconds": "120",
                    "outlook_client_id": "client-456",
                    "outlook_tenant_id": "organizations",
                }},
            )

        self.assertEqual(get_resp.status_code, 200)
        non_secret = get_resp.get_json()["settings"]["non_secret"]
        self.assertEqual(non_secret["email_intake_mailbox"], "recruitment@njordships.com")
        self.assertEqual(non_secret["outlook_client_id"], "client-123")
        self.assertEqual(post_resp.status_code, 200)
        put_calls = [call for call in captured if call[0] == "PUT" and call[1] == "/settings"]
        self.assertEqual(len(put_calls), 1)
        agent_payload = put_calls[0][2]
        self.assertEqual(agent_payload["email_intake_mailbox"], "crewing@example.com")
        self.assertEqual(agent_payload["outlook_client_id"], "client-456")

    def test_admin_settings_propagates_download_folder_to_local_agent(self):
        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        try:
            with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
                resp = self.client.post(
                    "/admin/settings",
                    headers={"X-Admin-Token": "test-admin-token"},
                    json={"settings": {
                        "default_download_folder": str(self.download_root),
                        "use_local_agent": True,
                    }},
                )
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertIn(("PUT", "/settings/download-folder", {"download_folder": str(self.download_root)}), captured)

    def test_admin_settings_same_request_can_enable_local_agent_and_mailbox(self):
        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=False)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            if method == "GET" and path == "/settings":
                return self._agent_json_response({
                    "success": True,
                    "settings": {
                        "email_intake_enabled": False,
                    },
                })
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        try:
            with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
                resp = self.client.post(
                    "/admin/settings",
                    headers={"X-Admin-Token": "test-admin-token"},
                    json={"settings": {
                        "use_local_agent": True,
                        "email_intake_enabled": True,
                        "email_intake_mailbox": "crewing@example.com",
                        "email_intake_monitored_folder": "Inbox/NjordHR Resumes",
                    }},
                )
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        put_calls = [call for call in captured if call[0] == "PUT" and call[1] == "/settings"]
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(put_calls[0][2]["email_intake_mailbox"], "crewing@example.com")
        self.assertEqual(put_calls[0][2]["email_intake_enabled"], True)

    def test_admin_settings_reports_agent_folder_sync_warning_without_failing_backend_save(self):
        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            if method == "PUT" and path == "/settings/download-folder":
                raise OSError("agent unavailable")
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        try:
            with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
                resp = self.client.post(
                    "/admin/settings",
                    headers={"X-Admin-Token": "test-admin-token"},
                    json={"settings": {
                        "default_download_folder": str(self.download_root),
                        "use_local_agent": True,
                    }},
                )
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertIn("warnings", body)
        self.assertTrue(body["warnings"])
        self.assertIn("agent unavailable", body["warnings"][0])

    def test_admin_settings_string_false_does_not_enable_local_agent(self):
        prev_feature_flags = backend_server.feature_flags
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=False)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        try:
            with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
                resp = self.client.post(
                    "/admin/settings",
                    headers={"X-Admin-Token": "test-admin-token"},
                    json={"settings": {
                        "use_local_agent": "false",
                        "default_download_folder": str(self.download_root),
                    }},
                )
        finally:
            backend_server.feature_flags = prev_feature_flags

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(captured, [])

    def test_admin_settings_does_not_clear_mailbox_settings_with_blank_form_values(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            return self._agent_json_response({"success": True, "settings": json_body or {}})

        with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
            resp = self.client.post(
                "/admin/settings",
                headers={"X-Admin-Token": "test-admin-token"},
                json={"settings": {
                    "email_intake_enabled": False,
                    "email_intake_mailbox": "",
                    "outlook_client_id": "",
                    "outlook_tenant_id": "",
                }},
            )

        self.assertEqual(resp.status_code, 200)
        put_calls = [call for call in captured if call[0] == "PUT" and call[1] == "/settings"]
        self.assertEqual(len(put_calls), 1)
        agent_payload = put_calls[0][2]
        self.assertEqual(agent_payload, {"email_intake_enabled": False})

    def test_mailbox_connect_reports_missing_agent_configuration_before_auth_start(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)
        captured = []

        def fake_agent_request(method, path, json_body=None, **_kwargs):
            captured.append((method, path, json_body))
            if method == "GET" and path == "/email-intake/auth/status":
                return self._agent_json_response({
                    "success": True,
                    "auth": {
                        "mailbox": "",
                        "client_id_present": False,
                    },
                })
            return self._agent_json_response({"success": True})

        with patch.object(backend_server, "_agent_request", side_effect=fake_agent_request):
            resp = self.client.post("/email-intake/auth/start", json={})

        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("Mailbox and Outlook Client ID missing", body["message"])
        self.assertFalse(any(call[0] == "POST" and call[1] == "/email-intake/auth/start" for call in captured))

    def test_get_rank_options_uses_live_agent_folders_when_available(self):
        agent_root = self.base / "AgentResumes"
        (agent_root / "OS").mkdir(parents=True, exist_ok=True)
        (agent_root / "AB").mkdir(parents=True, exist_ok=True)
        (agent_root / "_EmailInbox_ManualReview").mkdir(parents=True, exist_ok=True)
        (agent_root / "OS" / "resume.pdf").write_bytes(b"%PDF-1.4")
        (agent_root / "AB" / "resume.pdf").write_bytes(b"%PDF-1.4")
        (agent_root / "_EmailInbox_ManualReview" / "manual.pdf").write_bytes(b"%PDF-1.4")

        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)

        with patch.object(
            backend_server,
            "_agent_request",
            return_value=self._agent_settings_response(agent_root),
        ):
            resp = self.client.get("/get_rank_options")

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["source"], "active_download_root")
        self.assertEqual(body["ranks"], ["AB", "OS"])

    def test_download_results_summary_uses_live_agent_root_and_separates_manual_review(self):
        agent_root = self.base / "AgentResumes"
        os_rank = agent_root / "OS"
        os_rank.mkdir(parents=True, exist_ok=True)
        (os_rank / "EMAIL_resume.pdf").write_bytes(b"%PDF-1.4")
        (os_rank / "legacy.pdf").write_bytes(b"%PDF-1.4")
        manual_review = agent_root / "_EmailInbox_ManualReview"
        manual_review.mkdir(parents=True, exist_ok=True)
        (manual_review / "manual.pdf").write_bytes(b"%PDF-1.4")

        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=True)

        with patch.object(
            backend_server,
            "_agent_request",
            return_value=self._agent_settings_response(agent_root),
        ):
            resp = self.client.get("/get_download_results_summary")

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["mailbox"]["successfully_routed"], 1)
        self.assertEqual(body["mailbox"]["manual_review_count"], 1)
        self.assertEqual(body["mailbox"]["role_counts"], [{"folder": "OS", "count": 1}])
        self.assertEqual(body["online"]["total_downloaded"], 1)
        self.assertEqual(body["online"]["role_counts"], [{"folder": "OS", "count": 1}])

    def test_analyze_stream_forwards_applied_ship_type_to_analyzer(self):
        self._write_fake_resume("Chief_Officer_1001.pdf")
        captured = {}

        class CaptureAnalyzer:
            def __init__(self, *_args, **_kwargs):
                pass

            def run_analysis_stream(self, rank_folder, prompt, applied_ship_type=None, experienced_ship_type=None):
                captured["rank_folder"] = rank_folder
                captured["prompt"] = prompt
                captured["applied_ship_type"] = applied_ship_type
                captured["experienced_ship_type"] = experienced_ship_type
                yield {
                    "type": "complete",
                    "verified_matches": [],
                    "uncertain_matches": [],
                    "unknown_matches": [],
                    "hard_filter_summary": {"scanned": 0, "passed": 0, "failed": 0, "unknown": 0, "matched": 0},
                    "message": "ok",
                }

        with patch.object(backend_server, "Analyzer", CaptureAnalyzer):
            resp = self.client.get(
                "/analyze_stream?rank_folder=Chief_Officer&prompt=show%20candidates&applied_ship_type=Bulk%20Carrier&experienced_ship_type=Tanker"
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "complete"', payload)
        self.assertEqual(captured["rank_folder"], "Chief_Officer")
        self.assertEqual(captured["prompt"], "show candidates")
        self.assertEqual(captured["applied_ship_type"], "Bulk Carrier")
        self.assertEqual(captured["experienced_ship_type"], "Tanker")

    def test_analyze_stream_logs_hard_filter_audit_rows(self):
        self._write_fake_resume("Chief_Officer_1001.pdf")
        self._write_fake_resume("Chief_Officer_1002.pdf")

        class CaptureAnalyzer:
            def __init__(self, *_args, **_kwargs):
                pass

            def run_analysis_stream(self, rank_folder, prompt, applied_ship_type=None, experienced_ship_type=None):
                yield {
                    "type": "complete",
                    "verified_matches": [],
                    "uncertain_matches": [],
                    "unknown_matches": [],
                    "hard_filter_audit": [
                        {
                            "candidate_id": "1001",
                            "filename": "Chief_Officer_1001.pdf",
                            "facts_version": "1.1",
                            "hard_filter_decision": "FAIL",
                            "hard_filter_reasons": [
                                {
                                    "reason_code": "US_VISA_EXPIRED",
                                    "message": "Visa US Visa (USA) expired on 2023-02-09.",
                                }
                            ],
                            "llm_reached": False,
                            "result_bucket": "excluded",
                        },
                        {
                            "candidate_id": "1002",
                            "filename": "Chief_Officer_1002.pdf",
                            "facts_version": "2.0",
                            "hard_filter_decision": "UNKNOWN",
                            "hard_filter_reasons": [
                                {
                                    "reason_code": "VISA_FILTER_UNSUPPORTED",
                                    "message": "Requested filter 'valid UK visa' is not yet supported by the deterministic visa parser.",
                                }
                            ],
                            "llm_reached": False,
                            "result_bucket": "needs_review",
                        },
                    ],
                    "hard_filter_summary": {"scanned": 2, "passed": 0, "failed": 1, "unknown": 1, "matched": 0},
                    "message": "ok",
                }

        with patch.object(backend_server, "Analyzer", CaptureAnalyzer):
            resp = self.client.get(
                "/analyze_stream?rank_folder=Chief_Officer&prompt=having%20valid%20UK%20visa&applied_ship_type=Bulk%20Carrier&experienced_ship_type=Tanker"
            )

        self.assertEqual(resp.status_code, 200)
        audit_rows = backend_server.csv_manager.get_ai_search_audit_rows()
        self.assertEqual(len(audit_rows), 2)
        self.assertEqual(audit_rows[0]["Candidate_ID"], "1001")
        self.assertEqual(audit_rows[0]["Facts_Version"], "1.1")
        self.assertEqual(audit_rows[0]["Hard_Filter_Decision"], "FAIL")
        self.assertEqual(audit_rows[0]["Reason_Codes"], "US_VISA_EXPIRED")
        self.assertEqual(audit_rows[0]["Result_Bucket"], "excluded")
        self.assertEqual(audit_rows[1]["Facts_Version"], "2.0")
        self.assertEqual(audit_rows[1]["Hard_Filter_Decision"], "UNKNOWN")
        self.assertEqual(audit_rows[1]["Reason_Codes"], "VISA_FILTER_UNSUPPORTED")
        self.assertEqual(audit_rows[1]["Applied_Ship_Type_Filter"], "Bulk Carrier")
        self.assertEqual(audit_rows[1]["Experienced_Ship_Type_Filter"], "Tanker")
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "complete"', payload)
        self.assertNotIn('"hard_filter_audit"', payload)

    def test_admin_settings_requires_token(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        resp = self.client.get("/admin/settings")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.get_json()["success"])

    def test_admin_settings_save_applies_flags(self):
        resp = self.client.post(
            "/admin/settings",
            headers={"X-Admin-Token": "test-admin-token"},
            json={
                "settings": {
                    "use_supabase_db": True,
                    "use_dual_write": True,
                    "use_supabase_reads": False,
                    "supabase_url": "https://example.supabase.co",
                    "supabase_secret_key": "sb_secret_test",
                    "min_similarity_score": "0.31",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        runtime_flags = body["runtime"]["feature_flags"]
        self.assertTrue(runtime_flags["use_supabase_db"])
        self.assertTrue(runtime_flags["use_dual_write"])
        self.assertFalse(runtime_flags["use_supabase_reads"])

    def test_admin_folder_browser_lists_directories(self):
        browse_root = self.base / "browse_root"
        (browse_root / "A").mkdir(parents=True, exist_ok=True)
        (browse_root / "B").mkdir(parents=True, exist_ok=True)

        resp = self.client.get(
            f"/admin/fs/list?path={browse_root}",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        names = [item["name"] for item in body.get("entries", [])]
        self.assertIn("A", names)
        self.assertIn("B", names)

    def test_admin_folder_browser_exposes_windows_drive_roots(self):
        with patch.object(backend_server, "_is_windows", return_value=True), \
             patch.object(
                 backend_server,
                 "_list_windows_drive_entries",
                 return_value=[
                     {"name": "C:\\", "path": "C:\\"},
                     {"name": "D:\\", "path": "D:\\"},
                 ],
             ):
            resp = self.client.get(
                "/admin/fs/list",
                headers={"X-Admin-Token": "test-admin-token"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        names = [item["name"] for item in body.get("entries", [])]
        self.assertIn("C:\\", names)
        self.assertIn("D:\\", names)

    def test_get_rank_folders_excludes_hidden_directories(self):
        self._write_fake_resume("Chief_Officer_1001.pdf")
        hidden = self.download_root / ".git"
        hidden.mkdir(parents=True, exist_ok=True)
        stray = self.download_root / "git"
        stray.mkdir(parents=True, exist_ok=True)

        resp = self.client.get(
            "/get_rank_folders",
            headers={"X-Admin-Token": "test-admin-token"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertIn(self.rank, body["folders"])
        self.assertNotIn(".git", body["folders"])
        self.assertNotIn("git", body["folders"])

    def test_analyze_stream_still_completes_when_audit_logging_fails(self):
        self._write_fake_resume("Chief_Officer_1001.pdf")

        class CaptureAnalyzer:
            def __init__(self, *_args, **_kwargs):
                pass

            def run_analysis_stream(self, rank_folder, prompt, applied_ship_type=None, experienced_ship_type=None):
                yield {
                    "type": "complete",
                    "verified_matches": [
                        {
                            "filename": "Chief_Officer_1001.pdf",
                            "reason": "Match found.",
                            "confidence": 0.91,
                        }
                    ],
                    "uncertain_matches": [],
                    "unknown_matches": [],
                    "hard_filter_audit": [
                        {
                            "candidate_id": "1001",
                            "filename": "Chief_Officer_1001.pdf",
                            "hard_filter_decision": "PASS",
                            "hard_filter_reasons": [],
                            "llm_reached": True,
                            "result_bucket": "verified_match",
                        }
                    ],
                    "hard_filter_summary": {"scanned": 1, "passed": 1, "failed": 0, "unknown": 0, "matched": 1},
                    "message": "ok",
                }

        with patch.object(backend_server, "Analyzer", CaptureAnalyzer), \
             patch.object(backend_server.csv_manager, "log_ai_search_audit", side_effect=RuntimeError("disk busy")):
            resp = self.client.get(
                "/analyze_stream?rank_folder=Chief_Officer&prompt=having%20valid%20US%20visa"
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "complete"', payload)
        self.assertIn('"verified_matches"', payload)

    def test_admin_settings_rejects_invalid_otp_window(self):
        resp = self.client.post(
            "/admin/settings",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"settings": {"otp_window_seconds": "10"}},
        )
        self.assertEqual(resp.status_code, 400)

    def test_cloud_auth_unavailable_returns_503_instead_of_local_fallback(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_supabase_db=True)
        backend_server.cloud_auth_state_cache.update({"ts": 0, "mode": "local", "reason": "not_checked"})
        with patch("backend_server._supabase_users_request", side_effect=RuntimeError("network down")):
            resp = self.client.post("/auth/login", json={"username": "admin", "password": "x"})
        self.assertEqual(resp.status_code, 503)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("Cloud auth unavailable", body["message"])

    def test_get_resume_disabled_in_cloud_mode(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_supabase_db=True)
        resp = self.client.get("/get_resume/Chief_Officer/Chief_Officer_9999.pdf")
        self.assertEqual(resp.status_code, 410)

    def test_open_resume_requires_storage_url_in_cloud_mode(self):
        backend_server.feature_flags = replace(backend_server.feature_flags, use_supabase_db=True)
        resp = self.client.get("/open_resume?rank_folder=Chief_Officer&filename=Chief_Officer_9999.pdf")
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])

    def _write_runtime_config(self):
        with open(self.temp_config_path, "w", encoding="utf-8") as fh:
            backend_server.config.write(fh)

    def _reset_users_for_bootstrap(self):
        if "Users" in backend_server.config:
            backend_server.config.remove_section("Users")
        if "Auth" not in backend_server.config:
            backend_server.config["Auth"] = {}
        if "Advanced" not in backend_server.config:
            backend_server.config["Advanced"] = {}
        for key in (
            "admin_password",
            "manager_password",
            "recruiter_password",
            "admin_username",
            "manager_username",
            "recruiter_username",
        ):
            backend_server.config["Auth"][key] = ""
        backend_server.config["Advanced"]["admin_token"] = ""
        self._write_runtime_config()

    def test_bootstrap_status_required_when_no_valid_users(self):
        self._reset_users_for_bootstrap()
        old_token = os.environ.pop("NJORDHR_ADMIN_TOKEN", None)
        try:
            resp = self.client.get("/auth/bootstrap_status")
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            self.assertTrue(body["success"])
            self.assertTrue(body["bootstrap_required"])
            self.assertIn(body.get("reason"), {"no_users", "placeholder_only"})
        finally:
            if old_token is not None:
                os.environ["NJORDHR_ADMIN_TOKEN"] = old_token

    def test_login_blocked_until_bootstrap(self):
        self._reset_users_for_bootstrap()
        old_token = os.environ.pop("NJORDHR_ADMIN_TOKEN", None)
        try:
            resp = self.client.post("/auth/login", json={"username": "admin", "password": "anything"})
            self.assertEqual(resp.status_code, 403)
            body = resp.get_json()
            self.assertFalse(body["success"])
            self.assertIn("bootstrap", body.get("message", "").lower())
        finally:
            if old_token is not None:
                os.environ["NJORDHR_ADMIN_TOKEN"] = old_token

    def test_bootstrap_creates_admin_once_and_enables_login(self):
        self._reset_users_for_bootstrap()
        old_token = os.environ.pop("NJORDHR_ADMIN_TOKEN", None)
        try:
            bootstrap = self.client.post("/auth/bootstrap", json={
                "admin_username": "firstadmin",
                "admin_password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            })
            self.assertEqual(bootstrap.status_code, 200)
            body = bootstrap.get_json()
            self.assertTrue(body["success"])
            self.assertEqual(body["user"]["username"], "firstadmin")
            self.assertEqual(body["user"]["role"], "admin")

            second = self.client.post("/auth/bootstrap", json={
                "admin_username": "another",
                "admin_password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            })
            self.assertEqual(second.status_code, 409)
            self.assertFalse(second.get_json()["success"])

            self.client.post("/auth/logout")
            login = self.client.post("/auth/login", json={"username": "firstadmin", "password": "StrongPass123!"})
            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.get_json()["success"])
        finally:
            if old_token is not None:
                os.environ["NJORDHR_ADMIN_TOKEN"] = old_token

    def test_cloud_auth_login_uses_password_hash(self):
        with patch.object(
            backend_server,
            "_cloud_auth_state",
            return_value={"ts": time.time(), "mode": "cloud", "reason": "ok"},
        ), patch.object(
            backend_server,
            "_auth_user_list_cloud",
            return_value={
                "cloudadmin": {
                    "role": "admin",
                    "password_hash": generate_password_hash("SecretPass123!"),
                    "id": "x",
                    "email": "cloudadmin@njordhr.local",
                }
            },
        ):
            resp = self.client.post("/auth/login", json={"username": "cloudadmin", "password": "SecretPass123!"})
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            self.assertTrue(body["success"])
            self.assertEqual(body["user"]["role"], "admin")

    def test_cloud_auth_no_users_returns_actionable_login_message(self):
        with patch.object(
            backend_server,
            "_cloud_auth_state",
            return_value={"ts": time.time(), "mode": "cloud", "reason": "ok"},
        ), patch.object(
            backend_server,
            "_auth_user_list_cloud",
            return_value={},
        ):
            resp = self.client.post("/auth/login", json={"username": "admin", "password": "x"})
            self.assertEqual(resp.status_code, 403)
            body = resp.get_json()
            self.assertFalse(body["success"])
            self.assertIn("no users are configured", body["message"].lower())

    def test_candidate_facts_review_capture_approve_and_promote(self):
        payload = {
            "candidate_resume_id": "candidate-resume-1",
            "resume_blob_id": "blob-1",
            "parser_version": "generic_pdf.v1",
            "facts_revision": "rev-1",
            "candidate_facts": self._candidate_facts_payload(),
        }

        capture_resp = self.client.post("/candidate-facts/review/capture", json=payload)
        self.assertEqual(capture_resp.status_code, 200)
        capture_body = capture_resp.get_json()
        self.assertTrue(capture_body["success"])
        self.assertEqual(capture_body["review_item"]["review_status"], "pending_review")

        items_resp = self.client.get("/candidate-facts/review/items")
        self.assertEqual(items_resp.status_code, 200)
        items = items_resp.get_json()["items"]
        self.assertEqual(len(items), 1)
        self.assertNotIn("candidate_facts", items[0])
        self.assertIn("candidate_facts_summary", items[0])
        item_id = items[0]["id"]

        approve_resp = self.client.post("/candidate-facts/review/approve", json={
            "id": item_id,
            "reviewed_by": "reviewer",
            "review_notes": "looks good",
        })
        self.assertEqual(approve_resp.status_code, 200)
        self.assertEqual(approve_resp.get_json()["review_item"]["review_status"], "approved")

        promote_resp = self.client.post("/candidate-facts/review/promote", json={"id": item_id})
        self.assertEqual(promote_resp.status_code, 200)
        promote_body = promote_resp.get_json()
        self.assertTrue(promote_body["success"])
        self.assertTrue(promote_body["persist"]["committed"])
        self.assertEqual(promote_body["review_item"]["persistence_status"], "persisted")

    def test_candidate_facts_review_promote_surfaces_supabase_sync_warnings(self):
        class _FailingSupabaseStore:
            def promote_candidate_resume_facts_row(self, _row):
                raise RuntimeError("simulated supabase outage")

        repo = backend_server._candidate_facts_repository()
        repo.supabase_store = _FailingSupabaseStore()
        backend_server.candidate_facts_repo = repo

        payload = {
            "candidate_resume_id": "candidate-resume-1b",
            "resume_blob_id": "blob-1b",
            "parser_version": "generic_pdf.v1",
            "facts_revision": "rev-1",
            "candidate_facts": self._candidate_facts_payload(),
        }
        capture_resp = self.client.post("/candidate-facts/review/capture", json=payload)
        self.assertEqual(capture_resp.status_code, 200)
        item_id = capture_resp.get_json()["review_item"]["id"]
        self.assertEqual(
            self.client.post("/candidate-facts/review/approve", json={
                "id": item_id,
                "reviewed_by": "reviewer",
            }).status_code,
            200,
        )

        promote_resp = self.client.post("/candidate-facts/review/promote", json={"id": item_id})
        self.assertEqual(promote_resp.status_code, 200)
        body = promote_resp.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["persist"]["committed"])
        self.assertGreater(len(body.get("warnings") or []), 0)
        self.assertEqual(body["review_item"]["supabase_persistence_status"], "failed")
        self.assertIn("supabase outage", body["warnings"][0].lower())

    def test_candidate_facts_review_promote_rejects_client_override_of_acceptance_policy(self):
        payload = {
            "candidate_resume_id": "candidate-resume-2",
            "resume_blob_id": "blob-2",
            "parser_version": "generic_pdf.v1",
            "facts_revision": "rev-1",
            "candidate_facts": {
                **self._candidate_facts_payload(),
                "extraction": {
                    **self._candidate_facts_payload()["extraction"],
                    "status": "failed",
                },
            },
        }

        capture_resp = self.client.post("/candidate-facts/review/capture", json=payload)
        self.assertEqual(capture_resp.status_code, 200)
        item_id = capture_resp.get_json()["review_item"]["id"]
        self.assertEqual(
            self.client.post("/candidate-facts/review/approve", json={
                "id": item_id,
                "reviewed_by": "reviewer",
            }).status_code,
            200,
        )

        promote_resp = self.client.post(
            "/candidate-facts/review/promote",
            json={
                "id": item_id,
                "acceptable_extraction_statuses": ["failed"],
            },
        )
        self.assertEqual(promote_resp.status_code, 409)
        body = promote_resp.get_json()
        self.assertFalse(body["success"])
        self.assertFalse(body["persist"]["committed"])
        self.assertEqual(body["review_item"]["persistence_status"], "persisted_non_current")

    def test_candidate_facts_repository_rebuilds_when_supabase_runtime_changes(self):
        backend_server.candidate_facts_repo = None
        backend_server.feature_flags = replace(
            backend_server.feature_flags,
            use_supabase_db=False,
        )
        repo_local = backend_server._candidate_facts_repository()
        self.assertIsNone(getattr(repo_local, "supabase_store", None))

        backend_server.feature_flags = replace(
            backend_server.feature_flags,
            use_supabase_db=True,
        )
        with patch.object(backend_server, "_supabase_url", return_value="https://example.supabase.co"), patch.object(
            backend_server,
            "resolve_supabase_api_key",
            return_value="service-role-key",
        ):
            repo_remote = backend_server._candidate_facts_repository()

        self.assertIsNot(repo_local, repo_remote)
        self.assertIsNotNone(repo_remote.supabase_store)
        self.assertEqual(repo_remote.supabase_url, "https://example.supabase.co")
        self.assertEqual(repo_remote.supabase_service_role_key, "service-role-key")


if __name__ == "__main__":
    unittest.main()
