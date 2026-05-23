import unittest
from datetime import datetime, timezone

from query_understanding.normalizer_compare import compare_query_plans


def _base_plan():
    return {
        "schema_version": "query_plan.v1",
        "normalizer": {
            "name": "legacy",
            "model": None,
            "prompt_template_version": "test.v1",
            "catalog_version": "query_understanding.catalog.v1",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
        "input": {
            "raw_prompt": "rank and sea service",
            "rank_context": None,
            "ui_filters": {
                "schema_version": "ui_filters.v1",
                "filters": [],
            },
        },
        "applied_constraints": [
            {
                "id": "rank_match",
                "mode": "required",
                "constraint": {"type": "rank_match", "rank": "2nd_engineer"},
                "source_text": "2nd engineer",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "rank",
                    "legacy_applied_constraint_id": "rank_match",
                },
            }
        ],
        "unapplied_constraints": [],
        "semantic_query": "",
        "unrecognized_residual": [],
        "warnings": [],
        "validation": {"status": "valid", "errors": []},
    }


class QueryUnderstandingCompareTests(unittest.TestCase):
    def test_compare_query_plans_classifies_equivalent(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()
        results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].comparison_outcome, "equivalent")
        self.assertEqual(results[0].family, "rank_match")

    def test_compare_query_plans_classifies_unsupported_family_delta(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()
        llm_plan["applied_constraints"] = [
            {
                "id": "min_sea_service",
                "mode": "required",
                "constraint": {"type": "min_sea_service", "minimum_months": 60},
                "source_text": "minimum 5 years sea service",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "sea_service",
                    "legacy_applied_constraint_id": None,
                },
            }
        ]
        llm_plan["applied_constraints"].append(
            {
                "id": "rank_match",
                "mode": "required",
                "constraint": {"type": "rank_match", "rank": "2nd_engineer"},
                "source_text": "2nd engineer",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "rank",
                    "legacy_applied_constraint_id": "rank_match",
                },
            }
        )
        results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-2")
        outcomes = {result.family: result.comparison_outcome for result in results}
        self.assertEqual(outcomes["rank_match"], "equivalent")
        self.assertEqual(outcomes["min_sea_service"], "unsupported_family_delta")

    def test_compare_query_plans_classifies_schema_error_before_unsupported_delta(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()
        llm_plan["applied_constraints"] = [
            {
                "id": "min_sea_service",
                "mode": "required",
                "constraint": {"type": "min_sea_service", "minimum_months": 60},
                "source_text": 123,
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "sea_service",
                    "legacy_applied_constraint_id": None,
                },
            }
        ]
        results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-3")
        self.assertEqual(results[0].comparison_outcome, "schema_error")


if __name__ == "__main__":
    unittest.main()
