import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.migrate_local_state_to_supabase import (
    migrate_ai_feedback,
    migrate_ai_registry,
    migrate_local_state_to_supabase,
)


class _FakeResponse:
    def __init__(self, status_code=200, text="[]", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data if json_data is not None else []

    def json(self):
        return self._json_data


class MigrateLocalStateToSupabaseTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "NJORDHR_CONFIG_PATH",
        ]}
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_registry_migration_posts_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE files (file_path TEXT, last_modified REAL, resume_id TEXT)")
                conn.execute(
                    "INSERT INTO files VALUES (?, ?, ?)",
                    ("/tmp/resume-a.pdf", 123.0, "resume-123"),
                )
                conn.commit()
            finally:
                conn.close()

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
                return _FakeResponse(json_data=[])

            with patch("scripts.migrate_local_state_to_supabase.requests.request", side_effect=fake_request):
                result = migrate_ai_registry(
                    registry_db_path=str(db_path),
                    apply=True,
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["inserted"], 1)
            self.assertTrue(any(call["url"].endswith("/rest/v1/ai_file_registry") for call in calls))

    def test_feedback_migration_posts_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "feedback.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        query TEXT NOT NULL,
                        llm_decision TEXT NOT NULL,
                        llm_reason TEXT,
                        llm_confidence REAL,
                        user_decision TEXT NOT NULL,
                        user_notes TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO feedback (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("resume-a.pdf", "2nd engineer", "good", "matched well", 0.93, "approve", "looks good", "2026-01-01T00:00:00Z"),
                )
                conn.commit()
            finally:
                conn.close()

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
                return _FakeResponse(json_data=[])

            with patch("scripts.migrate_local_state_to_supabase.requests.request", side_effect=fake_request):
                result = migrate_ai_feedback(
                    feedback_db_path=str(db_path),
                    apply=True,
                )

            self.assertTrue(result["success"])
            self.assertEqual(result["inserted"], 1)
            self.assertTrue(any(call["url"].endswith("/rest/v1/ai_feedback") for call in calls))

    def test_orchestrator_reports_all_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_folder = Path(temp_dir) / "Verified_Resumes"
            base_folder.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([
                {
                    "Candidate_ID": "1001",
                    "Filename": "Chief_Officer_1001.pdf",
                    "Date_Added": "2026-01-01T00:00:00Z",
                    "Event_Type": "initial_verification",
                    "Status": "New",
                }
            ]).to_csv(base_folder / "verified_resumes.csv", index=False)

            registry_db = Path(temp_dir) / "registry.db"
            conn = sqlite3.connect(registry_db)
            try:
                conn.execute("CREATE TABLE files (file_path TEXT, last_modified REAL, resume_id TEXT)")
                conn.execute("INSERT INTO files VALUES (?, ?, ?)", ("/tmp/resume-a.pdf", 123.0, "resume-123"))
                conn.commit()
            finally:
                conn.close()

            feedback_db = Path(temp_dir) / "feedback.db"
            conn = sqlite3.connect(feedback_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        query TEXT NOT NULL,
                        llm_decision TEXT NOT NULL,
                        llm_reason TEXT,
                        llm_confidence REAL,
                        user_decision TEXT NOT NULL,
                        user_notes TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO feedback (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("resume-a.pdf", "2nd engineer", "good", "matched well", 0.93, "approve", "looks good", "2026-01-01T00:00:00Z"),
                )
                conn.commit()
            finally:
                conn.close()

            with patch("scripts.migrate_local_state_to_supabase.backfill_csv_to_supabase", return_value={
                "success": True,
                "applied": False,
                "master_csv_path": str(base_folder / "verified_resumes.csv"),
                "csv_rows": 1,
                "existing_supabase_events": None,
                "planned_inserts": 1,
                "inserted": 0,
                "skipped_existing": 0,
                "errors": [],
            }):
                report = migrate_local_state_to_supabase(
                    base_folder=str(base_folder),
                    registry_db_path=str(registry_db),
                    feedback_db_path=str(feedback_db),
                    apply=False,
                )

            self.assertTrue(report["success"])
            self.assertEqual(report["verified_resumes_csv"]["planned_inserts"], 1)
            self.assertEqual(report["ai_file_registry"]["planned_upserts"], 1)
            self.assertEqual(report["ai_feedback"]["planned_inserts"], 1)


if __name__ == "__main__":
    unittest.main()
