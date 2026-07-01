import json
import unittest
from pathlib import Path

from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_age_range_evidence_corpus,
    load_corpus,
    validate_query_plan_fixture,
)


CORPUS_FILE = Path("docs/eval-evidence/age-range-shadow-normalizer-corpus-2026-07-01.json")


class AgeRangeNormalizerEvidenceTests(unittest.TestCase):
    def test_seed_corpus_metrics_are_fixture_only_and_not_promotion_ready(self):
        report = evaluate_age_range_evidence_corpus(load_corpus(CORPUS_FILE))

        self.assertEqual(report["family"], "age_range")
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
        self.assertEqual(report["summary"]["class_c_safe_route_rate"], 1.0)
        self.assertFalse(report["promotion_gate"]["passes"])
        self.assertEqual(report["promotion_gate"]["failures"], ["real_llm_run_required"])

    def test_corpus_includes_cross_family_prompts_by_class(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        for class_name in ("A", "B", "C"):
            availability_count = sum(
                1
                for case in corpus["cases"]
                if case["class"] == class_name
                and ("available" in case["prompt"] or "availability" in case["prompt"])
            )
            vessel_tonnage_count = sum(
                1
                for case in corpus["cases"]
                if case["class"] == class_name and "vessel tonnage" in case["prompt"]
            )
            coc_country_count = sum(
                1
                for case in corpus["cases"]
                if case["class"] == class_name and "CoC" in case["prompt"]
            )
            self.assertGreaterEqual(availability_count, 1)
            self.assertGreaterEqual(vessel_tonnage_count, 1)
            self.assertGreaterEqual(coc_country_count, 1)

        self.assertEqual(cases["A078"]["llm_query_plan"]["constraints"][0]["filter_family"], "age_range")
        self.assertEqual(cases["B079"]["llm_query_plan"]["constraints"][0]["filter_family"], "age_range")
        self.assertFalse(cases["C037"]["llm_query_plan"]["constraints"])
        self.assertEqual(cases["C037"]["llm_query_plan"]["unapplied"][0]["reason"], "no matching capability")
        self.assertFalse(cases["C038"]["llm_query_plan"]["constraints"])
        self.assertEqual(cases["C038"]["llm_query_plan"]["unapplied"][0]["reason"], "no matching capability")
        self.assertFalse(cases["C039"]["llm_query_plan"]["constraints"])
        self.assertEqual(cases["C039"]["llm_query_plan"]["unapplied"][0]["reason"], "no matching capability")

    def test_corpus_pins_dob_and_birth_year_as_unapplied(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        self.assertIn("born after 1980", cases["C025"]["prompt"])
        self.assertEqual(cases["C025"]["llm_query_plan"]["unapplied"][0]["span"]["text"], "born after 1980")
        self.assertIn("DOB after 1990", cases["C037"]["prompt"])
        self.assertEqual(cases["C037"]["llm_query_plan"]["unapplied"][0]["span"]["text"], "DOB after 1990")

    def test_corpus_pins_exact_age_and_bounds(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        exact_params = cases["A051"]["llm_query_plan"]["constraints"][0]["parameters"]
        self.assertEqual(exact_params["minimum_years"], exact_params["maximum_years"])

        lower_payload = json.loads(json.dumps(cases["A001"]["llm_query_plan"]))
        lower_payload["constraints"][0]["parameters"]["minimum_years"] = 16
        lower_payload["constraints"][0]["parameters"]["maximum_years"] = 16
        lower_payload["constraints"][0]["parameters"]["display_value"] = "age between 16 and 16"
        lower_payload["constraints"][0]["source_span"] = {
            "text": "age between 16 and 16",
            "start": 0,
            "end": 21,
        }
        lower_result = validate_query_plan_fixture(lower_payload, prompt_normalized="age between 16 and 16")
        self.assertTrue(lower_result.accepted)

        upper_payload = json.loads(json.dumps(lower_payload))
        upper_payload["constraints"][0]["parameters"]["minimum_years"] = 75
        upper_payload["constraints"][0]["parameters"]["maximum_years"] = 75
        upper_payload["constraints"][0]["parameters"]["display_value"] = "age between 75 and 75"
        upper_payload["constraints"][0]["source_span"] = {
            "text": "age between 75 and 75",
            "start": 0,
            "end": 21,
        }
        upper_result = validate_query_plan_fixture(upper_payload, prompt_normalized="age between 75 and 75")
        self.assertTrue(upper_result.accepted)

    def test_query_plan_fixture_rejects_reversed_age_range(self):
        corpus = load_corpus(CORPUS_FILE)
        case = next(item for item in corpus["cases"] if item["id"] == "A001")
        payload = json.loads(json.dumps(case["llm_query_plan"]))
        payload["constraints"][0]["parameters"]["minimum_years"] = 60
        payload["constraints"][0]["parameters"]["maximum_years"] = 40

        result = validate_query_plan_fixture(payload, prompt_normalized=case["prompt"])

        self.assertFalse(result.accepted)
        self.assertTrue(any("minimum_years cannot exceed maximum_years" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
