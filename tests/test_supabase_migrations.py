import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SupabaseMigrationTests(unittest.TestCase):
    def test_candidate_facts_promotion_rpc_serializes_current_row_flip(self):
        sql = (
            REPO_ROOT
            / "supabase"
            / "migrations"
            / "007_candidate_resume_facts_rpc_race_hardening.sql"
        ).read_text(encoding="utf-8")

        lock_pos = sql.index("pg_advisory_xact_lock")
        demote_pos = sql.index("update public.candidate_resume_facts")
        upsert_pos = sql.index("insert into public.candidate_resume_facts")

        self.assertLess(lock_pos, demote_pos)
        self.assertLess(lock_pos, upsert_pos)
        self.assertIn("hashtext(coalesce(p_candidate_resume_id, ''))", sql)
        self.assertIn("hashtext(coalesce(p_schema_version, ''))", sql)


if __name__ == "__main__":
    unittest.main()
