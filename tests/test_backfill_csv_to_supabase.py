import tempfile
import unittest
from pathlib import Path

import pandas as pd

from csv_manager import CSVManager
from scripts.backfill_csv_to_supabase import (
    _event_key_from_csv_row,
    _event_key_from_supabase_row,
    _load_csv_events,
)


class BackfillCsvToSupabaseTests(unittest.TestCase):
    def test_event_key_alignment_between_csv_and_supabase_shapes(self):
        csv_row = {
            "Candidate_ID": "95082",
            "Filename": "2nd-Officer_Bulk-Carrier_95082_2025-09-05_14-21-20.pdf",
            "Event_Type": "initial_verification",
            "Status": "New",
            "Notes": "",
            "Rank_Applied_For": "2nd_Officer",
            "Search_Ship_Type": "Bulk Carrier",
            "AI_Search_Prompt": "having a valid us visa",
            "AI_Match_Reason": "matched",
        }
        sup_row = {
            "candidate_external_id": "95082",
            "filename": "2nd-Officer_Bulk-Carrier_95082_2025-09-05_14-21-20.pdf",
            "event_type": "initial_verification",
            "status": "New",
            "notes": "",
            "rank_applied_for": "2nd_Officer",
            "search_ship_type": "Bulk Carrier",
            "ai_search_prompt": "having a valid us visa",
            "ai_match_reason": "matched",
        }
        self.assertEqual(_event_key_from_csv_row(csv_row), _event_key_from_supabase_row(sup_row))

    def test_load_csv_events_normalizes_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            master = base / "verified_resumes.csv"
            pd.DataFrame([
                {
                    "Candidate_ID": "1001",
                    "Filename": "Chief_Officer_1001.pdf",
                    "Date_Added": "2026-01-01T00:00:00Z",
                }
            ]).to_csv(master, index=False)

            df = _load_csv_events(str(master))
            self.assertEqual(set(CSVManager.COLUMNS), set(df.columns))
            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["Candidate_ID"], "1001")
            self.assertEqual(df.iloc[0]["Event_Type"], "")


if __name__ == "__main__":
    unittest.main()
