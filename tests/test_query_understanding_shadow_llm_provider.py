import json
import sys
import types
import unittest
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
from query_understanding.llm_normalizer import SHADOW_LLM_NORMALIZER_ENV  # noqa: E402
from query_understanding.shadow_llm_provider import build_shadow_llm_prompt, build_shadow_llm_query_plan  # noqa: E402


class _DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ShadowLLMProviderTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.analyzer.config = types.SimpleNamespace(
            gemini_api_key="test-key",
            reasoning_model="gemini-test-model",
        )
        self.analyzer.LLM_REQUEST_TIMEOUT_SECONDS = 3
        self.legacy_plan = {
            "schema_version": "query_plan.v1",
            "normalizer": {
                "name": "legacy",
                "model": None,
                "prompt_template_version": "legacy.parser.v1",
                "catalog_version": "query_understanding.catalog.v1",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            "input": {
                "raw_prompt": "2nd engineer with valid passport and strong leadership",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [],
            "unapplied_constraints": [],
            "semantic_query": "strong leadership",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

    def test_build_shadow_llm_prompt_mentions_query_plan_contract(self):
        prompt = build_shadow_llm_prompt("2nd engineer with valid passport", rank="2nd Engineer")
        self.assertIn("query_plan.v1", prompt)
        self.assertIn("unsupported_filter_family", prompt)
        self.assertIn("2nd engineer with valid passport", prompt)

    def test_build_shadow_llm_query_plan_posts_to_gemini_and_returns_valid_plan(self):
        plan_payload = {
            "schema_version": "query_plan.v1",
            "normalizer": {
                "name": "llm",
                "model": "gemini-test-model",
                "prompt_template_version": "query_understanding.shadow_llm.v1",
                "catalog_version": "query_understanding.catalog.v1",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            "input": {
                "raw_prompt": "2nd engineer with valid passport and strong leadership",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {
                    "id": "passport_validity",
                    "mode": "required",
                    "constraint": {"type": "passport_validity", "minimum_months_remaining": 6},
                    "source_text": "valid passport",
                    "confidence": "high",
                    "compatibility": {
                        "legacy_hard_constraints_key": "passport_validity",
                        "legacy_applied_constraint_id": "passport_validity",
                    },
                }
            ],
            "unapplied_constraints": [],
            "semantic_query": "strong leadership",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        with mock.patch.dict("os.environ", {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            with mock.patch("query_understanding.shadow_llm_provider.requests.post", return_value=_DummyResponse(
                {"candidates": [{"content": {"parts": [{"text": json.dumps(plan_payload)}]}}]}
            )) as post_mock:
                plan = build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt="2nd engineer with valid passport and strong leadership",
                    rank="2nd Engineer",
                    prompt_id="prompt-1",
                    legacy_plan=self.legacy_plan,
                )

        self.assertIsNotNone(plan)
        self.assertEqual(plan["normalizer"]["name"], "llm")
        self.assertEqual(plan["normalizer"]["model"], "gemini-test-model")
        self.assertEqual(plan["semantic_query"], "strong leadership")
        post_mock.assert_called_once()
        called_url = post_mock.call_args.args[0]
        self.assertIn("gemini-test-model", called_url)
        self.assertIn("query_plan.v1", post_mock.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])

    def test_build_shadow_llm_query_plan_falls_back_to_legacy_plan_on_invalid_output(self):
        with mock.patch.dict("os.environ", {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            with mock.patch("query_understanding.shadow_llm_provider.requests.post", return_value=_DummyResponse(
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
            )):
                plan = build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt="2nd engineer with valid passport and strong leadership",
                    rank="2nd Engineer",
                    prompt_id="prompt-2",
                    legacy_plan=self.legacy_plan,
                )

        self.assertEqual(plan["normalizer"]["name"], "legacy")
        self.assertEqual(plan["semantic_query"], self.legacy_plan["semantic_query"])


if __name__ == "__main__":
    unittest.main()
