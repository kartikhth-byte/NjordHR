import os
import tempfile
import unittest

import pandas as pd

from app_settings import FeatureFlags
from repositories.candidate_event_repo import CandidateEventRepo
from repositories.dual_write_candidate_event_repo import DualWriteCandidateEventRepo
from repositories.repo_factory import build_candidate_event_repo
from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo
from repositories.csv_candidate_event_repo import CSVCandidateEventRepo


class _InMemoryRepo(CandidateEventRepo):
    def __init__(self, fail_writes=False, fail_first_write=False):
        self.fail_writes = fail_writes
        self.fail_first_write = fail_first_write
        self.events = []
        self.ai_search_audits = []
        self.status_changes = []
        self.note_changes = []
        self.rank_counts = []
        self.write_calls = 0

    def log_event(self, *args, **kwargs):
        self.write_calls += 1
        if self.fail_writes or (self.fail_first_write and self.write_calls == 1):
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
        return self.rank_counts

    def get_csv_stats(self, *args, **kwargs):
        return {"master_csv_rows": len(self.events)}

    def log_ai_search_audit(self, *args, **kwargs):
        if self.fail_writes:
            return False
        self.ai_search_audits.append(kwargs)
        return True

    def get_ai_search_audit_rows(self, *args, **kwargs):
        return list(self.ai_search_audits)


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

    def test_secondary_failure_is_retried_without_repeating_primary(self):
        primary = _InMemoryRepo()
        secondary = _InMemoryRepo(fail_first_write=True)
        repo = DualWriteCandidateEventRepo(
            primary_repo=primary,
            secondary_repo=secondary,
            idempotency_db_path=f"{self.tmp.name}/dual_write_idempotency_retry.db",
        )

        self.assertTrue(repo.log_event(
            candidate_id="1002",
            filename="Chief_Officer_1002.pdf",
            event_type="initial_verification",
            rank_applied_for="Chief_Officer",
            extracted_data={"email": "test@example.com"},
        ))
        self.assertTrue(repo.log_event(
            candidate_id="1002",
            filename="Chief_Officer_1002.pdf",
            event_type="initial_verification",
            rank_applied_for="Chief_Officer",
            extracted_data={"email": "test@example.com"},
        ))

        self.assertEqual(len(primary.events), 1)
        self.assertEqual(len(secondary.events), 1)
        self.assertEqual(secondary.write_calls, 2)

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

    def test_reads_can_delegate_to_secondary_when_configured(self):
        primary = _InMemoryRepo()
        secondary = _InMemoryRepo()
        secondary.log_event(
            candidate_id="3001",
            filename="Chief_Officer_3001.pdf",
            event_type="initial_verification",
            rank_applied_for="Chief_Officer",
            extracted_data={"email": "secondary@example.com"},
        )
        repo = DualWriteCandidateEventRepo(
            primary_repo=primary,
            secondary_repo=secondary,
            idempotency_db_path=f"{self.tmp.name}/dual_write_idempotency_reads.db",
            read_repo=secondary,
        )

        latest = repo.get_latest_status_per_candidate()
        self.assertEqual(len(latest), 1)
        self.assertEqual(str(latest.iloc[0]["Candidate_ID"]), "3001")

    def test_ai_search_audit_defaults_to_primary_store(self):
        ok = self.repo.log_ai_search_audit(
            search_session_id="search-1",
            candidate_id="123",
            filename="Chief_Officer_123.pdf",
            facts_version="2.0",
            rank_applied_for="Chief Officer",
            ai_prompt="having valid US visa",
            hard_filter_decision="PASS",
            llm_reached=True,
            result_bucket="verified_match",
        )

        self.assertTrue(ok)
        self.assertEqual(len(self.primary.ai_search_audits), 1)
        self.assertEqual(len(self.secondary.ai_search_audits), 1)
        rows = self.repo.get_ai_search_audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_id"], "123")
        self.assertEqual(rows[0]["facts_version"], "2.0")

    def test_factory_routes_reads_to_supabase_when_flagged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prev_url = os.environ.get("SUPABASE_URL")
            prev_key = os.environ.get("SUPABASE_SECRET_KEY")
            os.environ["SUPABASE_URL"] = "https://example.supabase.co"
            os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"
            try:
                repo = build_candidate_event_repo(
                    FeatureFlags(
                        use_supabase_db=True,
                        use_dual_write=True,
                        use_supabase_reads=True,
                        use_local_agent=False,
                        use_cloud_export=False,
                    ),
                    base_folder=temp_dir,
                    server_url="http://127.0.0.1:5000",
                )
                self.assertIsInstance(repo, DualWriteCandidateEventRepo)
                self.assertIsInstance(repo.primary_repo, CSVCandidateEventRepo)
                self.assertIsInstance(repo.secondary_repo, SupabaseCandidateEventRepo)
                self.assertIs(repo.read_repo, repo.secondary_repo)
                self.assertFalse(getattr(repo.secondary_repo, "allow_local_resume_url_fallback", True))
            finally:
                if prev_url is None:
                    os.environ.pop("SUPABASE_URL", None)
                else:
                    os.environ["SUPABASE_URL"] = prev_url
                if prev_key is None:
                    os.environ.pop("SUPABASE_SECRET_KEY", None)
                else:
                    os.environ["SUPABASE_SECRET_KEY"] = prev_key


if __name__ == "__main__":
    unittest.main()
