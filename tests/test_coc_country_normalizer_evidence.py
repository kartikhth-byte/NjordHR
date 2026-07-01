import json
import unittest
from pathlib import Path

from query_understanding.compound_prompt_normalizer_evidence import (
    evaluate_coc_country_evidence_corpus,
    load_corpus,
    validate_query_plan_fixture,
)


CORPUS_FILE = Path("docs/eval-evidence/coc-country-shadow-normalizer-corpus-2026-07-01.json")


class CocCountryNormalizerEvidenceTests(unittest.TestCase):
    def test_seed_corpus_metrics_are_audit_only_and_not_promotion_ready(self):
        report = evaluate_coc_country_evidence_corpus(load_corpus(CORPUS_FILE))

        self.assertEqual(report["family"], "coc_country_match")
        self.assertFalse(report["llm_invoked"])
        self.assertFalse(report["live_dispatch"])
        self.assertFalse(report["promoted_family"])
        self.assertEqual(report["summary"]["total_cases"], 200)
        self.assertEqual(report["summary"]["class_counts"], {"A": 80, "B": 80, "C": 40})
        self.assertEqual(report["summary"]["schema_valid_rate"], 1.0)
        self.assertEqual(report["summary"]["unsafe_widening_count"], 0)
        self.assertEqual(report["summary"]["class_a_fixture_match_rate"], 1.0)
        self.assertEqual(report["summary"]["class_b_correct_rate_against_human_label"], 1.0)
        self.assertEqual(report["summary"]["class_b_deterministic_baseline_correct_rate"], 0.1125)
        self.assertEqual(report["summary"]["class_b_recall_lift"], 0.8875)
        self.assertEqual(report["summary"]["class_c_safe_route_rate"], 1.0)
        self.assertFalse(report["promotion_gate"]["passes"])
        self.assertEqual(report["promotion_gate"]["failures"], ["real_llm_run_required"])

    def test_corpus_includes_cross_family_prompts_by_class(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        for class_name in ("A", "B", "C"):
            availability_count = sum(
                1 for case in corpus["cases"] if case["class"] == class_name and "available" in case["prompt"]
            )
            vessel_tonnage_count = sum(
                1 for case in corpus["cases"] if case["class"] == class_name and "vessel tonnage" in case["prompt"]
            )
            self.assertGreaterEqual(availability_count, 3)
            self.assertGreaterEqual(vessel_tonnage_count, 3)

        self.assertEqual(cases["A078"]["llm_query_plan"]["constraints"][0]["filter_family"], "coc_country_match")
        self.assertEqual(cases["B079"]["llm_query_plan"]["constraints"][0]["filter_family"], "coc_country_match")
        self.assertFalse(cases["C038"]["llm_query_plan"]["constraints"])
        self.assertEqual(cases["C038"]["llm_query_plan"]["needs_review"][0]["candidate_families"], ["coc_country_match"])
        self.assertFalse(cases["C039"]["llm_query_plan"]["constraints"])
        self.assertEqual(cases["C039"]["llm_query_plan"]["needs_review"][0]["candidate_families"], ["coc_country_match"])

    def test_corpus_pins_contains_any_for_multi_country_or(self):
        corpus = load_corpus(CORPUS_FILE)
        case = next(item for item in corpus["cases"] if item["id"] == "B080")
        constraint = case["llm_query_plan"]["constraints"][0]

        self.assertIn("India or Panama", case["prompt"])
        self.assertEqual(constraint["parameters"]["countries"], ["india", "panama"])
        self.assertEqual(constraint["parameters"]["operator"], "contains_any")

    def test_corpus_pins_equals_for_single_country_strict_class_a(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        for case_id, country in (("A070", "usa"), ("A071", "india"), ("A072", "uk")):
            with self.subTest(case_id=case_id):
                constraint = cases[case_id]["llm_query_plan"]["constraints"][0]
                self.assertEqual(cases[case_id]["class"], "A")
                self.assertEqual(constraint["parameters"]["countries"], [country])
                self.assertEqual(constraint["parameters"]["operator"], "equals")

    def test_corpus_pins_ambiguous_shortcut_context_to_canonical_ids(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        self.assertIn("CoC in India", cases["B070"]["prompt"])
        self.assertEqual(cases["B070"]["llm_query_plan"]["constraints"][0]["parameters"]["countries"], ["india"])
        self.assertIn("CoC in the US", cases["B074"]["prompt"])
        self.assertEqual(cases["B074"]["llm_query_plan"]["constraints"][0]["parameters"]["countries"], ["usa"])

    def test_corpus_routes_issue_authority_phrasing_to_needs_review(self):
        corpus = load_corpus(CORPUS_FILE)
        authority_cases = [
            item
            for item in corpus["cases"]
            if item["class"] == "C" and item["llm_query_plan"]["needs_review"]
        ]

        prompts = " ".join(item["prompt"] for item in authority_cases)
        self.assertIn("Panama Maritime Authority", prompts)
        self.assertIn("MARINA Philippines", prompts)
        for item in authority_cases:
            self.assertFalse(item["llm_query_plan"]["constraints"])
            self.assertEqual(item["llm_query_plan"]["needs_review"][0]["candidate_families"], ["coc_country_match"])

    def test_corpus_preserves_full_coc_country_source_span(self):
        corpus = load_corpus(CORPUS_FILE)
        cases = {item["id"]: item for item in corpus["cases"]}

        self.assertEqual(
            cases["A078"]["llm_query_plan"]["constraints"][0]["source_span"]["text"],
            "Indian CoC",
        )
        self.assertEqual(
            cases["B078"]["llm_query_plan"]["constraints"][0]["source_span"]["text"],
            "CoC issued in India",
        )
        self.assertNotEqual(
            cases["A078"]["llm_query_plan"]["constraints"][0]["source_span"]["text"],
            "Indian",
        )
        self.assertNotEqual(
            cases["B078"]["llm_query_plan"]["constraints"][0]["source_span"]["text"],
            "India",
        )

    def test_query_plan_fixture_rejects_ambiguous_shortcut_country(self):
        corpus = load_corpus(CORPUS_FILE)
        case = next(item for item in corpus["cases"] if item["id"] == "A001")
        payload = json.loads(json.dumps(case["llm_query_plan"]))
        payload["constraints"][0]["parameters"]["countries"] = ["in"]

        result = validate_query_plan_fixture(payload, prompt_normalized=case["prompt"])

        self.assertFalse(result.accepted)
        self.assertTrue(any("canonical CoC country" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
