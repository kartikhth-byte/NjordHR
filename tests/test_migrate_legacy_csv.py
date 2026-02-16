import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.migrate_legacy_csv import migrate_legacy_csvs
from csv_manager import CSVManager


class MigrateLegacyCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "Verified_Resumes"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _write_legacy_rank_csv(self, rank, rows):
        rank_dir = self.base / rank
        rank_dir.mkdir(parents=True, exist_ok=True)
        path = rank_dir / f"{rank}_verified.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def test_migrates_rank_csv_to_master_schema(self):
        self._write_legacy_rank_csv("Chief_Officer", [{
            "Filename": "Chief_Officer_1010.pdf",
            "Resume_URL": "http://127.0.0.1:5000/get_resume/Chief_Officer/Chief_Officer_1010.pdf",
            "Date_Added": "2025-01-10T10:00:00Z",
            "Name": "A",
            "Present_Rank": "Chief Officer",
            "Email": "a@example.com",
            "Country": "India",
            "Mobile_No": "123",
            "AI_Match_Reason": "Legacy match",
        }])

        result = migrate_legacy_csvs(base_folder=str(self.base))
        self.assertTrue(result["success"])
        self.assertEqual(result["added_rows"], 1)

        master = pd.read_csv(self.base / "verified_resumes.csv", keep_default_na=False)
        self.assertTrue(set(CSVManager.COLUMNS).issubset(set(master.columns)))
        self.assertEqual(master.iloc[0]["Candidate_ID"], 1010)
        self.assertEqual(master.iloc[0]["Event_Type"], "initial_verification")
        self.assertEqual(master.iloc[0]["Status"], "New")

    def test_idempotent_on_rerun(self):
        self._write_legacy_rank_csv("Master", [{
            "Filename": "Master_2020.pdf",
            "Resume_URL": "",
            "Date_Added": "2025-02-01T11:00:00Z",
            "Name": "B",
            "Present_Rank": "Master",
            "Email": "b@example.com",
            "Country": "India",
            "Mobile_No": "999",
            "AI_Match_Reason": "Legacy",
        }])

        first = migrate_legacy_csvs(base_folder=str(self.base))
        second = migrate_legacy_csvs(base_folder=str(self.base))
        self.assertEqual(first["added_rows"], 1)
        self.assertEqual(second["added_rows"], 0)

    def test_dry_run_does_not_write_master(self):
        self._write_legacy_rank_csv("2nd_Officer", [{
            "Filename": "2nd_Officer_3333.pdf",
            "Resume_URL": "",
            "Date_Added": "",
            "Name": "C",
            "Present_Rank": "2nd Officer",
            "Email": "c@example.com",
            "Country": "India",
            "Mobile_No": "111",
            "AI_Match_Reason": "Legacy",
        }])

        result = migrate_legacy_csvs(base_folder=str(self.base), dry_run=True)
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(self.base / "verified_resumes.csv"))


if __name__ == "__main__":
    unittest.main()
