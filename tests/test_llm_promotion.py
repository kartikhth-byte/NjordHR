import os
import configparser
import sys
import tempfile
import types
import unittest
from pathlib import Path
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


class _FakeRegistry:
    def get_resume_id(self, file_path):
        return Path(file_path).stem

    def generate_resume_id(self, file_path):
        return Path(file_path).stem


class _FakeFeedbackStore:
    def get_recent_feedback(self, *_args, **_kwargs):
        return []


class LlmPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.download_root = Path(self.temp_dir.name)
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.analyzer.config = self._make_config(stage=0)
        self.analyzer.registry = _FakeRegistry()
        self.analyzer.feedback = _FakeFeedbackStore()
        self.analyzer._configured_ship_type_labels_cache = None
        self.rank = "2nd Engineer"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _legacy_aff_prompt(self):
        return "AFF required"

    def _make_config(self, stage=None):
        config = configparser.ConfigParser()
        config.add_section("Settings")
        config.set("Settings", "Default_Download_Folder", str(self.download_root))
        if stage is not None:
            config.set("Settings", "LLM_Promotion_Stage", str(stage))
        config.add_section("Advanced")
        config.set("Advanced", "min_similarity_score", "0.0")
        return config

    def _high_conf_plan(self, family_id="certificate_requirement", certificates=None):
        return {
            "applied_constraints": [
                {
                    "id": family_id,
                    "mode": "required",
                    "constraint": {
                        "type": family_id,
                        "certificates_required": list(certificates or ["cert_aff"]),
                    },
                    "source_text": "AFF required",
                    "confidence": "high",
                }
            ],
            "unapplied_constraints": [],
        }

    def test_promotion_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch(
            "query_understanding.shadow_llm_provider.build_shadow_llm_query_plan",
            side_effect=AssertionError("LLM should not be called when promotion is disabled"),
        ):
            os.environ.pop("NJORDHR_LLM_PROMOTED_FAMILIES", None)
            constraints = self.analyzer._extract_job_constraints(self._legacy_aff_prompt(), rank=self.rank)

        self.assertEqual(self.analyzer._llm_promoted_families(), set())
        self.assertEqual(constraints["applied_constraints"], ["stcw_endorsement"])
        self.assertNotIn("llm_promoted", constraints)
        self.assertNotIn("certificate_requirement", constraints["applied_constraints"])

    def test_promotion_stage_counter_from_config(self):
        cases = [
            (0, set()),
            (1, {"certificate_requirement"}),
            (3, {"certificate_requirement", "rank_match", "stcw_basic"}),
            (5, {"certificate_requirement", "rank_match", "age_range", "stcw_basic", "us_visa"}),
            (-1, set()),
            (7, {"certificate_requirement", "rank_match", "age_range", "stcw_basic", "us_visa"}),
        ]
        with mock.patch.dict(os.environ, {}, clear=False):
            for stage, expected in cases:
                with self.subTest(stage=stage):
                    self.analyzer.config = self._make_config(stage=stage)
                    self.assertEqual(self.analyzer._llm_promoted_families(), expected)

    def test_promotion_enabled_via_stage_for_certificate_requirement(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            self.analyzer.config = self._make_config(stage=1)
            with mock.patch(
                "query_understanding.shadow_llm_provider.build_shadow_llm_query_plan",
                return_value=self._high_conf_plan(),
            ) as provider:
                self.assertEqual(self.analyzer._llm_promoted_families(), {"certificate_requirement"})
                constraints = self.analyzer._extract_job_constraints(self._legacy_aff_prompt(), rank=self.rank)

        provider.assert_called_once()
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertIn("certificate_requirement", constraints["applied_constraints"])
        self.assertIn("cert_aff", constraints["hard_constraints"]["certifications"]["certificates_required"])
        self.assertIn("certificate_requirement", constraints["llm_promoted"])

    def test_promotion_env_var_overrides_stage_config(self):
        self.analyzer.config = self._make_config(stage=5)
        with mock.patch.dict(os.environ, {"NJORDHR_LLM_PROMOTED_FAMILIES": "us_visa"}, clear=False):
            self.assertEqual(self.analyzer._llm_promoted_families(), {"us_visa"})

    def test_promotion_env_var_ignores_unknown_family_ids(self):
        with mock.patch.dict(
            os.environ,
            {"NJORDHR_LLM_PROMOTED_FAMILIES": "us_visa,age-range,certificate_requirement"},
            clear=False,
        ):
            self.assertEqual(
                self.analyzer._llm_promoted_families(),
                {"us_visa", "certificate_requirement"},
            )

    def test_promotion_skips_when_legacy_already_applied(self):
        constraints = {
            "rank": self.rank,
            "hard_constraints": {
                "certifications": {
                    "certificates_required": ["cert_aff"],
                }
            },
            "applied_constraints": ["certificate_requirement"],
            "unapplied_constraints": [],
            "parsing_notes": [],
        }

        with mock.patch.dict(os.environ, {"NJORDHR_LLM_PROMOTED_FAMILIES": "certificate_requirement"}, clear=False):
            self.analyzer._merge_promoted_constraint(
                constraints,
                "certificate_requirement",
                {"certificates_required": ["cert_mfa"]},
            )

        self.assertEqual(constraints["applied_constraints"], ["certificate_requirement"])
        self.assertEqual(constraints["hard_constraints"]["certifications"]["certificates_required"], ["cert_aff"])
        self.assertNotIn("llm_promoted", constraints)

    def test_promotion_ignores_low_confidence(self):
        with mock.patch.dict(os.environ, {"NJORDHR_LLM_PROMOTED_FAMILIES": "certificate_requirement"}, clear=False), mock.patch(
            "query_understanding.shadow_llm_provider.build_shadow_llm_query_plan",
            return_value={
                "applied_constraints": [
                    {
                        "id": "certificate_requirement",
                        "mode": "required",
                        "constraint": {"type": "certificate_requirement", "certificates_required": ["cert_aff"]},
                        "source_text": "AFF required",
                        "confidence": "medium",
                    }
                ],
                "unapplied_constraints": [],
            },
        ):
            constraints = self.analyzer._extract_job_constraints(self._legacy_aff_prompt(), rank=self.rank)

        self.assertEqual(constraints["applied_constraints"], ["stcw_endorsement"])
        self.assertNotIn("certificate_requirement", constraints["applied_constraints"])
        self.assertNotIn("llm_promoted", constraints)

    def test_promotion_missing_config_setting_falls_back_to_default_stage(self):
        self.analyzer.config = self._make_config(stage=None)
        with mock.patch.dict(os.environ, {}, clear=False):
            self.assertEqual(self.analyzer._llm_promoted_families(), set())

    def test_promotion_handles_llm_error_gracefully(self):
        with mock.patch.dict(os.environ, {"NJORDHR_LLM_PROMOTED_FAMILIES": "certificate_requirement"}, clear=False), mock.patch(
            "query_understanding.shadow_llm_provider.build_shadow_llm_query_plan",
            side_effect=RuntimeError("boom"),
        ):
            constraints = self.analyzer._extract_job_constraints(self._legacy_aff_prompt(), rank=self.rank)

        self.assertEqual(constraints["applied_constraints"], ["stcw_endorsement"])
        self.assertNotIn("certificate_requirement", constraints["applied_constraints"])
        self.assertNotIn("llm_promoted", constraints)

    def test_promotion_does_not_affect_non_promoted_families(self):
        with mock.patch.dict(os.environ, {"NJORDHR_LLM_PROMOTED_FAMILIES": "certificate_requirement"}, clear=False), mock.patch(
            "query_understanding.shadow_llm_provider.build_shadow_llm_query_plan",
            return_value={
                "applied_constraints": [
                    {
                        "id": "certificate_requirement",
                        "mode": "required",
                        "constraint": {"type": "certificate_requirement", "certificates_required": ["cert_aff"]},
                        "source_text": "AFF required",
                        "confidence": "high",
                    },
                    {
                        "id": "rank_match",
                        "mode": "required",
                        "constraint": {"type": "rank_match", "rank": "chief_engineer"},
                        "source_text": "chief engineer",
                        "confidence": "high",
                    },
                ],
                "unapplied_constraints": [],
            },
        ):
            constraints = self.analyzer._extract_job_constraints(self._legacy_aff_prompt(), rank=self.rank)

        self.assertIn("certificate_requirement", constraints["applied_constraints"])
        self.assertNotIn("rank_match", constraints["applied_constraints"])
        self.assertNotIn("rank", constraints["hard_constraints"])


if __name__ == "__main__":
    unittest.main()
