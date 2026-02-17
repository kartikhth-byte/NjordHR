from repositories.csv_candidate_event_repo import CSVCandidateEventRepo


def build_candidate_event_repo(flags, base_folder="Verified_Resumes", server_url="http://127.0.0.1:5000"):
    """
    Build candidate event repository for current runtime flags.
    Supabase adapter will be added in the next phase; CSV remains default.
    """
    if getattr(flags, "use_supabase_db", False):
        print("[CONFIG] USE_SUPABASE_DB=true requested, but Supabase repo is not wired yet. Falling back to CSV repo.")
    return CSVCandidateEventRepo(base_folder=base_folder, server_url=server_url)
