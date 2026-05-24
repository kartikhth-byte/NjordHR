"""Repository adapters for persistence backends."""

from .candidate_event_repo import CandidateEventRepo
from .csv_feedback_repo import CSVFeedbackStore
from .csv_registry_repo import CSVFileRegistry
from .ai_store_factory import AIStoreBundle, build_ai_store_bundle
from .dual_write_ai_store_repo import DualWriteAIRegistryRepo, DualWriteAIFeedbackStore
from .feedback_repo import FeedbackRepo
from .registry_repo import RegistryRepo
from .supabase_feedback_repo import SupabaseFeedbackStore
from .supabase_registry_repo import SupabaseFileRegistry

__all__ = [
    "CandidateEventRepo",
    "CSVFeedbackStore",
    "CSVFileRegistry",
    "AIStoreBundle",
    "build_ai_store_bundle",
    "DualWriteAIRegistryRepo",
    "DualWriteAIFeedbackStore",
    "FeedbackRepo",
    "RegistryRepo",
    "SupabaseFeedbackStore",
    "SupabaseFileRegistry",
]
