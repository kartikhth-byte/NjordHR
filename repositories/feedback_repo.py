from abc import ABC, abstractmethod


class FeedbackRepo(ABC):
    @abstractmethod
    def add_feedback(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_recent_feedback(self, *args, **kwargs):
        raise NotImplementedError
