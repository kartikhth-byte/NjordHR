import unittest
from datetime import datetime, timezone

from query_understanding.schema import normalize_query_plan_v1, validate_query_plan_v1


def _valid_plan():
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
            "raw_prompt": "2nd engineer with strong leadership",
            "rank_context": "2nd engineer",
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
        "semantic_query": "strong leadership",
        "unrecognized_residual": [],
        "warnings": [],
        "validation": {"status": "valid", "errors": []},
    }


class QueryUnderstandingSchemaTests(unittest.TestCase):
    def test_valid_query_plan_passes_validation(self):
        plan = _valid_plan()
        validated = validate_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        self.assertEqual(validated["applied_constraints"][0]["constraint"]["rank"], "2nd_engineer")

    def test_valid_any_of_logical_group_passes_validation(self):
        plan = _valid_plan()
        plan["applied_constraints"] = []
        plan["logical_groups"] = [
            {
                "id": "vessel_engine_experience_any_of",
                "type": "any_of",
                "mode": "required",
                "source_text": "lng vessel or dual fuel vessel",
                "confidence": "high",
                "children": [
                    {
                        "id": "experience_ship_type",
                        "mode": "required",
                        "constraint": {
                            "type": "experience_ship_type",
                            "ship_family": "lng",
                            "minimum_months": None,
                        },
                        "source_text": "lng vessel",
                        "confidence": "high",
                        "compatibility": {
                            "legacy_hard_constraints_key": "experience_ship_type",
                            "legacy_applied_constraint_id": "experience_ship_type",
                        },
                    },
                    {
                        "id": "engine_experience",
                        "mode": "required",
                        "constraint": {
                            "type": "engine_experience",
                            "engine_family": "dual_fuel",
                            "minimum_months": None,
                            "recent_contract_count": None,
                        },
                        "source_text": "dual fuel vessel",
                        "confidence": "high",
                        "compatibility": {
                            "legacy_hard_constraints_key": "engine_experience",
                            "legacy_applied_constraint_id": "engine_experience",
                        },
                    },
                ],
            }
        ]

        validated = validate_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        self.assertEqual(validated["logical_groups"][0]["type"], "any_of")
        self.assertEqual(len(validated["logical_groups"][0]["children"]), 2)

    def test_missing_required_top_level_field_is_invalid(self):
        plan = _valid_plan()
        plan.pop("normalizer")
        validated = validate_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "invalid")
        self.assertTrue(
            any(error["code"] == "missing_required_top_level_fields" for error in validated["validation"]["errors"])
        )

    def test_mandatory_marker_fragments_cannot_be_semantic_only(self):
        cases = [
            "must have valid passport",
            "need passport and leadership",
            "must possess passport",
            "must present passport and leadership",
            "must carry passport and leadership",
            "must submit passport and leadership",
            "must show passport and leadership",
            "must display passport and leadership",
            "must produce passport and leadership",
            "must reveal passport and leadership",
            "must furnish passport and leadership",
            "must obtain passport and leadership",
        ]

        for semantic_query in cases:
            with self.subTest(semantic_query=semantic_query):
                plan = _valid_plan()
                plan["applied_constraints"] = []
                plan["semantic_query"] = semantic_query
                validated = normalize_query_plan_v1(plan)
                self.assertEqual(validated["validation"]["status"], "invalid")
                self.assertTrue(
                    any(error["code"] == "mandatory_marker_in_semantic_query" for error in validated["validation"]["errors"])
                )

    def test_semantic_text_with_common_words_is_not_false_positive(self):
        plan = _valid_plan()
        plan["applied_constraints"] = []
        plan["semantic_query"] = "strong leadership and valid communication with certificate focus"
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")

    def test_passport_validity_boolean_form_is_valid(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "passport_validity",
                "mode": "required",
                "constraint": {
                    "type": "passport_validity",
                    "must_be_valid": True,
                    "minimum_months_remaining": None,
                },
                "source_text": "valid passport",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "passport_validity",
                    "legacy_applied_constraint_id": "passport_validity",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        self.assertTrue(validated["applied_constraints"][0]["constraint"]["must_be_valid"])

    def test_vessel_tonnage_constraint_is_valid(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "vessel_tonnage",
                "mode": "required",
                "constraint": {
                    "type": "vessel_tonnage",
                    "min_value": 50000,
                    "max_value": 80000,
                    "unit": "gt_grt",
                },
                "source_text": "vessel tonnage between 50000 and 80000 GT",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "vessel_tonnage",
                    "legacy_applied_constraint_id": "vessel_tonnage",
                },
            }
        ]
        validated = validate_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        constraint = validated["applied_constraints"][0]["constraint"]
        self.assertEqual(constraint["min_value"], 50000)
        self.assertEqual(constraint["max_value"], 80000)
        self.assertEqual(constraint["unit"], "gt_grt")

    def test_invalid_vessel_tonnage_constraint_is_demoted(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "vessel_tonnage",
                "mode": "required",
                "constraint": {
                    "type": "vessel_tonnage",
                    "min_value": 90000,
                    "max_value": 50000,
                    "unit": "tons",
                },
                "source_text": "vessel tonnage between 90000 and 50000 tons",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "vessel_tonnage",
                    "legacy_applied_constraint_id": "vessel_tonnage",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertEqual(validated["applied_constraints"], [])
        self.assertEqual(validated["unapplied_constraints"][0]["id"], "vessel_tonnage")
        self.assertTrue(any(error["code"] == "invalid_vessel_tonnage_range" for error in validated["validation"]["errors"]))
        self.assertTrue(any(error["code"] == "invalid_vessel_tonnage_unit" for error in validated["validation"]["errors"]))

    def test_vessel_tonnage_constraint_requires_at_least_one_bound(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "vessel_tonnage",
                "mode": "required",
                "constraint": {
                    "type": "vessel_tonnage",
                    "min_value": None,
                    "max_value": None,
                    "unit": "any",
                },
                "source_text": "vessel tonnage",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "vessel_tonnage",
                    "legacy_applied_constraint_id": "vessel_tonnage",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertEqual(validated["applied_constraints"], [])
        self.assertTrue(any(error["code"] == "missing_vessel_tonnage_bound" for error in validated["validation"]["errors"]))

    def test_vessel_tonnage_constraint_rejects_non_positive_values(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "vessel_tonnage",
                "mode": "required",
                "constraint": {
                    "type": "vessel_tonnage",
                    "min_value": -1,
                    "max_value": None,
                    "unit": "any",
                },
                "source_text": "minimum -1 tonnage",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "vessel_tonnage",
                    "legacy_applied_constraint_id": "vessel_tonnage",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertEqual(validated["applied_constraints"], [])
        self.assertTrue(any(error["code"] == "invalid_min_value" for error in validated["validation"]["errors"]))

    def test_experience_ship_type_accepts_v1_items_and_dedupes(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "experience_ship_type",
                "mode": "required",
                "constraint": {
                    "type": "experience_ship_type",
                    "items": [
                        {"ship_family": "tanker", "minimum_months": 12, "years_back": 3, "contract_count": None},
                        {"ship_family": "tanker", "minimum_months": 12, "years_back": 3, "contract_count": None},
                        {"ship_family": "lng", "minimum_months": None, "years_back": None, "contract_count": 2},
                    ],
                },
                "source_text": "tanker in last 3 years or lng in last 2 contracts",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "experience_ship_type",
                    "legacy_applied_constraint_id": "experience_ship_type",
                },
            }
        ]

        validated = normalize_query_plan_v1(plan)

        self.assertEqual(validated["validation"]["status"], "degraded")
        constraint = validated["applied_constraints"][0]["constraint"]
        self.assertEqual(
            constraint["items"],
            [
                {"ship_family": "tanker", "minimum_months": 12, "years_back": 3, "contract_count": None},
                {"ship_family": "lng", "minimum_months": None, "years_back": None, "contract_count": 2},
            ],
        )
        self.assertTrue(any(warning["code"] == "duplicate_filter_rows" for warning in validated["warnings"]))

    def test_engine_experience_v1_item_rejects_ambiguous_recency(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "engine_experience",
                "mode": "required",
                "constraint": {
                    "type": "engine_experience",
                    "items": [
                        {"engine_family": "dual_fuel", "minimum_months": None, "years_back": 3, "contract_count": 2},
                    ],
                },
                "source_text": "dual fuel in last 3 years and last 2 contracts",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "engine_experience",
                    "legacy_applied_constraint_id": "engine_experience",
                },
            }
        ]

        validated = normalize_query_plan_v1(plan)

        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertEqual(validated["applied_constraints"], [])
        self.assertTrue(any(error["code"] == "engine_experience_recency_ambiguous" for error in validated["validation"]["errors"]))

    def test_vessel_tonnage_accepts_years_back_but_not_contract_count(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "vessel_tonnage",
                "mode": "required",
                "constraint": {
                    "type": "vessel_tonnage",
                    "min_value": 50000,
                    "max_value": None,
                    "unit": "any",
                    "years_back": 3,
                },
                "source_text": "above 50000 tonnage in last 3 years",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "vessel_tonnage",
                    "legacy_applied_constraint_id": "vessel_tonnage",
                },
            }
        ]

        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        self.assertEqual(validated["applied_constraints"][0]["constraint"]["years_back"], 3)

        plan["applied_constraints"][0]["constraint"]["contract_count"] = 2
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertTrue(
            any(error["code"] == "invalid_vessel_tonnage_contract_count" for error in validated["validation"]["errors"])
        )

    def test_us_visa_payload_preserves_visa_group_and_accepted_types(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "us_visa",
                "mode": "required",
                "constraint": {
                    "type": "us_visa",
                    "required": True,
                    "minimum_months_remaining": 6,
                    "visa_group": "usa",
                    "accepted_types": ["C1/D (USA)"],
                },
                "source_text": "valid US visa",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "us_visa",
                    "legacy_applied_constraint_id": "us_visa",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "valid")
        constraint = validated["applied_constraints"][0]["constraint"]
        self.assertEqual(constraint["visa_group"], "usa")
        self.assertEqual(constraint["accepted_types"], ["C1/D (USA)"])

    def test_us_visa_rejects_unsupported_visa_group(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
            {
                "id": "us_visa",
                "mode": "required",
                "constraint": {
                    "type": "us_visa",
                    "required": True,
                    "minimum_months_remaining": None,
                    "visa_group": "uk",
                    "accepted_types": None,
                },
                "source_text": "valid UK visa",
                "confidence": "high",
                "compatibility": {
                    "legacy_hard_constraints_key": "us_visa",
                    "legacy_applied_constraint_id": "us_visa",
                },
            }
        ]
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertTrue(
            any(error["code"] == "invalid_visa_group" for error in validated["validation"]["errors"])
        )

    def test_negated_requirement_phrase_is_not_mandatory(self):
        cases = [
            "passport not required for leadership",
            "passport no longer required for leadership",
            "passport no longer mandatory for leadership",
            "passport no longer needed for leadership",
        ]

        for semantic_query in cases:
            with self.subTest(semantic_query=semantic_query):
                plan = _valid_plan()
                plan["applied_constraints"] = []
                plan["semantic_query"] = semantic_query
                validated = normalize_query_plan_v1(plan)
                self.assertEqual(validated["validation"]["status"], "valid")

    def test_mixed_negated_and_positive_requirements_still_flag_mandatory_text(self):
        cases = [
            "passport no longer required but valid us visa required",
            "passport not required but must have valid us visa",
        ]

        for semantic_query in cases:
            with self.subTest(semantic_query=semantic_query):
                plan = _valid_plan()
                plan["applied_constraints"] = []
                plan["semantic_query"] = semantic_query
                validated = normalize_query_plan_v1(plan)
                self.assertEqual(validated["validation"]["status"], "invalid")
                self.assertTrue(
                    any(error["code"] == "mandatory_marker_in_semantic_query" for error in validated["validation"]["errors"])
                )

    def test_unsupported_active_looking_family_is_demoted_to_unapplied_constraints(self):
        plan = _valid_plan()
        plan["applied_constraints"] = [
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
        validated = normalize_query_plan_v1(plan)
        self.assertEqual(validated["validation"]["status"], "degraded")
        self.assertEqual(validated["applied_constraints"], [])
        self.assertEqual(validated["unapplied_constraints"][0]["id"], "min_sea_service")
        self.assertEqual(validated["unapplied_constraints"][0]["reason"], "unsupported_filter_family")


if __name__ == "__main__":
    unittest.main()
