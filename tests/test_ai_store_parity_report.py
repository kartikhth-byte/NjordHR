import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ai_store_parity_report import build_ai_store_parity_report


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data
        self.text = json.dumps(json_data)
        self.status_code = 200

    def json(self):
        return self._json_data


class AIStoreParityReportTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ]}
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _make_registry_db(self, path, rows):
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE files (file_path TEXT, last_modified REAL, resume_id TEXT)")
            for row in rows:
                conn.execute("INSERT INTO files VALUES (?, ?, ?)", row)
            conn.commit()
        finally:
            conn.close()

    def _make_feedback_db(self, path, rows):
        conn = sqlite3.connect(path)
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
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO feedback (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            conn.commit()
        finally:
            conn.close()

    def test_build_report_matches_on_registry_and_feedback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_db = Path(temp_dir) / "registry.db"
            feedback_db = Path(temp_dir) / "feedback.db"
            self._make_registry_db(
                registry_db,
                [
                    ("/tmp/resume-a.pdf", 123.0, "resume-a"),
                    ("/tmp/resume-b.pdf", 456.0, "resume-b"),
                ],
            )
            self._make_feedback_db(
                feedback_db,
                [
                    ("resume-a.pdf", "2nd engineer", "good", "matched well", 0.93, "approve", "looks good", "2026-01-01T00:00:00Z"),
                    ("resume-b.pdf", "chief officer", "needs review", "check date", 0.72, "review", "needs another look", "2026-01-02T00:00:00Z"),
                ],
            )

            def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
                if url.endswith("/rest/v1/ai_file_registry"):
                    rows = [
                        {"file_key": "/tmp/resume-a.pdf", "last_modified": 123.0, "resume_id": "resume-a"},
                        {"file_key": "/tmp/resume-b.pdf", "last_modified": 456.0, "resume_id": "resume-b"},
                    ]
                elif url.endswith("/rest/v1/ai_feedback"):
                    rows = [
                        {
                            "filename": "resume-a.pdf",
                            "query": "2nd engineer",
                            "llm_decision": "good",
                            "llm_reason": "matched well",
                            "llm_confidence": 0.93,
                            "user_decision": "approve",
                            "user_notes": "looks good",
                            "timestamp": "2026-01-01T00:00:00Z",
                        },
                        {
                            "filename": "resume-b.pdf",
                            "query": "chief officer",
                            "llm_decision": "needs review",
                            "llm_reason": "check date",
                            "llm_confidence": 0.72,
                            "user_decision": "review",
                            "user_notes": "needs another look",
                            "timestamp": "2026-01-02T00:00:00Z",
                        },
                    ]
                else:
                    rows = []
                limit = int((params or {}).get("limit", len(rows)) or len(rows))
                offset = int((params or {}).get("offset", 0) or 0)
                return _FakeResponse(rows[offset:offset + limit])

            with patch("scripts.ai_store_parity_report.requests.request", side_effect=fake_request):
                report = build_ai_store_parity_report(
                    registry_db_path=str(registry_db),
                    feedback_db_path=str(feedback_db),
                    supabase_url="https://example.supabase.co",
                    supabase_api_key="sb_secret_test",
                    sample_size=1,
                    seed=7,
                )

            self.assertTrue(report["success"])
            self.assertTrue(report["parity_ok"])
            self.assertEqual(report["registry"]["matched_rows"], 2)
            self.assertEqual(report["feedback"]["matched_rows"], 2)
            self.assertEqual(len(report["registry"]["spot_check"]["sampled_keys"]), 1)
            self.assertEqual(report["registry"]["spot_check"]["field_mismatches"], [])
            self.assertEqual(report["feedback"]["spot_check"]["field_mismatches"], [])

    def test_build_report_detects_mismatches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_db = Path(temp_dir) / "registry.db"
            feedback_db = Path(temp_dir) / "feedback.db"
            self._make_registry_db(
                registry_db,
                [("/tmp/resume-a.pdf", 123.0, "resume-a")],
            )
            self._make_feedback_db(
                feedback_db,
                [("resume-a.pdf", "2nd engineer", "good", "matched well", 0.93, "approve", "looks good", "2026-01-01T00:00:00Z")],
            )

            def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
                if url.endswith("/rest/v1/ai_file_registry"):
                    rows = []
                elif url.endswith("/rest/v1/ai_feedback"):
                    rows = [{
                        "filename": "resume-a.pdf",
                        "query": "2nd engineer",
                        "llm_decision": "good",
                        "llm_reason": "changed reason",
                        "llm_confidence": 0.93,
                        "user_decision": "approve",
                        "user_notes": "looks good",
                        "timestamp": "2026-01-01T00:00:00Z",
                    }]
                else:
                    rows = []
                limit = int((params or {}).get("limit", len(rows)) or len(rows))
                offset = int((params or {}).get("offset", 0) or 0)
                return _FakeResponse(rows[offset:offset + limit])

            with patch("scripts.ai_store_parity_report.requests.request", side_effect=fake_request):
                report = build_ai_store_parity_report(
                    registry_db_path=str(registry_db),
                    feedback_db_path=str(feedback_db),
                    supabase_url="https://example.supabase.co",
                    supabase_api_key="sb_secret_test",
                    sample_size=1,
                    seed=1,
                )

            self.assertFalse(report["parity_ok"])
            self.assertEqual(report["registry"]["missing_in_supabase"], ["/tmp/resume-a.pdf"])
            self.assertTrue(report["feedback"]["spot_check"]["field_mismatches"])


if __name__ == "__main__":
    unittest.main()
