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
                    "filter_family": "passport_validity",
                    "parameters": {"is_valid": True, "minimum_months_remaining": 6},
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
                result = build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt="2nd engineer with valid passport and strong leadership",
                    rank="2nd Engineer",
                    prompt_id="prompt-1",
                    legacy_plan=self.legacy_plan,
                )

        self.assertIsNotNone(result)
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        plan = result["plan"]
        self.assertEqual(plan["normalizer"]["name"], "llm")
        self.assertEqual(plan["normalizer"]["model"], "gemini-test-model")
        self.assertEqual(plan["semantic_query"], "strong leadership")
        self.assertTrue(plan["applied_constraints"][0]["constraint"]["must_be_valid"])
        self.assertEqual(plan["applied_constraints"][0]["constraint"]["minimum_months_remaining"], 6)
        post_mock.assert_called_once()
        called_url = post_mock.call_args.args[0]
        self.assertIn("gemini-test-model", called_url)
        self.assertIn("query_plan.v1", post_mock.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])

    def test_build_shadow_llm_query_plan_falls_back_to_legacy_plan_on_invalid_output(self):
        with mock.patch.dict("os.environ", {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            with mock.patch("query_understanding.shadow_llm_provider.requests.post", return_value=_DummyResponse(
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
            )):
                result = build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt="2nd engineer with valid passport and strong leadership",
                    rank="2nd Engineer",
                    prompt_id="prompt-2",
                    legacy_plan=self.legacy_plan,
                )

        self.assertEqual(result["diagnostics"]["status"], "fallback")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_model_json")
        self.assertEqual(result["plan"]["normalizer"]["name"], "legacy")
        self.assertEqual(result["plan"]["semantic_query"], self.legacy_plan["semantic_query"])

    def test_build_shadow_llm_query_plan_translates_common_families_and_demotes_unsupported(self):
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
                "raw_prompt": "2nd engineer older than 32 with valid COC, STCW basic, and tanker gas endorsement",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "age_range", "parameters": {"minimum_years": 32}},
                {"family": "coc_document_gate", "constraint": {"type": "coc_document_gate", "required": True}},
                {"filter_family": "stcw_basic", "parameters": {"required": True}},
                {"filter_family": "us_visa", "parameters": {"validity": "valid", "minimum_months_remaining": 6}},
                {"filter_family": "stcw_endorsement", "parameters": {"endorsements_required": ["tanker_gas"]}},
                {"filter_family": "vessel_type", "parameters": {"display_value": "tanker"}},
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
            )):
                result = build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt="2nd engineer older than 32 with valid COC, STCW basic, and tanker gas endorsement",
                    rank="2nd Engineer",
                    prompt_id="prompt-3",
                    legacy_plan=self.legacy_plan,
                )

        self.assertIsNotNone(result)
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        plan = result["plan"]
        applied_families = [item["id"] for item in plan["applied_constraints"]]
        self.assertIn("age_range", applied_families)
        self.assertIn("coc_document_gate", applied_families)
        self.assertIn("stcw_basic", applied_families)
        self.assertIn("us_visa", applied_families)
        self.assertIn("stcw_endorsement", applied_families)
        self.assertIn("vessel_type", [item["id"] for item in plan["unapplied_constraints"]])
        self.assertEqual(
            next(item for item in plan["applied_constraints"] if item["id"] == "age_range")["constraint"],
            {"type": "age_range", "minimum_years": 33, "maximum_years": None},
        )
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "coc_document_gate")["constraint"]["required"])
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "stcw_basic")["constraint"]["required"])
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "us_visa")["constraint"]["required"])
        self.assertEqual(
            next(item for item in plan["applied_constraints"] if item["id"] == "stcw_endorsement")["constraint"]["endorsements_required"],
            ["tanker_gas"],
        )
        self.assertEqual(plan["validation"]["status"], "degraded")

    def test_build_shadow_llm_query_plan_keeps_compound_passport_coc_and_availability(self):
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
                "raw_prompt": "ready to join with valid passport and COC",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "passport_validity", "parameters": {"validity": "valid"}},
                {"filter_family": "coc_document_gate", "constraint": {"type": "coc_document_gate", "required": True}},
                {"filter_family": "availability", "parameters": {"status": "available"}},
            ],
            "unapplied_constraints": [],
            "semantic_query": "",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan(
            "ready to join with valid passport and COC",
            plan_payload,
        )

        self.assertIsNotNone(result)
        plan = result["plan"]
        applied_families = [item["id"] for item in plan["applied_constraints"]]
        self.assertIn("availability", applied_families)
        self.assertIn("passport_validity", applied_families)
        self.assertIn("coc_document_gate", applied_families)
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "passport_validity")["constraint"]["must_be_valid"])
        self.assertEqual(
            next(item for item in plan["applied_constraints"] if item["id"] == "availability")["constraint"]["status"],
            "available",
        )
        self.assertEqual(
            next(item for item in plan["applied_constraints"] if item["id"] == "coc_document_gate")["constraint"]["required"],
            True,
        )

    def test_build_shadow_llm_query_plan_keeps_generic_passport_and_visa(self):
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
                "raw_prompt": "has valid passport and visa",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "passport_validity", "parameters": {"validity": "valid"}},
                {"filter_family": "us_visa", "parameters": {"required": True}},
            ],
            "unapplied_constraints": [],
            "semantic_query": "",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan("has valid passport and visa", plan_payload)

        self.assertIsNotNone(result)
        plan = result["plan"]
        self.assertIn("passport_validity", [item["id"] for item in plan["applied_constraints"]])
        self.assertIn("us_visa", [item["id"] for item in plan["applied_constraints"]])
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "passport_validity")["constraint"]["must_be_valid"])
        self.assertTrue(next(item for item in plan["applied_constraints"] if item["id"] == "us_visa")["constraint"]["required"])

    def _run_shadow_plan(self, prompt: str, plan_payload: dict, *, rank: str | None = "2nd Engineer"):
        with mock.patch.dict("os.environ", {SHADOW_LLM_NORMALIZER_ENV: "true"}, clear=False):
            with mock.patch(
                "query_understanding.shadow_llm_provider.requests.post",
                return_value=_DummyResponse({"candidates": [{"content": {"parts": [{"text": json.dumps(plan_payload)}]}}]}),
            ):
                return build_shadow_llm_query_plan(
                    self.analyzer,
                    prompt=prompt,
                    rank=rank,
                    prompt_id="prompt-shadow",
                    legacy_plan=self.legacy_plan,
                )

    def test_build_shadow_llm_query_plan_normalizes_bootstrap_family_shapes(self):
        cases = [
            (
                "age_range:13",
                "2nd engineer older than 32",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "2nd engineer older than 32",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "age_range", "min_age": 33, "reason": "hard_filter"},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "2nd engineer",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["age_range", "rank_match"], "unapplied": [], "semantic": ""},
            ),
            (
                "age_range:13_source_text_only",
                "2nd engineer older than 32",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "2nd engineer older than 32",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [],
                    "unapplied_constraints": [
                        {
                            "filter_family": "age_range",
                            "reason": "unsupported_filter_family",
                            "source_text": "older than 32",
                        }
                    ],
                    "semantic_query": "2nd engineer older than 32",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["age_range", "rank_match"], "unapplied": [], "semantic": ""},
            ),
            (
                "age_range:14",
                "3rd engineer over 29",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "3rd engineer over 29",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "age_range", "parameters": {"minimum_years": 29}},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "3rd engineer",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["age_range", "rank_match"], "unapplied": [], "semantic": ""},
            ),
            (
                "us_visa:9",
                "need fitter with valid C1/D",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "need fitter with valid C1/D",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "rank_match", "parameters": {"rank": "fitter"}},
                    ],
                    "unapplied_constraints": [
                        {
                            "filter_family": "certificate_requirement",
                            "reason": "validation_failed",
                            "source_text": "need fitter with valid C1/D",
                        }
                    ],
                    "semantic_query": "need fitter with valid C1/D",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["rank_match", "us_visa"], "unapplied": [], "semantic": "need with valid C1/D"},
            ),
            (
                "us_visa:12",
                "D visa required for this 4th engineer search",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "D visa required for this 4th engineer search",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [],
                    "unapplied_constraints": [
                        {"filter_family": "us_visa", "reason": "unsupported_filter_family", "details": "D visa"},
                    ],
                    "semantic_query": "4th engineer",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["us_visa", "rank_match"], "unapplied": [], "semantic": ""},
            ),
            (
                "coc_document_gate:1",
                "valid COC",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2024-07-18T12:00:00Z",
                    },
                    "input": {
                        "raw_prompt": "valid COC",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "coc_document_gate", "constraint": "required"},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "valid COC",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["coc_document_gate"], "unapplied": [], "semantic": ""},
            ),
            (
                "coc_document_gate:15",
                "junior engineer coc required",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "junior engineer coc required",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "coc_document_gate", "constraint": "required"},
                        {"filter_family": "coc_grade_match", "parameters": {"grade": "junior_engineer"}},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "junior engineer coc required",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["coc_document_gate", "coc_grade_match"], "unapplied": [], "semantic": ""},
            ),
            (
                "coc_document_gate:bare_shorthand",
                "valid passport and COC",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "valid passport and COC",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "passport_validity", "parameters": {"validity": "valid"}},
                        {"filter_family": "certificate_requirement", "values": []},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "valid passport and COC",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["passport_validity", "coc_document_gate"], "unapplied": [], "semantic": ""},
            ),
            (
                "coc_document_gate:17",
                "certificate of competency required for eto",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "certificate of competency required for eto",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "certificate_requirement", "values": ["eto"]},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "eto",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["coc_document_gate", "rank_match"], "unapplied": [], "semantic": "eto"},
            ),
            (
                "stcw_basic:4",
                "valid stcw basic required",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "valid stcw basic required",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "stcw_basic", "parameters": {"required": True}},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "valid",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["stcw_basic"], "unapplied": [], "semantic": ""},
            ),
        ]

        for prompt_id, prompt, plan_payload, expected in cases:
            with self.subTest(prompt_id=prompt_id):
                result = self._run_shadow_plan(prompt, plan_payload)
                self.assertEqual(result["diagnostics"]["status"], "success")
                self.assertEqual(result["diagnostics"]["reason"], "ok")
                plan = result["plan"]
                self.assertEqual(plan["semantic_query"], expected["semantic"])
                self.assertEqual([item["id"] for item in plan["applied_constraints"]], expected["applied"])
                self.assertEqual([item["id"] for item in plan["unapplied_constraints"]], expected["unapplied"])

    def test_build_shadow_llm_query_plan_rejects_false_required_flags(self):
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
                "raw_prompt": "valid COC and stcw basic required",
                "rank_context": None,
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "coc_document_gate", "parameters": {"required": False}},
                {"filter_family": "stcw_basic", "parameters": {"required": False}},
            ],
            "unapplied_constraints": [],
            "semantic_query": "valid COC stcw basic required",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan("valid COC and stcw basic required", plan_payload)
        self.assertEqual(result["diagnostics"]["status"], "fallback")
        self.assertEqual(result["diagnostics"]["reason"], "schema_invalid")
        self.assertEqual(result["plan"]["normalizer"]["name"], "legacy")

    def test_build_shadow_llm_query_plan_canonicalizes_unsupported_unapplied_ids(self):
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
                "raw_prompt": "same sea service for 6 months",
                "rank_context": None,
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [],
            "unapplied_constraints": [
                {"filter_family": "sea_service", "details": "6 months sea service", "reason": "unsupported_filter_family"},
            ],
            "semantic_query": "same company",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan("same sea service for 6 months", plan_payload, rank=None)
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        self.assertEqual(result["plan"]["unapplied_constraints"][0]["id"], "min_sea_service")

    def test_build_shadow_llm_query_plan_handles_live_tail_regressions(self):
        cases = [
            (
                "unsupported_or_diagnostic:1",
                "2nd engineer with 7+ years sea service",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "2nd engineer with 7+ years sea service",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [],
                    "unapplied_constraints": [
                        {"filter_family": "rank_match", "reason": "validation_failed", "source_text": "2nd engineer with 7+ years sea service"},
                        {"filter_family": "sea_service", "reason": "unsupported_filter_family", "details": "7+ years sea service"},
                    ],
                    "semantic_query": "suitable for",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["rank_match"], "unapplied": ["min_sea_service"], "semantic": "suitable for", "validation": "degraded"},
            ),
            (
                "unsupported_or_diagnostic:12",
                "pumpman joinable in 30 days",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "pumpman joinable in 30 days",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [
                        {"filter_family": "availability", "parameters": {"value_type": "relative_phrase", "relative_days": 30, "display_value": "joinable in 30 days"}},
                    ],
                    "unapplied_constraints": [],
                    "semantic_query": "pumpman joinable in 30 days",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["availability", "rank_match"], "unapplied": [], "semantic": "joinable in 30 days", "validation": "valid"},
            ),
            (
                "unsupported_or_diagnostic:13",
                "need 3rd officer with 36 months sea service",
                {
                    "schema_version": "query_plan.v1",
                    "normalizer": {
                        "name": "llm",
                        "model": "gemini-test-model",
                        "prompt_template_version": "query_understanding.shadow_llm.v1",
                        "catalog_version": "query_understanding.catalog.v1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    "input": {
                        "raw_prompt": "need 3rd officer with 36 months sea service",
                        "rank_context": None,
                        "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
                    },
                    "applied_constraints": [],
                    "unapplied_constraints": [
                        {"filter_family": "rank_match", "reason": "validation_failed", "source_text": "need 3rd officer with 36 months sea service"},
                        {"filter_family": "sea_service", "reason": "unsupported_filter_family", "details": "36 months sea service"},
                    ],
                    "semantic_query": "need",
                    "unrecognized_residual": [],
                    "warnings": [],
                    "validation": {"status": "valid", "errors": []},
                },
                {"applied": ["rank_match"], "unapplied": ["min_sea_service"], "semantic": "need", "validation": "degraded"},
            ),
        ]

        for prompt_id, prompt, plan_payload, expected in cases:
            with self.subTest(prompt_id=prompt_id):
                result = self._run_shadow_plan(prompt, plan_payload)
                self.assertEqual(result["diagnostics"]["status"], "success")
                self.assertEqual(result["diagnostics"]["reason"], "ok")
                plan = result["plan"]
                self.assertEqual([item["id"] for item in plan["applied_constraints"]], expected["applied"])
                self.assertEqual([item["id"] for item in plan["unapplied_constraints"]], expected["unapplied"])
                self.assertEqual(plan["semantic_query"], expected["semantic"])
                self.assertEqual(plan["validation"]["status"], expected["validation"])

    def test_build_shadow_llm_query_plan_preserves_untranslated_certificate_requirement_without_repair(self):
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
                "raw_prompt": "need fitter with yellow fever certificate",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "rank_match", "parameters": {"rank": "fitter"}},
            ],
            "unapplied_constraints": [
                {
                    "filter_family": "certificate_requirement",
                    "reason": "validation_failed",
                    "source_text": "yellow fever certificate",
                }
            ],
            "semantic_query": "need fitter with yellow fever certificate",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan("need fitter with yellow fever certificate", plan_payload, rank="2nd Engineer")
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        plan = result["plan"]
        self.assertEqual([item["id"] for item in plan["applied_constraints"]], ["rank_match"])
        self.assertEqual([item["id"] for item in plan["unapplied_constraints"]], ["certificate_requirement"])
        self.assertEqual(plan["unapplied_constraints"][0]["reason"], "validation_failed")
        self.assertEqual(plan["semantic_query"], "need")
        self.assertEqual(plan["validation"]["status"], "degraded")

    def test_build_shadow_llm_query_plan_preserves_unrelated_certificate_requirement_when_visa_repairs(self):
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
                "raw_prompt": "need fitter with valid C1/D and yellow fever certificate",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "rank_match", "parameters": {"rank": "fitter"}},
            ],
            "unapplied_constraints": [
                {
                    "filter_family": "certificate_requirement",
                    "reason": "validation_failed",
                    "source_text": "yellow fever certificate",
                }
            ],
            "semantic_query": "need fitter with valid C1/D and yellow fever certificate",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan(
            "need fitter with valid C1/D and yellow fever certificate",
            plan_payload,
            rank="2nd Engineer",
        )
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        plan = result["plan"]
        self.assertEqual([item["id"] for item in plan["applied_constraints"]], ["rank_match", "us_visa"])
        self.assertEqual([item["id"] for item in plan["unapplied_constraints"]], ["certificate_requirement"])
        self.assertEqual(plan["unapplied_constraints"][0]["source_text"], "yellow fever certificate")
        self.assertEqual(plan["validation"]["status"], "degraded")
        self.assertEqual(plan["semantic_query"], "need with valid C1/D")

    def test_build_shadow_llm_query_plan_preserves_unrelated_certificate_requirement_with_broad_source_text(self):
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
                "raw_prompt": "need fitter with valid C1/D and yellow fever certificate",
                "rank_context": "2nd Engineer",
                "ui_filters": {"schema_version": "ui_filters.v1", "filters": []},
            },
            "applied_constraints": [
                {"filter_family": "rank_match", "parameters": {"rank": "fitter"}},
            ],
            "unapplied_constraints": [
                {
                    "filter_family": "certificate_requirement",
                    "reason": "validation_failed",
                    "source_text": "need fitter with valid C1/D and yellow fever certificate",
                }
            ],
            "semantic_query": "need fitter with valid C1/D and yellow fever certificate",
            "unrecognized_residual": [],
            "warnings": [],
            "validation": {"status": "valid", "errors": []},
        }

        result = self._run_shadow_plan(
            "need fitter with valid C1/D and yellow fever certificate",
            plan_payload,
            rank="2nd Engineer",
        )
        self.assertEqual(result["diagnostics"]["status"], "success")
        self.assertEqual(result["diagnostics"]["reason"], "ok")
        plan = result["plan"]
        self.assertEqual([item["id"] for item in plan["applied_constraints"]], ["rank_match", "us_visa"])
        self.assertEqual([item["id"] for item in plan["unapplied_constraints"]], ["certificate_requirement"])
        self.assertEqual(plan["unapplied_constraints"][0]["source_text"], "need fitter with valid C1/D and yellow fever certificate")
        self.assertEqual(plan["validation"]["status"], "degraded")
        self.assertEqual(plan["semantic_query"], "need with valid C1/D and yellow fever certificate")


if __name__ == "__main__":
    unittest.main()
