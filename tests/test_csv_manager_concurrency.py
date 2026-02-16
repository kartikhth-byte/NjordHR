import tempfile
import threading
import unittest
from pathlib import Path

import pandas as pd

from csv_manager import CSVManager


class CsvManagerConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "Verified_Resumes"
        self.base.mkdir(parents=True, exist_ok=True)
        self.manager = CSVManager(base_folder=str(self.base))

        ok = self.manager.log_event(
            candidate_id="9001",
            filename="Chief_Officer_9001.pdf",
            event_type="initial_verification",
            status="New",
            rank_applied_for="Chief_Officer",
            ai_reason="seed event",
            extracted_data={
                "name": "Concurrent Candidate",
                "present_rank": "Chief Officer",
                "email": "cc@example.com",
                "country": "India",
                "mobile_no": "123",
            },
        )
        self.assertTrue(ok)

    def tearDown(self):
        self.temp.cleanup()

    def test_concurrent_status_and_note_writes_preserve_all_events(self):
        status_threads = 20
        note_threads = 20
        barrier = threading.Barrier(status_threads + note_threads)
        errors = []

        def status_worker(idx):
            try:
                barrier.wait()
                ok = self.manager.log_status_change("9001", f"Contacted-{idx}")
                if not ok:
                    errors.append(f"status-{idx}-failed")
            except Exception as exc:
                errors.append(f"status-{idx}-exc:{exc}")

        def note_worker(idx):
            try:
                barrier.wait()
                ok = self.manager.log_note_added("9001", f"note-{idx}")
                if not ok:
                    errors.append(f"note-{idx}-failed")
            except Exception as exc:
                errors.append(f"note-{idx}-exc:{exc}")

        threads = []
        for i in range(status_threads):
            threads.append(threading.Thread(target=status_worker, args=(i,)))
        for i in range(note_threads):
            threads.append(threading.Thread(target=note_worker, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Unexpected worker errors: {errors}")

        csv_path = self.base / "verified_resumes.csv"
        self.assertTrue(csv_path.exists())
        df = pd.read_csv(csv_path, keep_default_na=False)

        # 1 seed + one row per concurrent write
        expected_rows = 1 + status_threads + note_threads
        self.assertEqual(len(df), expected_rows)

        candidate_df = df[df["Candidate_ID"].astype(str) == "9001"]
        self.assertEqual(len(candidate_df), expected_rows)

        type_counts = candidate_df["Event_Type"].value_counts().to_dict()
        self.assertEqual(type_counts.get("initial_verification", 0), 1)
        self.assertEqual(type_counts.get("status_change", 0), status_threads)
        self.assertEqual(type_counts.get("note_added", 0), note_threads)


if __name__ == "__main__":
    unittest.main()
