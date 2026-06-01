import unittest
from datetime import datetime, timezone
from unittest import mock

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
        self.assertEqual(results[0].legacy_record.confidence, "high")
        self.assertEqual(results[0].llm_record.confidence, "high")

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

    def test_compare_query_plans_can_treat_expected_unsupported_families_as_expected_delta(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()

        legacy_normalized = _base_plan()
        llm_normalized = _base_plan()

        legacy_record = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-2a",
            family="min_sea_service",
            mode="required",
            normalized_payload={"type": "min_sea_service", "minimum_months": 60},
            source_text="minimum 5 years sea service",
            status="degraded",
        )
        llm_record = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-2a",
            family="min_sea_service",
            mode="required",
            normalized_payload={"type": "min_sea_service", "minimum_months": 60},
            source_text="minimum 5 years sea service",
            status="degraded",
        )
        rank_legacy = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-2a",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="valid",
        )
        rank_llm = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-2a",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="valid",
        )

        with mock.patch(
            "query_understanding.normalizer_compare.normalize_query_plan_v1",
            side_effect=[legacy_normalized, llm_normalized],
        ), mock.patch(
            "query_understanding.normalizer_compare.canonical_comparison_records",
            side_effect=[[legacy_record, rank_legacy], [llm_record, rank_llm]],
        ):
            results = compare_query_plans(
                legacy_plan,
                llm_plan,
                prompt_id="prompt-2a",
                expected_delta_families={"min_sea_service"},
            )

        outcomes = {result.family: result.comparison_outcome for result in results}
        self.assertEqual(outcomes["rank_match"], "equivalent")
        self.assertEqual(outcomes["min_sea_service"], "expected_delta")

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

    def test_compare_query_plans_classifies_legacy_missed_as_legacy_missed(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()

        legacy_normalized = _base_plan()
        llm_normalized = _base_plan()
        llm_normalized["applied_constraints"] = [
            {
                "id": "stcw_basic",
                "mode": "required",
                "constraint": {"type": "stcw_basic", "required": True},
                "source_text": "valid STCW basic safety",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "stcw_basic",
                    "legacy_applied_constraint_id": None,
                },
            },
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
            },
        ]

        legacy_rank = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-3a",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="valid",
        )
        llm_rank = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-3a",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="valid",
        )
        llm_stcw = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-3a",
            family="stcw_basic",
            mode="required",
            normalized_payload={"type": "stcw_basic", "required": True},
            source_text="valid STCW basic safety",
            status="applied",
        )

        with mock.patch(
            "query_understanding.normalizer_compare.normalize_query_plan_v1",
            side_effect=[legacy_normalized, llm_normalized],
        ), mock.patch(
            "query_understanding.normalizer_compare.canonical_comparison_records",
            side_effect=[[legacy_rank], [llm_stcw, llm_rank]],
        ):
            results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-3a")

        outcomes = {result.family: result.comparison_outcome for result in results}
        self.assertEqual(outcomes["rank_match"], "equivalent")
        self.assertEqual(outcomes["stcw_basic"], "legacy_missed")

    def test_compare_query_plans_treats_legacy_invalid_but_llm_valid_equivalent_rows_as_expected_delta(self):
        legacy_plan = _base_plan()
        legacy_plan["semantic_query"] = "2nd engineer with valid passport"
        legacy_plan["validation"] = {
            "status": "invalid",
            "errors": [
                {
                    "code": "mandatory_marker_in_semantic_query",
                    "path": "semantic_query",
                    "message": "mandatory fragments must not remain in semantic_query",
                }
            ],
        }

        llm_plan = _base_plan()
        llm_plan["semantic_query"] = ""
        llm_plan["validation"] = {"status": "valid", "errors": []}

        results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-4")
        self.assertEqual(results[0].comparison_outcome, "expected_delta")

    def test_compare_query_plans_keeps_non_mandatory_legacy_schema_failures_as_schema_error(self):
        legacy_plan = _base_plan()
        llm_plan = _base_plan()

        legacy_normalized = _base_plan()
        legacy_normalized["validation"] = {
            "status": "invalid",
            "errors": [
                {
                    "code": "catalogue_drift",
                    "path": "normalizer.catalog_version",
                    "message": "catalog version mismatch",
                }
            ],
        }

        llm_normalized = _base_plan()
        llm_normalized["validation"] = {"status": "valid", "errors": []}

        legacy_record = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-5",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="invalid",
        )
        llm_record = compare_query_plans.__globals__["CanonicalComparisonRecord"](
            catalog_snapshot_id="query_understanding.catalog.v1",
            prompt_id="prompt-5",
            family="rank_match",
            mode="required",
            normalized_payload={"type": "rank_match", "rank": "2nd_engineer"},
            source_text="2nd engineer",
            status="invalid",
        )

        with mock.patch(
            "query_understanding.normalizer_compare.normalize_query_plan_v1",
            side_effect=[legacy_normalized, llm_normalized],
        ), mock.patch(
            "query_understanding.normalizer_compare.canonical_comparison_records",
            side_effect=[[legacy_record], [llm_record]],
        ):
            results = compare_query_plans(legacy_plan, llm_plan, prompt_id="prompt-5")

        self.assertEqual(results[0].comparison_outcome, "schema_error")


if __name__ == "__main__":
    unittest.main()
