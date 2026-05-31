import sys
import types
import unittest
import os
from unittest import mock
from pathlib import Path


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
from scripts.query_understanding_shadow_audit import _merge_corpora


class ShadowAuditTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_llm_normalizer_stub_is_disabled(self):
        self.assertFalse(is_enabled())
        self.assertIsNone(normalize_prompt_to_query_plan_v1("2nd engineer", rank="2nd engineer"))

    def test_llm_normalizer_env_flag_can_be_enabled(self):
        with mock.patch.dict(os.environ, {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            self.assertTrue(is_enabled())

    def test_llm_normalizer_wrapper_delegates_when_enabled(self):
        provider = mock.Mock(return_value={"plan": {"schema_version": "query_plan.v1"}, "diagnostics": {"status": "success"}})
        with mock.patch.dict(os.environ, {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            result = normalize_prompt_to_query_plan_v1(
                prompt="2nd engineer",
                rank="2nd engineer",
                llm_plan_provider=provider,
            )
        provider.assert_called_once()
        self.assertEqual(result["plan"]["schema_version"], "query_plan.v1")
        self.assertEqual(result["diagnostics"]["status"], "success")

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

    def test_shadow_audit_entry_can_force_shadow_mode_for_recruiter_sessions(self):
        provider = mock.Mock(return_value={
            "plan": {
                "schema_version": "query_plan.v1",
                "normalizer": {
                    "name": "llm",
                    "model": "gemini",
                    "prompt_template_version": "query_understanding.shadow_llm.v1",
                    "catalog_version": "query_understanding.catalog.v1",
                    "created_at": "2026-05-25T00:00:00Z",
                },
                "input": {"raw_prompt": "2nd engineer", "rank_context": "2nd Engineer", "ui_filters": {"schema_version": "ui_filters.v1", "filters": []}},
                "applied_constraints": [],
                "unapplied_constraints": [],
                "semantic_query": "2nd engineer",
                "unrecognized_residual": [],
                "warnings": [],
                "validation": {"status": "valid", "errors": []},
            },
            "diagnostics": {"status": "success", "reason": "ok"},
        })

        entry = build_shadow_audit_entry(
            self.analyzer,
            "2nd engineer between 30 and 50 years old",
            rank="2nd Engineer",
            prompt_id="prompt-force",
            llm_plan_provider=provider,
            force_shadow=True,
        )

        provider.assert_called_once()
        self.assertEqual(entry["shadow_mode"], "enabled")
        self.assertTrue(entry["shadow_wiring"]["llm_plan_requested"])
        self.assertEqual(entry["shadow_wiring"]["llm_plan_source"], "llm")

    def test_merge_corpora_concatenates_family_entries(self):
        bootstrap = {
            "status": "bootstrap",
            "date": "2026-04-08",
            "purpose": ["bootstrap corpus"],
            "families": {
                "age_range": [{"prompt": "between 30 and 50", "expected_primary_family": "age_range"}],
                "us_visa": [{"prompt": "valid visa", "expected_primary_family": "us_visa"}],
            },
        }
        tail = {
            "status": "gold",
            "date": "2026-05-26",
            "purpose": ["tail corpus"],
            "families": {
                "age_range": [{"prompt": "not below 30", "expected_primary_family": "age_range"}],
            },
        }

        merged = _merge_corpora([
            (Path("/tmp/bootstrap.json"), bootstrap),
            (Path("/tmp/tail.json"), tail),
        ])

        self.assertEqual(merged["status"], "combined_shadow_audit_corpus")
        self.assertEqual(merged["prompt_count"], 3)
        self.assertEqual(merged["family_counts"]["age_range"], 2)
        self.assertEqual(len(merged["families"]["age_range"]), 2)
        self.assertEqual(merged["families"]["age_range"][0]["prompt"], "between 30 and 50")
        self.assertEqual(merged["source_corpora"][0]["path"], "/tmp/bootstrap.json")
        self.assertIsInstance(merged["purpose"], list)
        self.assertGreater(len(merged["purpose"]), 0)
        self.assertEqual(merged["duplicate_prompt_warnings"], [])

    def test_merge_corpora_flags_duplicate_prompt_pairs(self):
        first = {
            "status": "bootstrap",
            "date": "2026-04-08",
            "purpose": ["bootstrap corpus"],
            "families": {
                "age_range": [{"prompt": "between 30 and 50", "expected_primary_family": "age_range"}],
            },
        }
        second = {
            "status": "tail",
            "date": "2026-05-26",
            "purpose": ["tail corpus"],
            "families": {
                "age_range": [{"prompt": "between 30 and 50", "expected_primary_family": "age_range"}],
            },
        }

        merged = _merge_corpora([
            (Path("/tmp/first.json"), first),
            (Path("/tmp/second.json"), second),
        ])

        self.assertEqual(len(merged["duplicate_prompt_warnings"]), 1)
        warning = merged["duplicate_prompt_warnings"][0]
        self.assertEqual(warning["family"], "age_range")
        self.assertEqual(warning["prompt"], "between 30 and 50")
        self.assertEqual(warning["first_seen_in"], "/tmp/first.json")
        self.assertEqual(warning["duplicate_in"], "/tmp/second.json")


if __name__ == "__main__":
    unittest.main()
