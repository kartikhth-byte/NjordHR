from csv_manager import CSVManager
from repositories.candidate_event_repo import CandidateEventRepo


class CSVCandidateEventRepo(CandidateEventRepo):
    """CSV-backed candidate event repository (current default backend)."""

    def __init__(self, base_folder="Verified_Resumes", server_url="http://127.0.0.1:5000"):
        self._manager = CSVManager(base_folder=base_folder, server_url=server_url)

    def log_event(self, *args, **kwargs):
        return self._manager.log_event(*args, **kwargs)

    def get_latest_status_per_candidate(self, *args, **kwargs):
        return self._manager.get_latest_status_per_candidate(*args, **kwargs)

    def get_candidate_history(self, *args, **kwargs):
        return self._manager.get_candidate_history(*args, **kwargs)

    def log_status_change(self, *args, **kwargs):
        return self._manager.log_status_change(*args, **kwargs)

    def log_note_added(self, *args, **kwargs):
        return self._manager.log_note_added(*args, **kwargs)

    def get_rank_counts(self, *args, **kwargs):
        return self._manager.get_rank_counts(*args, **kwargs)

    def get_csv_stats(self, *args, **kwargs):
        return self._manager.get_csv_stats(*args, **kwargs)
