import os

from repositories.csv_candidate_event_repo import CSVCandidateEventRepo
from repositories.supabase_candidate_event_repo import SupabaseCandidateEventRepo, can_enable_supabase_repo


def build_candidate_event_repo(flags, base_folder="Verified_Resumes", server_url="http://127.0.0.1:5000"):
    """
    Build candidate event repository for current runtime flags.
    Supabase adapter will be added in the next phase; CSV remains default.
    """
    if getattr(flags, "use_supabase_db", False):
        if can_enable_supabase_repo():
            print("[CONFIG] Using Supabase candidate event repository.")
            return SupabaseCandidateEventRepo(
                supabase_url=os.getenv("SUPABASE_URL", ""),
                service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
                server_url=server_url
            )
        print("[CONFIG] USE_SUPABASE_DB=true requested but SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY missing. Falling back to CSV repo.")
    return CSVCandidateEventRepo(base_folder=base_folder, server_url=server_url)
