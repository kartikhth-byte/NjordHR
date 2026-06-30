import json
import unittest
from pathlib import Path

from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_vessel_tonnage_evidence_corpus,
    load_corpus,
    validate_query_plan_fixture,
)


CORPUS_FILE = Path("docs/eval-evidence/vessel-tonnage-shadow-normalizer-corpus-2026-06-30.json")


class VesselTonnageNormalizerEvidenceTests(unittest.TestCase):
    def test_seed_corpus_metrics_are_audit_only_and_not_promotion_ready(self):
        report = evaluate_vessel_tonnage_evidence_corpus(load_corpus(CORPUS_FILE))

        self.assertEqual(report["family"], "vessel_tonnage")
        self.assertFalse(report["llm_invoked"])
        self.assertFalse(report["live_dispatch"])
        self.assertTrue(report["promoted_family"])
        self.assertEqual(report["summary"]["total_cases"], 200)
        self.assertEqual(report["summary"]["class_counts"], {"A": 80, "B": 80, "C": 40})
        self.assertEqual(report["summary"]["schema_valid_rate"], 1.0)
        self.assertEqual(report["summary"]["unsafe_widening_count"], 0)
        self.assertEqual(report["summary"]["class_a_fixture_match_rate"], 1.0)
        self.assertEqual(report["summary"]["class_b_correct_rate_against_human_label"], 1.0)
        self.assertEqual(report["summary"]["class_b_deterministic_baseline_correct_rate"], 0.0)
        self.assertEqual(report["summary"]["class_b_recall_lift"], 1.0)
        self.assertEqual(report["summary"]["class_c_safe_route_rate"], 1.0)
        self.assertFalse(report["promotion_gate"]["passes"])
        self.assertEqual(report["promotion_gate"]["failures"], ["real_llm_run_required"])

    def test_corpus_includes_cross_family_availability_prompts_by_class(self):
        corpus = load_corpus(CORPUS_FILE)
        class_a = next(item for item in corpus["cases"] if item["id"] == "A080")
        class_b = next(item for item in corpus["cases"] if item["id"] == "B080")
        class_c = next(item for item in corpus["cases"] if item["id"] == "C040")

        for case in (class_a, class_b, class_c):
            self.assertIn("available within 30 days", case["prompt"])
        self.assertIn("vessel tonnage above 50000 GT", class_a["prompt"])
        self.assertIn("not less than 55k DWT", class_b["prompt"])
        self.assertIn("NRT above 50000", class_c["prompt"])
        self.assertEqual([item["filter_family"] for item in class_a["llm_query_plan"]["constraints"]], ["vessel_tonnage"])
        self.assertEqual([item["filter_family"] for item in class_b["llm_query_plan"]["constraints"]], ["vessel_tonnage"])
        self.assertFalse(class_c["llm_query_plan"]["constraints"])
        self.assertEqual(class_c["llm_query_plan"]["needs_review"][0]["candidate_families"], ["vessel_tonnage"])

    def test_corpus_pins_years_back_and_unspecified_unit_cases(self):
        corpus = load_corpus(CORPUS_FILE)

        years_back_case = next(
            item
            for item in corpus["cases"]
            if item["class"] == "A"
            and item["llm_query_plan"]["constraints"][0]["parameters"].get("years_back") == 1
        )
        unspecified_case = next(
            item
            for item in corpus["cases"]
            if item["class"] == "A"
            and item["llm_query_plan"]["constraints"][0]["parameters"].get("unit") == "unspecified"
        )
        any_unit_case = next(
            item
            for item in corpus["cases"]
            if item["class"] == "A"
            and item["llm_query_plan"]["constraints"][0]["parameters"].get("unit") == "any"
        )

        self.assertIn("last 1 year", years_back_case["prompt"])
        self.assertIn("vessel tonnage", unspecified_case["prompt"])
        self.assertIn("any tonnage unit", any_unit_case["prompt"])

    def test_corpus_pins_class_b_and_class_c_diversity_cases(self):
        corpus = load_corpus(CORPUS_FILE)

        self.assertTrue(any("approximately" in item["prompt"] for item in corpus["cases"] if item["class"] == "B"))
        self.assertTrue(any("exactly" in item["prompt"] for item in corpus["cases"] if item["class"] == "B"))
        self.assertTrue(any("Aframax-only" in item["prompt"] for item in corpus["cases"] if item["class"] == "C"))
        self.assertTrue(any("-" in item["prompt"] and "vessel tonnage" in item["prompt"] for item in corpus["cases"] if item["class"] == "C"))

        class_b_prompts = {item["prompt"].split(": ", 1)[1] for item in corpus["cases"] if item["class"] == "B"}
        class_c_prompts = {item["prompt"].split(": ", 1)[1] for item in corpus["cases"] if item["class"] == "C"}
        self.assertGreaterEqual(len(class_b_prompts), 70)
        self.assertGreaterEqual(len(class_c_prompts), 35)

    def test_query_plan_fixture_rejects_invalid_tonnage_range(self):
        corpus = load_corpus(CORPUS_FILE)
        case = next(item for item in corpus["cases"] if item["id"] == "A003")
        payload = json.loads(json.dumps(case["llm_query_plan"]))
        payload["constraints"][0]["parameters"]["min_value"] = 90000
        payload["constraints"][0]["parameters"]["max_value"] = 30000

        result = validate_query_plan_fixture(payload, prompt_normalized=case["prompt"])

        self.assertFalse(result.accepted)
        self.assertTrue(any("min_value cannot exceed max_value" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
