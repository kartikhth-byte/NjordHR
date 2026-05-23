import sys
import types
import unittest
import os
from unittest import mock


def _stub_ai_dependencies():
    if "fitz" not in sys.modules:
        sys.modules["fitz"] = types.ModuleType("fitz")

    if "PIL" not in sys.modules:
        pil_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        pil_module.Image = image_module
        sys.modules["PIL"] = pil_module
        sys.modules["PIL.Image"] = image_module

    if "pinecone" not in sys.modules:
        pinecone_module = types.ModuleType("pinecone")

        class DummyPinecone:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyServerlessSpec:
            def __init__(self, *_args, **_kwargs):
                pass

        pinecone_module.Pinecone = DummyPinecone
        pinecone_module.ServerlessSpec = DummyServerlessSpec
        sys.modules["pinecone"] = pinecone_module


_stub_ai_dependencies()
from ai_analyzer import AIResumeAnalyzer  # noqa: E402

from query_understanding.llm_normalizer import SHADOW_LLM_NORMALIZER_ENV, is_enabled, normalize_prompt_to_query_plan_v1
from query_understanding.shadow_audit import build_shadow_audit_entry


class ShadowAuditTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_llm_normalizer_stub_is_disabled(self):
        self.assertFalse(is_enabled())
        self.assertIsNone(normalize_prompt_to_query_plan_v1("2nd engineer", rank="2nd engineer"))

    def test_llm_normalizer_env_flag_can_be_enabled(self):
        with mock.patch.dict(os.environ, {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            self.assertTrue(is_enabled())

    def test_shadow_audit_entry_logs_legacy_plan_when_llm_disabled(self):
        entry = build_shadow_audit_entry(
            self.analyzer,
            "2nd engineer between 30 and 50 years old",
            rank="2nd Engineer",
            prompt_id="prompt-1",
        )
        self.assertEqual(entry["shadow_mode"], "disabled")
        self.assertIn("shadow_wiring", entry)
        self.assertFalse(entry["shadow_wiring"]["feature_flag_enabled"])
        self.assertIsNone(entry["llm_plan"])
        self.assertEqual(entry["comparison_results"], [])
        self.assertEqual(entry["legacy_plan"]["validation"]["status"], "valid")
        self.assertEqual(entry["legacy_comparison_records"][0]["family"], "age_range")

    def test_shadow_provider_is_only_invoked_when_feature_flag_is_enabled(self):
        provider = mock.Mock(side_effect=lambda **kwargs: {"plan": kwargs["legacy_plan"], "diagnostics": {"status": "fallback", "reason": "test"}})
        with mock.patch.dict(os.environ, {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            entry = build_shadow_audit_entry(
                self.analyzer,
                "2nd engineer between 30 and 50 years old",
                rank="2nd Engineer",
                prompt_id="prompt-2",
                llm_plan_provider=provider,
            )
        provider.assert_called_once()
        self.assertEqual(entry["shadow_mode"], "enabled")
        self.assertTrue(entry["shadow_wiring"]["feature_flag_enabled"])
        self.assertTrue(entry["shadow_wiring"]["llm_plan_provider_attached"])
        self.assertTrue(entry["shadow_wiring"]["llm_plan_requested"])
        self.assertTrue(entry["shadow_wiring"]["llm_plan_fallback_used"])
        self.assertEqual(entry["shadow_wiring"]["llm_plan_source"], "legacy_fallback")
        self.assertEqual(entry["shadow_wiring"]["failure_reason"], "test")
        self.assertEqual(entry["shadow_llm_diagnostics"]["reason"], "test")
        self.assertIsInstance(entry["comparison_results"], list)
        self.assertEqual(entry["llm_plan"]["schema_version"], "query_plan.v1")


if __name__ == "__main__":
    unittest.main()
