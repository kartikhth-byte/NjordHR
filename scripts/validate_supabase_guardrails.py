#!/usr/bin/env python3
"""
Validate Supabase migration 003 guardrails.

Checks:
1) Schema objects exist:
   - candidate_events.admin_override
   - trigger trg_njordhr_validate_candidate_event
   - functions njordhr_is_valid_status_transition, njordhr_validate_candidate_event
2) DB behavior:
   - valid transition (New -> Contacted) succeeds
   - invalid transition without admin_override fails
   - invalid transition with admin_override succeeds

Usage:
  SUPABASE_DB_URL="postgresql://..." python3 scripts/validate_supabase_guardrails.py
"""

import os
import subprocess
import sys
import textwrap


def run_psql(db_url, sql):
    cmd = [
        "psql",
        db_url,
        "-v",
        "ON_ERROR_STOP=1",
        "-tA",
        "-c",
        sql,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def require_db_url():
    db_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not db_url:
        print("SUPABASE_DB_URL is required.")
        return None
    return db_url


def check_object_exists(db_url):
    checks = [
        (
            "candidate_events.admin_override column",
            """
            select exists (
              select 1
              from information_schema.columns
              where table_schema='public'
                and table_name='candidate_events'
                and column_name='admin_override'
            );
            """,
        ),
        (
            "trigger trg_njordhr_validate_candidate_event",
            """
            select exists (
              select 1
              from pg_trigger t
              join pg_class c on c.oid=t.tgrelid
              join pg_namespace n on n.oid=c.relnamespace
              where n.nspname='public'
                and c.relname='candidate_events'
                and t.tgname='trg_njordhr_validate_candidate_event'
                and not t.tgisinternal
            );
            """,
        ),
        (
            "function public.njordhr_is_valid_status_transition",
            """
            select exists (
              select 1
              from pg_proc p
              join pg_namespace n on n.oid=p.pronamespace
              where n.nspname='public'
                and p.proname='njordhr_is_valid_status_transition'
            );
            """,
        ),
        (
            "function public.njordhr_validate_candidate_event",
            """
            select exists (
              select 1
              from pg_proc p
              join pg_namespace n on n.oid=p.pronamespace
              where n.nspname='public'
                and p.proname='njordhr_validate_candidate_event'
            );
            """,
        ),
    ]

    failed = False
    for label, sql in checks:
        res = run_psql(db_url, textwrap.dedent(sql))
        if res.returncode != 0:
            failed = True
            print(f"[FAIL] {label}: query failed")
            print(res.stderr.strip())
            continue
        ok = res.stdout.strip().lower() == "t"
        print(f"[{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failed = True
    return not failed


def behavioral_checks(db_url):
    seed_sql = """
    insert into public.candidate_events (
      candidate_external_id, filename, resume_url, event_type, status,
      rank_applied_for, search_ship_type, ai_search_prompt, ai_match_reason,
      name, present_rank, email, country, mobile_no
    ) values (
      'guardrail_probe_001',
      'probe.pdf',
      'storage://resumes/probe/probe.pdf',
      'initial_verification',
      'New',
      'Chief_Officer',
      '',
      'probe',
      'probe reason',
      'Probe User',
      'Chief Officer',
      'probe@example.com',
      'India',
      '9999999999'
    );
    """
    valid_transition_sql = """
    insert into public.candidate_events (
      candidate_external_id, filename, resume_url, event_type, status,
      rank_applied_for, search_ship_type, ai_search_prompt, ai_match_reason,
      name, present_rank, email, country, mobile_no
    ) values (
      'guardrail_probe_001',
      'probe.pdf',
      'storage://resumes/probe/probe.pdf',
      'status_change',
      'Contacted',
      'Chief_Officer',
      '',
      'probe',
      'probe reason',
      'Probe User',
      'Chief Officer',
      'probe@example.com',
      'India',
      '9999999999'
    );
    """
    invalid_transition_sql = """
    insert into public.candidate_events (
      candidate_external_id, filename, resume_url, event_type, status,
      rank_applied_for, search_ship_type, ai_search_prompt, ai_match_reason,
      name, present_rank, email, country, mobile_no
    ) values (
      'guardrail_probe_001',
      'probe.pdf',
      'storage://resumes/probe/probe.pdf',
      'status_change',
      'New',
      'Chief_Officer',
      '',
      'probe',
      'probe reason',
      'Probe User',
      'Chief Officer',
      'probe@example.com',
      'India',
      '9999999999'
    );
    """
    invalid_transition_override_sql = """
    insert into public.candidate_events (
      candidate_external_id, filename, resume_url, event_type, status,
      rank_applied_for, search_ship_type, ai_search_prompt, ai_match_reason,
      name, present_rank, email, country, mobile_no, admin_override
    ) values (
      'guardrail_probe_001',
      'probe.pdf',
      'storage://resumes/probe/probe.pdf',
      'status_change',
      'New',
      'Chief_Officer',
      '',
      'probe',
      'probe reason',
      'Probe User',
      'Chief Officer',
      'probe@example.com',
      'India',
      '9999999999',
      true
    );
    """

    cleanup_sql = "delete from public.candidate_events where candidate_external_id='guardrail_probe_001';"
    run_psql(db_url, cleanup_sql)

    failed = False
    for label, sql, should_succeed in [
        ("seed initial_verification New", seed_sql, True),
        ("valid transition New->Contacted", valid_transition_sql, True),
        ("invalid transition Contacted->New without override", invalid_transition_sql, False),
        ("invalid transition Contacted->New with admin_override=true", invalid_transition_override_sql, True),
    ]:
        res = run_psql(db_url, textwrap.dedent(sql))
        success = res.returncode == 0
        ok = success if should_succeed else not success
        print(f"[{'OK' if ok else 'FAIL'}] {label}")
        if not ok:
            failed = True
            print((res.stderr or res.stdout).strip())

    run_psql(db_url, cleanup_sql)
    return not failed


def main():
    db_url = require_db_url()
    if not db_url:
        return 1
    try:
        subprocess.run(["psql", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("psql is not installed or not available on PATH.")
        return 1

    print("== Checking migration objects ==")
    objects_ok = check_object_exists(db_url)
    print("\n== Checking behavior ==")
    behavior_ok = behavioral_checks(db_url)

    if objects_ok and behavior_ok:
        print("\nAll guardrail checks passed.")
        return 0
    print("\nGuardrail validation failed.")
    return 2


if __name__ == "__main__":
    sys.exit(main())

