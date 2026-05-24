import os
import tempfile
import unittest

from app_settings import FeatureFlags
from repositories.ai_store_factory import build_ai_store_bundle
from repositories.dual_write_ai_store_repo import DualWriteAIRegistryRepo, DualWriteAIFeedbackStore
from repositories.csv_feedback_repo import CSVFeedbackStore
from repositories.csv_registry_repo import CSVFileRegistry
from repositories.supabase_feedback_repo import SupabaseFeedbackStore
from repositories.supabase_registry_repo import SupabaseFileRegistry


class AIStoreFactoryTests(unittest.TestCase):
    def setUp(self):
        self._env = {key: os.environ.get(key) for key in [
            "SUPABASE_URL",
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ]}
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_local_bundle_uses_csv_stores(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = build_ai_store_bundle(
                FeatureFlags(False, False, False, False, False),
                registry_db_path=os.path.join(temp_dir, "registry.db"),
                feedback_db_path=os.path.join(temp_dir, "feedback.db"),
            )
            try:
                self.assertIsInstance(bundle.registry, CSVFileRegistry)
                self.assertIsInstance(bundle.feedback, CSVFeedbackStore)
                self.assertIsNone(bundle.ingest_registry_cache)
            finally:
                bundle.registry.close()
                bundle.feedback.close()

    def test_supabase_bundle_uses_supabase_stores_and_local_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = build_ai_store_bundle(
                FeatureFlags(True, False, False, False, False),
                registry_db_path=os.path.join(temp_dir, "registry.db"),
                feedback_db_path=os.path.join(temp_dir, "feedback.db"),
                supabase_url="https://example.supabase.co",
                supabase_api_key="sb_secret_test",
            )
            try:
                self.assertIsInstance(bundle.registry, SupabaseFileRegistry)
                self.assertIsInstance(bundle.feedback, SupabaseFeedbackStore)
                self.assertIsInstance(bundle.ingest_registry_cache, CSVFileRegistry)
            finally:
                bundle.ingest_registry_cache.close()

    def test_dual_write_bundle_wraps_supabase_and_csv_stores(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = build_ai_store_bundle(
                FeatureFlags(True, True, True, False, False),
                registry_db_path=os.path.join(temp_dir, "registry.db"),
                feedback_db_path=os.path.join(temp_dir, "feedback.db"),
                supabase_url="https://example.supabase.co",
                supabase_api_key="sb_secret_test",
            )
            try:
                self.assertIsInstance(bundle.registry, DualWriteAIRegistryRepo)
                self.assertIsInstance(bundle.feedback, DualWriteAIFeedbackStore)
                self.assertIsInstance(bundle.ingest_registry_cache, CSVFileRegistry)
            finally:
                bundle.ingest_registry_cache.close()


if __name__ == "__main__":
    unittest.main()
