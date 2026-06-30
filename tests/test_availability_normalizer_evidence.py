import json
import unittest
from pathlib import Path

from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_availability_evidence_corpus,
    load_corpus,
    validate_query_plan_fixture,
)


CORPUS_FILE = Path("docs/eval-evidence/availability-shadow-normalizer-corpus-2026-06-29.json")


class AvailabilityNormalizerEvidenceTests(unittest.TestCase):
    def test_seed_corpus_metrics_are_audit_only_and_not_promotion_ready(self):
        report = evaluate_availability_evidence_corpus(load_corpus(CORPUS_FILE))

        self.assertEqual(report["family"], "availability")
        self.assertFalse(report["llm_invoked"])
        self.assertFalse(report["live_dispatch"])
        self.assertFalse(report["promoted_family"])
        self.assertEqual(report["summary"]["class_counts"], {"A": 5, "B": 5, "C": 5})
        self.assertEqual(report["summary"]["schema_valid_rate"], 1.0)
        self.assertEqual(report["summary"]["unsafe_widening_count"], 0)
        self.assertEqual(report["summary"]["class_a_fixture_match_rate"], 1.0)
        self.assertEqual(report["summary"]["class_b_correct_rate_against_human_label"], 1.0)
        self.assertIsNone(report["summary"]["class_b_recall_lift"])
        self.assertEqual(report["summary"]["class_b_recall_lift_status"], "not_measured_fixture_only")
        self.assertEqual(report["summary"]["class_c_safe_route_rate"], 1.0)
        self.assertFalse(report["promotion_gate"]["passes"])
        self.assertIn("real_llm_run_required", report["promotion_gate"]["failures"])
        self.assertIn("corpus_size_below_200", report["promotion_gate"]["failures"])

    def test_query_plan_fixture_rejects_bad_span(self):
        corpus = load_corpus(CORPUS_FILE)
        case = corpus["cases"][0]
        payload = json.loads(json.dumps(case["llm_query_plan"]))
        payload["constraints"][0]["source_span"]["start"] = 0

        result = validate_query_plan_fixture(
            payload,
            prompt_normalized=case["prompt"],
        )

        self.assertFalse(result.accepted)
        self.assertTrue(any("source_span must replay" in error for error in result.errors))

    def test_query_plan_fixture_rejects_invalid_catalog_parameters(self):
        corpus = load_corpus(CORPUS_FILE)
        case = corpus["cases"][1]
        payload = json.loads(json.dumps(case["llm_query_plan"]))
        payload["constraints"][0]["parameters"]["available_by_date"] = "2026-02-30"

        result = validate_query_plan_fixture(
            payload,
            prompt_normalized=case["prompt"],
        )

        self.assertFalse(result.accepted)
        self.assertTrue(any("available_by_date must match YYYY-MM-DD" in error for error in result.errors))

    def test_class_c_constraint_is_counted_as_unsafe_widening(self):
        corpus = json.loads(json.dumps(load_corpus(CORPUS_FILE)))
        class_c_case = next(case for case in corpus["cases"] if case["id"] == "C002")
        class_a_case = next(case for case in corpus["cases"] if case["id"] == "A001")
        class_c_case["llm_query_plan"]["constraints"] = class_a_case["llm_query_plan"]["constraints"]
        class_c_case["llm_query_plan"]["constraints"][0]["source_span"] = {
            "text": "Available only on Tuesdays",
            "start": 0,
            "end": 26,
        }
        class_c_case["llm_query_plan"]["unapplied"] = []

        report = evaluate_availability_evidence_corpus(corpus)

        c002 = next(item for item in report["case_results"] if item["id"] == "C002")
        self.assertTrue(c002["unsafe_widening"])
        self.assertEqual(report["summary"]["unsafe_widening_count"], 1)
        self.assertIn("unsafe_widening_present", report["promotion_gate"]["failures"])


if __name__ == "__main__":
    unittest.main()
