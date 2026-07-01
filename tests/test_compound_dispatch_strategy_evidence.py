import unittest

from scripts.compound_dispatch_strategy_evidence import (
    evaluate_dispatch_strategy,
    _merge_query_plans,
    _score_case,
)
from query_understanding.compound_prompt_normalizer_evidence import normalize_prompt_text
from query_understanding.compound_prompt_normalizer_provider import AvailabilityNormalizerProviderResult
from candidate_facts.aliases.filter_capability_catalog import load_filter_capability_catalog


class CompoundDispatchStrategyEvidenceTests(unittest.TestCase):
    def test_merge_query_plans_combines_per_family_payloads(self):
        results = {
            "availability": AvailabilityNormalizerProviderResult(
                model_id="fake",
                prompt_template_version="availability",
                raw_llm_output=None,
                parsed_payload={
                    "version": "v1",
                    "constraints": [{"filter_family": "availability"}],
                    "soft_signals": [],
                    "unapplied": [],
                    "needs_review": [],
                },
            ),
            "vessel_tonnage": AvailabilityNormalizerProviderResult(
                model_id="fake",
                prompt_template_version="vessel_tonnage",
                raw_llm_output=None,
                parsed_payload={
                    "version": "v1",
                    "constraints": [{"filter_family": "vessel_tonnage"}],
                    "soft_signals": [],
                    "unapplied": [],
                    "needs_review": [],
                },
            ),
            "coc_country_match": AvailabilityNormalizerProviderResult(
                model_id="fake",
                prompt_template_version="coc_country_match",
                raw_llm_output=None,
                parsed_payload={
                    "version": "v1",
                    "constraints": [{"filter_family": "coc_country_match"}],
                    "soft_signals": [],
                    "unapplied": [],
                    "needs_review": [],
                },
            ),
        }

        payload = _merge_query_plans(results)

        self.assertEqual(payload["version"], "v1")
        self.assertEqual(
            [item["filter_family"] for item in payload["constraints"]],
            ["availability", "coc_country_match", "vessel_tonnage"],
        )

    def test_score_case_checks_constraint_and_review_families(self):
        prompt = "Need Indian CoC candidates available by 1 Aug 2026 with vessels above 50000 GT."
        prompt_normalized = normalize_prompt_text(prompt)
        case = {
            "expected_constraint_families": ["availability", "coc_country_match", "vessel_tonnage"],
            "expected_review_families": [],
        }
        payload = {
            "version": "v1",
            "constraints": [
                {
                    "filter_family": "availability",
                    "parameters": {
                        "version": "v1",
                        "value_type": "by_date",
                        "status": None,
                        "available_by_date": "2026-08-01",
                        "available_from_date": None,
                        "available_until_date": None,
                        "relative_days": None,
                        "resolved_reference_date": "2026-07-01",
                        "display_value": "available by 1 Aug 2026",
                    },
                    "source_span": {
                        "text": "available by 1 Aug 2026",
                        "start": prompt_normalized.index("available by 1 Aug 2026"),
                        "end": prompt_normalized.index("available by 1 Aug 2026") + len("available by 1 Aug 2026"),
                    },
                },
                {
                    "filter_family": "vessel_tonnage",
                    "parameters": {
                        "version": "v1",
                        "value_type": "minimum",
                        "min_value": 50000,
                        "max_value": None,
                        "unit": "gt_grt",
                        "years_back": None,
                        "display_value": "vessels above 50000 GT",
                    },
                    "source_span": {
                        "text": "vessels above 50000 GT",
                        "start": prompt_normalized.index("vessels above 50000 GT"),
                        "end": prompt_normalized.index("vessels above 50000 GT") + len("vessels above 50000 GT"),
                    },
                },
                {
                    "filter_family": "coc_country_match",
                    "parameters": {
                        "version": "v1",
                        "type": "coc_country_match",
                        "countries": ["india"],
                        "operator": "contains_any",
                        "display_value": "Indian CoC",
                    },
                    "source_span": {
                        "text": "Indian CoC",
                        "start": prompt_normalized.index("Indian CoC"),
                        "end": prompt_normalized.index("Indian CoC") + len("Indian CoC"),
                    },
                },
            ],
            "soft_signals": [],
            "unapplied": [],
            "needs_review": [],
        }

        score = _score_case(case, payload, prompt_normalized=prompt_normalized, catalog=load_filter_capability_catalog())

        self.assertTrue(score["schema_valid"])
        self.assertTrue(score["constraint_family_match"])
        self.assertTrue(score["review_family_match"])
        self.assertEqual(score["missing_constraint_families"], [])
        self.assertEqual(score["unexpected_constraint_families"], [])

    def test_dry_run_evaluates_without_provider_credentials_and_records_latency_shape(self):
        corpus = {
            "corpus_id": "test-n3",
            "version": "1.0.0",
            "reference_date": "2026-07-01",
            "cases": [
                {
                    "id": "T001",
                    "class": "all_three",
                    "prompt": "Need Indian CoC candidates available now with vessels above 50000 GT.",
                    "expected_constraint_families": ["availability", "coc_country_match", "vessel_tonnage"],
                    "expected_review_families": [],
                },
                {
                    "id": "T002",
                    "class": "review",
                    "prompt": "Need Panama Maritime Authority with vessels above 50000 GT.",
                    "expected_constraint_families": ["vessel_tonnage"],
                    "expected_review_families": ["coc_country_match"],
                },
            ],
        }

        report = evaluate_dispatch_strategy(
            corpus,
            strategy="parallel_per_family",
            api_key="",
            model="dry-run-model",
            timeout=1,
            dry_run=True,
        )

        self.assertTrue(report["dry_run"])
        self.assertFalse(report["live_dispatch"])
        self.assertEqual(report["summary"]["total_cases"], 2)
        self.assertEqual(report["summary"]["schema_valid_rate"], 1.0)
        self.assertEqual(report["summary"]["unsafe_widening_count"], 0)
        self.assertEqual(report["summary"]["constraint_family_match_rate"], 1.0)
        self.assertEqual(report["summary"]["review_family_match_rate"], 1.0)
        self.assertGreaterEqual(report["summary"]["p50_elapsed_ms"], 0.0)
        self.assertEqual(report["summary"]["provider_calls_per_case"], 3)
        self.assertEqual(
            sorted(report["case_results"][0]["per_family_elapsed_ms"]),
            ["availability", "coc_country_match", "vessel_tonnage"],
        )
        self.assertIn("latency_reduction_min_relative", report["adoption_thresholds"])
        self.assertIn("latency_reduction_min_absolute_ms", report["adoption_thresholds"])


if __name__ == "__main__":
    unittest.main()
