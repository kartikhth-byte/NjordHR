import io
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
import backend_server  # noqa: E402
from csv_manager import CSVManager  # noqa: E402


class BackendEventLogFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.download_root = self.base / "Source"
        self.verified_root = self.base / "Verified_Resumes"
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
        backend_server.feature_flags = replace(backend_server.feature_flags, use_local_agent=False)
        self.prev_admin_token = os.environ.get("NJORDHR_ADMIN_TOKEN")
        os.environ["NJORDHR_ADMIN_TOKEN"] = "test-admin-token"
        self.prev_config_path = os.environ.get("NJORDHR_CONFIG_PATH")
        self.temp_config_path = str(self.base / "config.test.ini")
        with open(self.temp_config_path, "w", encoding="utf-8") as fh:
            backend_server.config.write(fh)
        os.environ["NJORDHR_CONFIG_PATH"] = self.temp_config_path
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
        self.temp_dir.cleanup()

    def _write_fake_resume(self, filename):
        path = self.rank_dir / filename
        path.write_bytes(b"%PDF-1.4 fake resume content")
        return path

    def _read_master_csv(self):
        master = self.verified_root / "verified_resumes.csv"
        self.assertTrue(master.exists(), "Master CSV should exist")
        return pd.read_csv(master, keep_default_na=False)

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
        resp = self.client.get("/config/runtime")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn(data["persistence_backend"], {"csv", "supabase"})
        self.assertIn("feature_flags", data)
        self.assertIn("use_supabase_db", data["feature_flags"])
        self.assertIn("use_supabase_reads", data["feature_flags"])

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

    def test_admin_settings_rejects_invalid_otp_window(self):
        resp = self.client.post(
            "/admin/settings",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"settings": {"otp_window_seconds": "10"}},
        )
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


if __name__ == "__main__":
    unittest.main()
