import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.availability_normalizer_evidence import _gemini_api_key_from_config
from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_availability_helper_tool_fixture_corpus,
    evaluate_availability_llm_corpus,
    load_corpus,
    repair_query_plan_payload,
)
from query_understanding.compound_prompt_normalizer_provider import (
    COMPOUND_NORMALIZER_DEFAULT_MODEL,
    AvailabilityNormalizerProviderResult,
    build_availability_normalizer_prompt,
    build_vessel_tonnage_normalizer_prompt,
    call_gemini_availability_normalizer,
)


CORPUS_FILE = Path("docs/eval-evidence/availability-shadow-normalizer-corpus-2026-06-29.json")


class _FakeGeminiResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class AvailabilityNormalizerLlmProviderTests(unittest.TestCase):
    def test_prompt_uses_llm_catalog_without_executor_ids(self):
        prompt = build_availability_normalizer_prompt(
            "available within 30 days",
            prompt_normalized="available within 30 days",
            reference_date="2026-06-29",
        )

        self.assertIn('"family": "availability"', prompt)
        self.assertIn("prompt_normalized", prompt)
        self.assertIn("evidence_reference_date", prompt)
        self.assertIn("2026-06-29", prompt)
        self.assertIn("source_span", prompt)
        self.assertIn("candidate_families", prompt)
        self.assertIn("availability mixed with day-of-week constraints", prompt)
        self.assertNotIn("executor_id", prompt)

    def test_vessel_tonnage_prompt_uses_catalog_without_executor_ids(self):
        prompt = build_vessel_tonnage_normalizer_prompt(
            "Need candidates with vessel tonnage above 50000 GT",
            prompt_normalized="Need candidates with vessel tonnage above 50000 GT",
            reference_date="2026-06-29",
        )

        self.assertIn('"family": "vessel_tonnage"', prompt)
        self.assertIn('"family": "availability"', prompt)
        self.assertIn("filter_family=\"vessel_tonnage\"", prompt)
        self.assertIn("Unit must be one of: any, unspecified, gt_grt, dwt", prompt)
        self.assertIn("available", prompt)
        self.assertIn("Do not put unrelated availability", prompt)
        self.assertNotIn("executor_id", prompt)

    def test_gemini_provider_parses_json_payload(self):
        calls = []
        payload = {
            "version": "v1",
            "constraints": [],
            "soft_signals": [],
            "unapplied": [
                {
                    "span": {"text": "available only on Tuesdays", "start": 0, "end": 27},
                    "reason": "out of scope",
                }
            ],
            "needs_review": [],
        }

        def fake_post(url, *, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return _FakeGeminiResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "```json\n" + __import__("json").dumps(payload) + "\n```"}
                                ]
                            }
                        }
                    ]
                }
            )

        result = call_gemini_availability_normalizer(
            "available only on Tuesdays",
            prompt_normalized="available only on Tuesdays",
            reference_date="2026-06-29",
            api_key="test-key",
            post=fake_post,
        )

        self.assertEqual(result.model_id, COMPOUND_NORMALIZER_DEFAULT_MODEL)
        self.assertEqual(result.parsed_payload, payload)
        self.assertIsNone(result.transport_error)
        self.assertEqual(calls[0]["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(calls[0]["json"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("responseSchema", calls[0]["json"]["generationConfig"])
        provider_prompt = calls[0]["json"]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("executor_id", provider_prompt)

    def test_gemini_provider_includes_helper_context_when_enabled(self):
        calls = []
        payload = {
            "version": "v1",
            "constraints": [],
            "soft_signals": [],
            "unapplied": [],
            "needs_review": [],
        }

        def fake_post(url, *, headers, json, timeout):
            calls.append({"json": json})
            return _FakeGeminiResponse(
                {
                    "candidates": [
                        {"content": {"parts": [{"text": __import__("json").dumps(payload)}]}}
                    ]
                }
            )

        result = call_gemini_availability_normalizer(
            "Need crew available within 30 days",
            prompt_normalized="Need crew available within 30 days",
            reference_date="2026-06-29",
            api_key="test-key",
            use_helper_tools=True,
            post=fake_post,
        )

        self.assertEqual(result.helper_tool_version, "1.0.0")
        self.assertTrue(result.helper_tool_calls)
        self.assertTrue(result.helper_tool_context)
        provider_prompt = calls[0]["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("provider_helper_tool_outputs", provider_prompt)
        self.assertIn("display_value MUST equal source_span.text exactly", provider_prompt)
        self.assertIn("locate_prompt_span.v1", provider_prompt)
        self.assertIn("check_availability_parameters.v1", provider_prompt)
        self.assertNotIn("text appears more than once", provider_prompt)
        self.assertNotIn("executor_id", provider_prompt)

    def test_gemini_provider_reports_missing_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            result = call_gemini_availability_normalizer(
                "available immediately",
                prompt_normalized="available immediately",
                reference_date="2026-06-29",
                api_key="",
                post=lambda *_, **__: self.fail("post should not be called"),
            )

        self.assertIsNone(result.parsed_payload)
        self.assertEqual(result.transport_error, "missing_api_credentials")

    def test_cli_config_fallback_reads_gemini_key_without_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.ini"
            config_path.write_text("[Credentials]\nGemini_API_Key = test-key\n", encoding="utf-8")

            self.assertEqual(_gemini_api_key_from_config(config_path), "test-key")

    def test_llm_evidence_mode_uses_provider_payloads_and_stays_unpromoted(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        fixture_payloads = {
            case["id"]: case["llm_query_plan"]
            for case in corpus["cases"]
            if isinstance(case, dict)
        }

        reference_dates = []

        def provider(prompt, *, prompt_normalized, reference_date, catalog):
            reference_dates.append(reference_date)
            case = next(item for item in corpus["cases"] if item["prompt"] == prompt)
            return AvailabilityNormalizerProviderResult(
                model_id="fake-model",
                prompt_template_version="fake-template",
                raw_llm_output=json.dumps(fixture_payloads[case["id"]]),
                parsed_payload=fixture_payloads[case["id"]],
            )

        report = evaluate_availability_llm_corpus(corpus, provider=provider)

        self.assertTrue(report["llm_invoked"])
        self.assertFalse(report["live_dispatch"])
        self.assertFalse(report["promoted_family"])
        self.assertEqual(report["mode"], "shadow_llm_evidence")
        self.assertEqual(report["summary"]["class_a_llm_match_rate"], 1.0)
        self.assertEqual(report["summary"]["class_b_correct_rate_against_human_label"], 1.0)
        self.assertEqual(report["summary"]["class_b_deterministic_baseline_correct_rate"], 0.0)
        self.assertEqual(report["summary"]["class_b_recall_lift"], 1.0)
        self.assertEqual(report["summary"]["class_b_recall_lift_status"], "measured_against_corpus_deterministic_baseline")
        self.assertNotIn("real_llm_run_required", report["promotion_gate"]["failures"])
        self.assertNotIn("corpus_size_below_200", report["promotion_gate"]["failures"])
        self.assertNotIn("class_b_recall_lift_not_measured", report["promotion_gate"]["failures"])
        self.assertEqual(len(report["llm_audit_records"]), len(corpus["cases"]))
        self.assertEqual(report["llm_audit_records"][0]["validator_result"], "accepted")
        self.assertEqual(set(reference_dates), {"2026-06-29"})

    def test_llm_evidence_records_helper_tool_audit_counts(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        corpus["cases"] = corpus["cases"][:3]

        def provider(prompt, *, prompt_normalized, reference_date, catalog):
            result = call_gemini_availability_normalizer(
                prompt,
                prompt_normalized=prompt_normalized,
                reference_date=reference_date,
                api_key="test-key",
                catalog=catalog,
                use_helper_tools=True,
                post=lambda *_, **__: _FakeGeminiResponse(
                    {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {"text": __import__("json").dumps(next(case for case in corpus["cases"] if case["prompt"] == prompt)["llm_query_plan"])}
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
            self.assertTrue(result.helper_tool_calls)
            return result

        report = evaluate_availability_llm_corpus(corpus, provider=provider)

        self.assertGreater(report["summary"]["helper_tool_call_count"], 0)
        self.assertGreater(report["summary"]["helper_tool_accepted_count"], 0)
        self.assertIn("helper_tool_calls", report["llm_audit_records"][0])
        self.assertEqual(
            set(report["llm_audit_records"][0]["helper_tool_calls"][0]),
            {"tool_id", "input_hash", "accepted", "result_hash", "errors"},
        )
        self.assertEqual(report["llm_audit_records"][0]["quality_failure_class"], "")
        self.assertNotIn("available immediately", json.dumps(report["llm_audit_records"][0]["helper_tool_calls"]))

    def test_llm_evidence_records_quality_failure_class_per_audit_record(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        corpus["cases"] = [next(case for case in corpus["cases"] if case["id"] == "A049")]
        payload = json.loads(json.dumps(corpus["cases"][0]["llm_query_plan"]))
        payload["constraints"][0]["parameters"]["display_value"] = "within 7 days"

        def provider(prompt, *, prompt_normalized, reference_date, catalog):
            return AvailabilityNormalizerProviderResult(
                model_id="fake-model",
                prompt_template_version="fake-template",
                raw_llm_output=json.dumps(payload),
                parsed_payload=payload,
            )

        report = evaluate_availability_llm_corpus(corpus, provider=provider)

        self.assertEqual(report["summary"]["quality_failure_class_counts"], {"display_value_mismatch_only": 1})
        self.assertEqual(report["case_results"][0]["quality_failure_class"], "display_value_mismatch_only")
        self.assertEqual(report["llm_audit_records"][0]["quality_failure_class"], "display_value_mismatch_only")

    def test_helper_tool_fixture_evidence_compares_without_claiming_real_llm_run(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        corpus["cases"] = corpus["cases"][:5]

        report = evaluate_availability_helper_tool_fixture_corpus(corpus)

        self.assertFalse(report["llm_invoked"])
        self.assertFalse(report["live_dispatch"])
        self.assertEqual(report["mode"], "shadow_helper_tool_fixture_evidence")
        self.assertEqual(report["summary"]["class_a_fixture_match_rate"], 1.0)
        self.assertGreater(report["summary"]["helper_tool_call_count"], 0)
        self.assertGreater(report["summary"]["helper_tool_accepted_count"], 0)
        self.assertGreaterEqual(report["summary"]["helper_tool_rejected_count"], 0)
        self.assertIn("real_llm_run_required", report["promotion_gate"]["failures"])
        self.assertEqual(len(report["llm_audit_records"]), 5)

    def test_llm_evidence_mode_repairs_unambiguous_span_offsets_only(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        corpus["cases"] = [next(case for case in corpus["cases"] if case["id"] == "A002")]
        payload = json.loads(json.dumps(corpus["cases"][0]["llm_query_plan"]))
        payload["constraints"][0]["source_span"]["start"] = 11
        payload["constraints"][0]["source_span"]["end"] = 35
        expected_start = corpus["cases"][0]["prompt"].index(payload["constraints"][0]["source_span"]["text"])

        def provider(prompt, *, prompt_normalized, reference_date, catalog):
            return AvailabilityNormalizerProviderResult(
                model_id="fake-model",
                prompt_template_version="fake-template",
                raw_llm_output=json.dumps(payload),
                parsed_payload=payload,
            )

        report = evaluate_availability_llm_corpus(corpus, provider=provider)

        audit = report["llm_audit_records"][0]
        self.assertEqual(audit["validator_result"], "accepted")
        self.assertEqual(audit["repair_actions"], ["query_plan.constraints[0].source_span.offsets_replayed"])
        self.assertEqual(audit["raw_parsed_payload"]["constraints"][0]["source_span"]["start"], 11)
        self.assertEqual(audit["parsed_payload"]["constraints"][0]["source_span"]["start"], expected_start)

    def test_repair_query_plan_payload_does_not_repair_ambiguous_span_text(self):
        payload = {
            "version": "v1",
            "constraints": [],
            "soft_signals": [],
            "unapplied": [
                {
                    "span": {"text": "available", "start": 50, "end": 59},
                    "reason": "out of scope",
                }
            ],
            "needs_review": [],
        }

        repaired, actions = repair_query_plan_payload(
            payload,
            prompt_normalized="available now and available later",
        )

        self.assertEqual(actions, ())
        self.assertEqual(repaired["unapplied"][0]["span"]["start"], 50)


if __name__ == "__main__":
    unittest.main()
