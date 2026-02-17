from abc import ABC, abstractmethod


class CandidateEventRepo(ABC):
    @abstractmethod
    def log_event(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_latest_status_per_candidate(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_candidate_history(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def log_status_change(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def log_note_added(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_rank_counts(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_csv_stats(self, *args, **kwargs):
        raise NotImplementedError
