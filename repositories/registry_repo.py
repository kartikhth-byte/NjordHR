from abc import ABC, abstractmethod


class RegistryRepo(ABC):
    @abstractmethod
    def generate_resume_id(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def needs_processing(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def upsert_file_record(self, *args, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def get_resume_id(self, *args, **kwargs):
        raise NotImplementedError
