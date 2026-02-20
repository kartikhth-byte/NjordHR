import os

from repositories.csv_candidate_event_repo import CSVCandidateEventRepo
from repositories.dual_write_candidate_event_repo import DualWriteCandidateEventRepo
from repositories.supabase_candidate_event_repo import (
    SupabaseCandidateEventRepo,
    can_enable_supabase_repo,
    resolve_supabase_api_key,
)


def build_candidate_event_repo(flags, base_folder="Verified_Resumes", server_url="http://127.0.0.1:5000"):
    """
    Build candidate event repository for current runtime flags.
    Supabase adapter will be added in the next phase; CSV remains default.
    """
    csv_repo = CSVCandidateEventRepo(base_folder=base_folder, server_url=server_url)

    if not getattr(flags, "use_supabase_db", False):
        return csv_repo

    if not can_enable_supabase_repo():
        print("[CONFIG] USE_SUPABASE_DB=true requested but SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are missing. Falling back to CSV repo.")
        return csv_repo

    supabase_api_key = resolve_supabase_api_key()
    if os.getenv("SUPABASE_SECRET_KEY", "").strip():
        print("[CONFIG] Supabase auth: using SUPABASE_SECRET_KEY.")
    else:
        print("[CONFIG] Supabase auth: using legacy SUPABASE_SERVICE_ROLE_KEY.")

    supabase_repo = SupabaseCandidateEventRepo(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        service_role_key=supabase_api_key,
        server_url=server_url
    )

    if getattr(flags, "use_dual_write", False):
        idempotency_db_path = os.path.join(base_folder, "dual_write_idempotency.db")
        read_repo = supabase_repo if getattr(flags, "use_supabase_reads", False) else csv_repo
        print("[CONFIG] Using dual-write candidate event repository (primary=csv, mirror=supabase).")
        return DualWriteCandidateEventRepo(
            primary_repo=csv_repo,
            secondary_repo=supabase_repo,
            idempotency_db_path=idempotency_db_path,
            read_repo=read_repo
        )

    print("[CONFIG] Using Supabase candidate event repository.")
    return supabase_repo
