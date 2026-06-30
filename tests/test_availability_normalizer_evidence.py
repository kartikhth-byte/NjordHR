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
        self.assertEqual(report["summary"]["total_cases"], 200)
        self.assertEqual(report["summary"]["class_counts"], {"A": 80, "B": 80, "C": 40})
        self.assertEqual(report["summary"]["schema_valid_rate"], 1.0)
        self.assertEqual(report["summary"]["unsafe_widening_count"], 0)
        self.assertEqual(report["summary"]["class_a_fixture_match_rate"], 1.0)
        self.assertEqual(report["summary"]["class_b_correct_rate_against_human_label"], 1.0)
        self.assertEqual(report["summary"]["class_b_deterministic_baseline_correct_rate"], 0.0)
        self.assertEqual(report["summary"]["class_b_recall_lift"], 1.0)
        self.assertEqual(report["summary"]["class_b_recall_lift_status"], "measured_against_corpus_deterministic_baseline")
        self.assertEqual(report["summary"]["class_c_safe_route_rate"], 1.0)
        self.assertFalse(report["promotion_gate"]["passes"])
        self.assertIn("real_llm_run_required", report["promotion_gate"]["failures"])
        self.assertNotIn("corpus_size_below_200", report["promotion_gate"]["failures"])

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
        case = next(item for item in corpus["cases"] if item["id"] == "A017")
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
        class_c_case = next(case for case in corpus["cases"] if case["id"] == "C011")
        class_a_case = next(case for case in corpus["cases"] if case["id"] == "A001")
        class_c_span = class_c_case["llm_query_plan"]["unapplied"][0]["span"]
        class_c_case["llm_query_plan"]["constraints"] = class_a_case["llm_query_plan"]["constraints"]
        class_c_case["llm_query_plan"]["constraints"][0]["source_span"] = class_c_span
        class_c_case["llm_query_plan"]["unapplied"] = []

        report = evaluate_availability_evidence_corpus(corpus)

        c011 = next(item for item in report["case_results"] if item["id"] == "C011")
        self.assertTrue(c011["unsafe_widening"])
        self.assertEqual(report["summary"]["unsafe_widening_count"], 1)
        self.assertIn("unsafe_widening_present", report["promotion_gate"]["failures"])


if __name__ == "__main__":
    unittest.main()
