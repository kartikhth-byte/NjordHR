import unittest

import pandas as pd

from scripts.supabase_parity_report import _build_parity_report


class _FakeRepo:
    def __init__(self, latest_df, rank_counts, stats):
        self._latest_df = latest_df
        self._rank_counts = rank_counts
        self._stats = stats

    def get_csv_stats(self):
        return dict(self._stats)

    def get_latest_status_per_candidate(self, rank_name=""):
        if not rank_name:
            return self._latest_df.copy()
        if self._latest_df.empty:
            return self._latest_df.copy()
        return self._latest_df[self._latest_df["Rank_Applied_For"] == rank_name].copy()

    def get_rank_counts(self):
        return list(self._rank_counts)


class SupabaseParityReportTests(unittest.TestCase):
    def _base_df(self):
        return pd.DataFrame([
            {
                "Candidate_ID": "1001",
                "Filename": "Chief_Officer_1001.pdf",
                "Rank_Applied_For": "Chief_Officer",
                "Status": "New",
                "Notes": "",
                "Name": "A",
                "Present_Rank": "Chief Officer",
                "Email": "a@example.com",
                "Country": "India",
                "Mobile_No": "111",
            },
            {
                "Candidate_ID": "1002",
                "Filename": "2nd_Officer_1002.pdf",
                "Rank_Applied_For": "2nd_Officer",
                "Status": "Contacted",
                "Notes": "note",
                "Name": "B",
                "Present_Rank": "2nd Officer",
                "Email": "b@example.com",
                "Country": "India",
                "Mobile_No": "222",
            },
        ])

    def test_parity_ok_when_datasets_match(self):
        df = self._base_df()
        rank_counts = [
            {"Rank_Applied_For": "Chief_Officer", "count": 1},
            {"Rank_Applied_For": "2nd_Officer", "count": 1},
        ]
        csv_repo = _FakeRepo(df, rank_counts, {"master_csv_rows": 4})
        sup_repo = _FakeRepo(df, rank_counts, {"master_csv_rows": 4})

        report = _build_parity_report(csv_repo, sup_repo, sample_size=10)
        self.assertTrue(report["parity_ok"])
        self.assertEqual(report["missing_candidate_ids"]["missing_in_supabase"], [])
        self.assertEqual(report["spot_check"]["field_mismatches"], [])
        self.assertEqual(report["rank_count_mismatches"], [])

    def test_parity_detects_missing_and_field_mismatch(self):
        csv_df = self._base_df()
        sup_df = self._base_df().copy()
        sup_df = sup_df[sup_df["Candidate_ID"] != "1002"].copy()
        sup_df.loc[sup_df["Candidate_ID"] == "1001", "Email"] = "wrong@example.com"

        csv_rank_counts = [
            {"Rank_Applied_For": "Chief_Officer", "count": 1},
            {"Rank_Applied_For": "2nd_Officer", "count": 1},
        ]
        sup_rank_counts = [{"Rank_Applied_For": "Chief_Officer", "count": 1}]
        csv_repo = _FakeRepo(csv_df, csv_rank_counts, {"master_csv_rows": 4})
        sup_repo = _FakeRepo(sup_df, sup_rank_counts, {"master_csv_rows": 3})

        report = _build_parity_report(csv_repo, sup_repo, sample_size=10)
        self.assertFalse(report["parity_ok"])
        self.assertEqual(report["missing_candidate_ids"]["missing_in_supabase"], ["1002"])
        self.assertTrue(any(m["candidate_id"] == "1001" and m["field"] == "Email" for m in report["spot_check"]["field_mismatches"]))
        self.assertTrue(any(m["rank"] == "2nd_Officer" for m in report["rank_count_mismatches"]))


if __name__ == "__main__":
    unittest.main()
