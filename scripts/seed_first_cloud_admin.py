#!/usr/bin/env python3
"""Seed or update the first cloud admin user in Supabase public.users.

Required env vars:
  SUPABASE_DB_URL

Usage:
  python3 scripts/seed_first_cloud_admin.py --username admin --password 'StrongPass123!'
"""

import argparse
import os
import sys
from urllib.parse import quote

from werkzeug.security import generate_password_hash
import subprocess


def build_db_url_from_parts() -> str:
    user = os.getenv("SUPABASE_DB_USER", "").strip()
    pwd = os.getenv("SUPABASE_DB_PASSWORD", "").strip()
    host = os.getenv("SUPABASE_DB_HOST", "").strip()
    port = os.getenv("SUPABASE_DB_PORT", "5432").strip()
    db = os.getenv("SUPABASE_DB_NAME", "postgres").strip()
    if not (user and pwd and host and port and db):
        return ""
    return f"postgresql://{user}:{quote(pwd, safe='')}@{host}:{port}/{db}"


def resolve_db_url() -> str:
    direct = os.getenv("SUPABASE_DB_URL", "").strip()
    if direct:
        return direct
    return build_db_url_from_parts()


def validate_username(username: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9._-]{3,64}", username or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed first cloud admin in Supabase")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Admin password")
    parser.add_argument("--role", default="admin", choices=["admin", "manager", "recruiter"], help="Role")
    args = parser.parse_args()

    username = (args.username or "").strip()
    password = args.password or ""
    role = (args.role or "admin").strip().lower()

    if not validate_username(username):
        print("[ERROR] Username must be 3-64 chars: letters, numbers, dot, underscore, hyphen.")
        return 2
    if len(password) < 8:
        print("[ERROR] Password must be at least 8 characters.")
        return 2

    db_url = resolve_db_url()
    if not db_url:
        print("[ERROR] SUPABASE_DB_URL is required (or SUPABASE_DB_USER/PASSWORD/HOST/PORT/NAME).")
        return 2

    hash_method = os.getenv("NJORDHR_PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000").strip() or "pbkdf2:sha256:600000"
    pwd_hash = generate_password_hash(password, method=hash_method)
    email = f"{username}@local.njordhr"

    # Use psql so we don't add a new Python DB dependency for installers/users.
    sql = """
    insert into public.users (id, email, username, password_hash, role, is_active)
    values (gen_random_uuid(), '{email}', '{username}', '{pwd_hash}', '{role}', true)
    on conflict (username) do update
    set email = excluded.email,
        password_hash = excluded.password_hash,
        role = excluded.role,
        is_active = true,
        updated_at = now();
    """.format(
        email=email.replace("'", "''"),
        username=username.replace("'", "''"),
        pwd_hash=pwd_hash.replace("'", "''"),
        role=role.replace("'", "''"),
    )

    try:
        subprocess.run(
            ["psql", db_url, "-v", "ON_ERROR_STOP=1", "-c", sql],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("[ERROR] psql is not installed or not on PATH.")
        return 1
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        print(f"[ERROR] Failed to seed cloud user: {detail}")
        return 1
    except Exception as exc:
        print(f"[ERROR] Failed to seed cloud user: {exc}")
        return 1

    print(f"[OK] Seeded cloud user '{username}' with role '{role}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
