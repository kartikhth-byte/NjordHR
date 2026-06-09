import unittest
from pathlib import Path
from collections import Counter


REPO_ROOT = Path(__file__).resolve().parents[1]


class SupabaseMigrationTests(unittest.TestCase):
    def test_supabase_migration_filenames_sort_by_version(self):
        migration_dir = REPO_ROOT / "supabase" / "migrations"
        versions_by_filename = [
            path.name.split("_", 1)[0]
            for path in sorted(migration_dir.glob("*.sql"))
        ]

        self.assertEqual(sorted(versions_by_filename), versions_by_filename)

    def test_supabase_migration_versions_are_unique(self):
        migration_dir = REPO_ROOT / "supabase" / "migrations"
        versions = [
            path.name.split("_", 1)[0]
            for path in migration_dir.glob("*.sql")
        ]
        duplicates = sorted(
            version for version, count in Counter(versions).items() if count > 1
        )

        self.assertEqual([], duplicates)

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
