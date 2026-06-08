import os
from dataclasses import dataclass

from repositories.dual_write_ai_store_repo import DualWriteAIRegistryRepo, DualWriteAIFeedbackStore
from repositories.csv_feedback_repo import CSVFeedbackStore
from repositories.csv_registry_repo import CSVFileRegistry
from repositories.supabase_feedback_repo import SupabaseFeedbackStore
from repositories.supabase_registry_repo import SupabaseFileRegistry
from runtime_env import normalize_env_value, normalized_url


@dataclass(frozen=True)
class AIStoreBundle:
    registry: object
    feedback: object
    ingest_registry_cache: object | None = None


def build_ai_store_bundle(
    feature_flags,
    *,
    registry_db_path: str,
    feedback_db_path: str,
    supabase_url: str | None = None,
    supabase_api_key: str | None = None,
):
    """
    Build the AI registry/feedback dependency bundle from feature flags.

    The caller supplies runtime paths so the factories stay independent from
    the analyzer and can be reused by tests, scripts, and future startup code.
    """
    use_supabase = bool(getattr(feature_flags, "use_supabase_db", False))
    if use_supabase:
        resolved_supabase_url = normalized_url(supabase_url or "")
        resolved_supabase_key = normalize_env_value(supabase_api_key or "")
        if not resolved_supabase_url or not resolved_supabase_key:
            raise RuntimeError(
                "USE_SUPABASE_DB=true requested but SUPABASE_URL and SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY are missing."
            )
        csv_registry = CSVFileRegistry(registry_db_path)
        csv_feedback = CSVFeedbackStore(feedback_db_path)
        supabase_registry = SupabaseFileRegistry(
            supabase_url=resolved_supabase_url,
            service_role_key=resolved_supabase_key,
        )
        supabase_feedback = SupabaseFeedbackStore(
            supabase_url=resolved_supabase_url,
            service_role_key=resolved_supabase_key,
        )
        idempotency_db_path = os.path.join(
            os.path.dirname(os.path.abspath(registry_db_path)),
            "ai_store_dual_write_idempotency.db",
        )
        if bool(getattr(feature_flags, "use_dual_write", False)):
            read_repo = supabase_registry if bool(getattr(feature_flags, "use_supabase_reads", False)) else csv_registry
            registry = DualWriteAIRegistryRepo(
                primary_repo=csv_registry,
                secondary_repo=supabase_registry,
                idempotency_db_path=idempotency_db_path,
                read_repo=read_repo,
            )
            feedback = DualWriteAIFeedbackStore(
                primary_repo=csv_feedback,
                secondary_repo=supabase_feedback,
                idempotency_db_path=idempotency_db_path,
                read_repo=supabase_feedback if bool(getattr(feature_flags, "use_supabase_reads", False)) else csv_feedback,
            )
        else:
            registry = supabase_registry
            feedback = supabase_feedback
        ingest_registry_cache = csv_registry
    else:
        registry = CSVFileRegistry(registry_db_path)
        feedback = CSVFeedbackStore(feedback_db_path)
        ingest_registry_cache = None

    return AIStoreBundle(
        registry=registry,
        feedback=feedback,
        ingest_registry_cache=ingest_registry_cache,
    )
