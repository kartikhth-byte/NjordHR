import io
import os
import sys
import types
import zipfile
import tempfile
import unittest
from pathlib import Path

import pandas as pd


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
        self.client = backend_server.app.test_client()

    def tearDown(self):
        backend_server.scraper_session = None
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

    def test_download_stream_reports_error_when_session_missing(self):
        backend_server.scraper_session = None
        resp = self.client.get("/download_stream?rank=Chief_Officer&shipType=Bulk%20Carrier")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_data(as_text=True)
        self.assertIn('"type": "error"', payload)
        self.assertIn("Website session is not active or has expired", payload)

    def test_download_stream_emits_complete_event_for_valid_session(self):
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


if __name__ == "__main__":
    unittest.main()
