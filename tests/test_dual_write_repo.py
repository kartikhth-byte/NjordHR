import tempfile
import unittest

import pandas as pd

from repositories.candidate_event_repo import CandidateEventRepo
from repositories.dual_write_candidate_event_repo import DualWriteCandidateEventRepo


class _InMemoryRepo(CandidateEventRepo):
    def __init__(self, fail_writes=False):
        self.fail_writes = fail_writes
        self.events = []
        self.status_changes = []
        self.note_changes = []

    def log_event(self, *args, **kwargs):
        if self.fail_writes:
            return False
        self.events.append(kwargs)
        return True

    def get_latest_status_per_candidate(self, *args, **kwargs):
        rows = []
        for event in self.events:
            rows.append({
                "Candidate_ID": str(event.get("candidate_id", "")),
                "Filename": event.get("filename", ""),
                "Rank_Applied_For": event.get("rank_applied_for", ""),
                "Date_Added": "2026-01-01T00:00:00Z",
            })
        return pd.DataFrame(rows)

    def get_candidate_history(self, *args, **kwargs):
        return list(self.events)

    def log_status_change(self, candidate_id, status):
        if self.fail_writes:
            return False
        self.status_changes.append((candidate_id, status))
        return True

    def log_note_added(self, candidate_id, notes):
        if self.fail_writes:
            return False
        self.note_changes.append((candidate_id, notes))
        return True

    def get_rank_counts(self, *args, **kwargs):
        return []

    def get_csv_stats(self, *args, **kwargs):
        return {"master_csv_rows": len(self.events)}


class DualWriteRepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.primary = _InMemoryRepo()
        self.secondary = _InMemoryRepo()
        self.repo = DualWriteCandidateEventRepo(
            primary_repo=self.primary,
            secondary_repo=self.secondary,
            idempotency_db_path=f"{self.tmp.name}/dual_write_idempotency.db",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_log_event_writes_to_both_once_for_same_payload(self):
        payload = {
            "candidate_id": "95082",
            "filename": "2nd-Officer_Bulk-Carrier_95082_2025-09-05_14-21-20.pdf",
            "event_type": "initial_verification",
            "status": "New",
            "notes": "",
            "rank_applied_for": "2nd_Officer",
            "search_ship_type": "Bulk Carrier",
            "ai_prompt": "having valid US visa",
            "ai_reason": "matched",
            "extracted_data": {"email": "getmeyash011@gmail.com"},
        }
        self.assertTrue(self.repo.log_event(**payload))
        self.assertTrue(self.repo.log_event(**payload))

        self.assertEqual(len(self.primary.events), 1)
        self.assertEqual(len(self.secondary.events), 1)

    def test_secondary_failure_does_not_break_primary_path(self):
        secondary = _InMemoryRepo(fail_writes=True)
        repo = DualWriteCandidateEventRepo(
            primary_repo=self.primary,
            secondary_repo=secondary,
            idempotency_db_path=f"{self.tmp.name}/dual_write_idempotency_fail.db",
        )

        ok = repo.log_event(
            candidate_id="1001",
            filename="Chief_Officer_1001.pdf",
            event_type="initial_verification",
            rank_applied_for="Chief_Officer",
            extracted_data={"email": "test@example.com"},
        )
        self.assertTrue(ok)
        self.assertEqual(len(self.primary.events), 1)
        self.assertEqual(len(secondary.events), 0)

    def test_reads_delegate_to_primary(self):
        self.repo.log_event(
            candidate_id="2001",
            filename="Chief_Officer_2001.pdf",
            event_type="initial_verification",
            rank_applied_for="Chief_Officer",
            extracted_data={"email": "test@example.com"},
        )
        latest = self.repo.get_latest_status_per_candidate()
        self.assertEqual(len(latest), 1)
        self.assertEqual(str(latest.iloc[0]["Candidate_ID"]), "2001")


if __name__ == "__main__":
    unittest.main()
