#!/usr/bin/env python3
"""
Apply Supabase SQL migrations using psql and a database URL.

Usage:
  python3 scripts/apply_supabase_migrations.py --dry-run
  SUPABASE_DB_URL="postgresql://..." python3 scripts/apply_supabase_migrations.py --apply
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _list_migrations(migrations_dir):
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def _run_psql(db_url, sql_path):
    cmd = [
        "psql",
        db_url,
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(sql_path),
    ]
    return subprocess.run(cmd, check=False)


def main():
    parser = argparse.ArgumentParser(description="Apply Supabase migrations using psql.")
    parser.add_argument("--apply", action="store_true", help="Apply migrations to SUPABASE_DB_URL.")
    parser.add_argument("--dry-run", action="store_true", help="List migrations only (default).")
    parser.add_argument(
        "--migrations-dir",
        default="supabase/migrations",
        help="Path to migrations folder (default: supabase/migrations).",
    )
    args = parser.parse_args()

    migrations_dir = Path(args.migrations_dir).resolve()
    migrations = _list_migrations(migrations_dir)
    if not migrations:
        print(f"No migration files found in {migrations_dir}")
        return 1

    print(f"Found {len(migrations)} migration(s):")
    for m in migrations:
        print(f" - {m.name}")

    if not args.apply:
        print("\nDry-run only. Use --apply to execute against SUPABASE_DB_URL.")
        return 0

    db_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not db_url:
        print("SUPABASE_DB_URL is required when using --apply.")
        return 1

    try:
        subprocess.run(["psql", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("psql is not installed or not available on PATH.")
        return 1

    for migration in migrations:
        print(f"\nApplying {migration.name} ...")
        result = _run_psql(db_url, migration)
        if result.returncode != 0:
            print(f"Migration failed: {migration.name}")
            return result.returncode

    print("\nAll migrations applied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
