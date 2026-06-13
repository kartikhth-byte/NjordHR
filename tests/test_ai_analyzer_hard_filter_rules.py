import sys
import types
import unittest
from datetime import date


def _stub_ai_dependencies():
    if "fitz" not in sys.modules:
        sys.modules["fitz"] = types.ModuleType("fitz")

    if "PIL" not in sys.modules:
        pil_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        pil_module.Image = image_module
        sys.modules["PIL"] = pil_module
        sys.modules["PIL.Image"] = image_module

    if "pinecone" not in sys.modules:
        pinecone_module = types.ModuleType("pinecone")

        class DummyPinecone:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyServerlessSpec:
            def __init__(self, *_args, **_kwargs):
                pass

        pinecone_module.Pinecone = DummyPinecone
        pinecone_module.ServerlessSpec = DummyServerlessSpec
        sys.modules["pinecone"] = pinecone_module


_stub_ai_dependencies()
from ai_analyzer import AIResumeAnalyzer  # noqa: E402


class AIAnalyzerHardFilterRuleTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_rank_match_rule_pass(self):
        result = self.analyzer._evaluate_rank_rule(
            {
                "role": {"applied_rank_normalized": "2nd_engineer"},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            },
            {"applied_rank_normalized": ["2nd_engineer"]},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_rank_match_rule_fail(self):
        result = self.analyzer._evaluate_rank_rule(
            {
                "role": {"applied_rank_normalized": "chief_officer"},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            },
            {"applied_rank_normalized": ["2nd_engineer"]},
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_rank_match_rule_unknown(self):
        result = self.analyzer._evaluate_rank_rule(
            {
                "role": {"applied_rank_normalized": None},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": None}},
            },
            {"applied_rank_normalized": ["2nd_engineer"]},
        )
        self.assertEqual(result["decision"], "UNKNOWN")

    def test_coc_document_gate_valid(self):
        result = self.analyzer._evaluate_coc_document_gate(
            {
                "certifications": {
                    "coc": {
                        "grade": "2nd_engineer",
                        "expiry_date": "2028-05-04",
                        "expiry_status": "PARSED",
                        "status": "PARSED",
                    }
                },
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"coc_required": True, "coc_valid_required": True},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_coc_document_gate_expired(self):
        result = self.analyzer._evaluate_coc_document_gate(
            {
                "certifications": {
                    "coc": {
                        "grade": "2nd_engineer",
                        "expiry_date": "2020-05-04",
                        "expiry_status": "PARSED",
                        "status": "PARSED",
                    }
                },
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"coc_required": True, "coc_valid_required": True},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_coc_document_gate_absent(self):
        result = self.analyzer._evaluate_coc_document_gate(
            {
                "certifications": {
                    "coc": {
                        "grade": None,
                        "expiry_date": None,
                        "expiry_status": "MISSING",
                        "status": "MISSING",
                    }
                },
                "fact_meta": {"certifications.coc": {"confidence": None}},
            },
            {"coc_required": True, "coc_valid_required": True},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_coc_document_gate_low_confidence_is_factual_unknown(self):
        result = self.analyzer._evaluate_coc_document_gate(
            {
                "certifications": {
                    "coc": {
                        "grade": "2nd_engineer",
                        "expiry_date": "2028-05-04",
                        "expiry_status": "PARSED",
                        "status": "PARSED",
                    }
                },
                "fact_meta": {"certifications.coc": {"confidence": 0.7}},
            },
            {"coc_required": True, "coc_valid_required": True},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_coc_grade_rule_pass(self):
        result = self.analyzer._evaluate_coc_grade_rule(
            {
                "certifications": {"coc": {"grade": "chief_officer"}},
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"required_grades": ["chief_officer"]},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_coc_grade_rule_fail(self):
        result = self.analyzer._evaluate_coc_grade_rule(
            {
                "certifications": {"coc": {"grade": "2nd_officer"}},
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"required_grades": ["chief_officer"]},
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_coc_grade_rule_missing_is_unknown(self):
        result = self.analyzer._evaluate_coc_grade_rule(
            {
                "certifications": {"coc": {"grade": None}},
                "fact_meta": {"certifications.coc": {"confidence": None}},
            },
            {"required_grades": ["chief_officer"]},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_coc_country_rule_pass(self):
        result = self.analyzer._evaluate_coc_country_rule(
            {
                "certifications": {"coc": {"country": "india"}},
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"countries": ["india"], "operator": "contains_any"},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_coc_country_rule_fail(self):
        result = self.analyzer._evaluate_coc_country_rule(
            {
                "certifications": {"coc": {"country": "uk"}},
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {"countries": ["india"], "operator": "contains_any"},
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_coc_country_rule_missing_is_unknown(self):
        result = self.analyzer._evaluate_coc_country_rule(
            {
                "certifications": {"coc": {"grade": "2nd_engineer", "country": None}},
                "fact_meta": {"certifications.coc": {"confidence": None}},
            },
            {"countries": ["india"], "operator": "contains_any"},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_rule_skipped_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "role": {"applied_rank_normalized": "chief_officer"},
                "certifications": {
                    "coc": {
                        "grade": "2nd_engineer",
                        "expiry_date": "2020-05-04",
                        "expiry_status": "PARSED",
                        "status": "PARSED",
                    },
                    "stcw_basic_all_valid": False,
                },
                "fact_meta": {
                    "role.applied_rank_normalized": {"confidence": 1.0},
                    "certifications.coc": {"confidence": 0.9},
                    "certifications.stcw_basic_all_valid": {"confidence": 0.9},
                },
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "rank": {"applied_rank_normalized": ["2nd_engineer"]},
                    "certifications": {"coc_required": True, "coc_valid_required": True},
                    "stcw_basic": {"required": True},
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_hard_filter_skips_age_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "personal": {
                    "dob": date(1971, 1, 4),
                    "dob_parse_status": "PARSED",
                },
                "derived": {
                    "age_years": 55,
                },
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "age_years": {"min_age": 30, "max_age": 50},
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_hard_filter_skips_visa_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "travel": {
                    "us_visa_status": "PARSED",
                    "visa_records": [{
                        "status": "PARSED",
                        "visa_type": "US Visa (USA)",
                        "visa_group": "usa",
                        "expiry_date": date(2019, 6, 22),
                        "expiry_status": "PARSED",
                    }],
                    "visa_types": ["US Visa (USA)"],
                }
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "us_visa": {
                        "required": True,
                        "must_be_valid": True,
                        "accepted_types": ["US Visa (USA)"],
                        "visa_group": "usa",
                        "requested_label": "valid US visa",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_passport_validity_rule_pass(self):
        result = self.analyzer._evaluate_passport_validity_rule(
            {
                "logistics": {
                    "passport_expiry_date": "2028-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {"required": True, "must_be_valid": True, "requested_label": "valid passport"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_passport_validity_rule_fail_when_expired(self):
        result = self.analyzer._evaluate_passport_validity_rule(
            {
                "logistics": {
                    "passport_expiry_date": "2020-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {"required": True, "must_be_valid": True, "requested_label": "valid passport"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_passport_validity_rule_passes_when_remaining_window_is_met(self):
        result = self.analyzer._evaluate_passport_validity_rule(
            {
                "logistics": {
                    "passport_expiry_date": "2028-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {
                "required": True,
                "must_be_valid": True,
                "minimum_months_remaining": 18,
                "requested_label": "passport valid for at least 18 months",
            },
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_passport_validity_rule_fails_when_remaining_window_is_too_short(self):
        result = self.analyzer._evaluate_passport_validity_rule(
            {
                "logistics": {
                    "passport_expiry_date": "2027-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {
                "required": True,
                "must_be_valid": True,
                "minimum_months_remaining": 18,
                "requested_label": "passport valid for at least 18 months",
            },
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["reason_code"], "PASSPORT_VALIDITY_WINDOW_TOO_SHORT")

    def test_passport_validity_rule_missing_is_unknown(self):
        result = self.analyzer._evaluate_passport_validity_rule(
            {
                "logistics": {
                    "passport_expiry_date": None,
                    "passport_expiry_status": "MISSING",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": None}},
            },
            {"required": True, "must_be_valid": True, "requested_label": "valid passport"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_recent_contract_vessel_rule_passes_when_recent_rows_meet_month_threshold(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2024, 11, 3),
                            "sign_out_date": date(2025, 4, 13),
                            "vessel_types": ["container"],
                        },
                        {
                            "sign_in_date": date(2024, 3, 3),
                            "sign_out_date": date(2024, 11, 6),
                            "vessel_types": ["container vessel"],
                        },
                        {
                            "sign_in_date": date(2023, 12, 6),
                            "sign_out_date": date(2024, 2, 27),
                            "vessel_types": ["bulk carrier"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "container",
                "min_months": 12,
                "lookback_contracts": 3,
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_MATCH")

    def test_recent_contract_vessel_rule_fails_when_recent_rows_are_short(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 4, 17),
                            "sign_out_date": date(2025, 6, 20),
                            "vessel_types": ["container"],
                        },
                        {
                            "sign_in_date": date(2024, 7, 26),
                            "sign_out_date": date(2025, 1, 25),
                            "vessel_types": ["oil tanker"],
                        },
                        {
                            "sign_in_date": date(2024, 3, 3),
                            "sign_out_date": date(2024, 6, 6),
                            "vessel_types": ["container vessel"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "container",
                "min_months": 12,
                "lookback_contracts": 3,
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_INSUFFICIENT")

    def test_recent_contract_vessel_rule_passes_no_duration_when_recent_contract_matches(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 4, 17),
                            "sign_out_date": date(2025, 6, 20),
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2024, 7, 26),
                            "sign_out_date": date(2025, 1, 25),
                            "vessel_types": ["container vessel"],
                        },
                        {
                            "sign_in_date": date(2024, 3, 3),
                            "sign_out_date": date(2024, 6, 6),
                            "vessel_types": ["oil tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "container",
                "min_months": 0,
                "lookback_contracts": 3,
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_MATCH")
        self.assertEqual(result["actual_value"]["matched_contracts"], 1)

    def test_recent_contract_vessel_rule_fails_no_duration_when_recent_contracts_do_not_match(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 4, 17),
                            "sign_out_date": date(2025, 6, 20),
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2024, 7, 26),
                            "sign_out_date": date(2025, 1, 25),
                            "vessel_types": ["oil tanker"],
                        },
                        {
                            "sign_in_date": date(2024, 3, 3),
                            "sign_out_date": date(2024, 6, 6),
                            "vessel_types": ["chemical tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "container",
                "min_months": 0,
                "lookback_contracts": 3,
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_INSUFFICIENT")

    def test_recent_contract_vessel_rule_fails_available_rows_when_window_is_short(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2024, 11, 20),
                            "sign_out_date": date(2025, 4, 16),
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2024, 2, 12),
                            "sign_out_date": date(2024, 6, 19),
                            "vessel_types": ["dry cargo"],
                        },
                        {
                            "sign_in_date": date(2023, 5, 18),
                            "sign_out_date": date(2023, 10, 16),
                            "vessel_types": ["dry cargo"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "tanker",
                "min_months": 18,
                "lookback_contracts": 4,
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_INSUFFICIENT")
        self.assertEqual(result["actual_value"]["matched_contracts"], 0)
        self.assertEqual(result["actual_value"]["evaluated_contracts"], 3)

    def test_recent_contract_vessel_rule_is_unknown_when_recent_rows_lack_ship_types(self):
        result = self.analyzer._evaluate_recent_contract_vessel_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 4, 17),
                            "sign_out_date": date(2025, 6, 20),
                            "vessel_types": [],
                        },
                        {
                            "sign_in_date": date(2024, 7, 26),
                            "sign_out_date": date(2025, 1, 25),
                            "vessel_types": [],
                        },
                        {
                            "sign_in_date": date(2024, 3, 3),
                            "sign_out_date": date(2024, 6, 6),
                            "vessel_types": [],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "vessel_type": "container",
                "min_months": 12,
                "lookback_contracts": 3,
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["reason_code"], "RECENT_CONTRACT_VESSEL_UNPARSED")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_rank_duration_rule_uses_seajobs_total_experience_rows(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "rank_duration_rows": [
                        {"rank_normalized": "chief_officer", "months_total": 54},
                    ],
                },
                "fact_meta": {"experience.rank_duration_rows": {"status": "PARSED", "confidence": 0.95}},
            },
            {
                "applied_constraints": ["rank_duration_experience"],
                "hard_constraints": {
                    "rank_duration_experience": {"rank_normalized": "chief_officer", "min_months": 48},
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "RANK_DURATION_MATCH")
        self.assertEqual(result["results"][0]["actual_value"]["source"], "rank_duration_rows")

    def test_rank_duration_rule_fails_short_seajobs_total_experience_row(self):
        result = self.analyzer._evaluate_rank_duration_experience_rule(
            {
                "experience": {
                    "rank_duration_rows": [
                        {"rank_normalized": "2nd_engineer", "months_total": 17},
                    ],
                },
                "fact_meta": {"experience.rank_duration_rows": {"status": "PARSED", "confidence": 0.95}},
            },
            {"rank_normalized": "2nd_engineer", "min_months": 24},
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["reason_code"], "RANK_DURATION_INSUFFICIENT")

    def test_rank_duration_rule_falls_back_to_non_seajobs_service_rows(self):
        result = self.analyzer._evaluate_rank_duration_experience_rule(
            {
                "experience": {
                    "service_rows": [
                        {
                            "rank_normalized": "3rd_engineer",
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 8, 28),
                        },
                        {
                            "rank_normalized": "3rd_engineer",
                            "sign_in_date": date(2023, 1, 1),
                            "sign_out_date": date(2023, 6, 29),
                        },
                        {
                            "rank_normalized": "4th_engineer",
                            "sign_in_date": date(2022, 1, 1),
                            "sign_out_date": date(2022, 11, 1),
                        },
                    ],
                },
                "fact_meta": {
                    "experience.rank_duration_rows": {"status": "SOURCE_EXCLUDED", "confidence": None},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {"rank_normalized": "3rd_engineer", "min_months": 12},
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["reason_code"], "RANK_DURATION_MATCH")
        self.assertEqual(result["actual_value"]["source"], "service_rows")
        self.assertEqual(result["actual_value"]["matched_rows"], 2)

    def test_rank_duration_rule_is_unknown_without_rank_or_service_rows(self):
        result = self.analyzer._evaluate_rank_duration_experience_rule(
            {
                "experience": {},
                "fact_meta": {
                    "experience.rank_duration_rows": {"status": "MISSING", "confidence": None},
                    "experience.service_rows": {"status": "SOURCE_EXCLUDED", "confidence": None},
                },
            },
            {"rank_normalized": "chief_officer", "min_months": 48},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["reason_code"], "RANK_DURATION_UNPARSED")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_rank_duration_rule_marks_v1_1_facts_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {"facts_version": "1.1"},
            {
                "applied_constraints": ["rank_duration_experience"],
                "hard_constraints": {
                    "rank_duration_experience": {"rank_normalized": "chief_officer", "min_months": 48},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "RANK_DURATION_RULE_REQUIRES_V2_FACTS")

    def test_engine_experience_rule_matches_expected_family(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {"engine_types": ["man_b_w_me_gi"]},
                "fact_meta": {"experience.engine_types": {"confidence": 0.8}},
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "dual_fuel",
                        "expected_values": self.analyzer._engine_type_expected_values("dual_fuel"),
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_MATCH")

    def test_engine_experience_rule_fails_when_family_missing(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {"engine_types": ["mitsubishi_uec"]},
                "fact_meta": {"experience.engine_types": {"confidence": 0.8}},
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "man_b_w_me",
                        "expected_values": self.analyzer._engine_type_expected_values("man_b_w_me"),
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_MISMATCH")

    def test_engine_experience_rule_fails_when_parsed_service_rows_have_no_match(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "engine_types": [],
                    "service_rows": [
                        {"engine_types": [], "snippet": "Chief Engineer / Product Tanker 7463 Wartsila"},
                        {"engine_types": [], "snippet": "Chief Engineer / AHTS 489 Mak"},
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": None},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "man_b_w_me",
                        "expected_values": self.analyzer._engine_type_expected_values("man_b_w_me"),
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_MISMATCH")

    def test_vessel_tonnage_rule_passes_when_row_value_matches_minimum(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "row_index": 1,
                            "vessel_name": "MT Aurora",
                            "vessel_tonnage": [
                                {"value": 58000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "Tonnage: 58000"}
                            ],
                        }
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_MATCH")
        self.assertEqual(result["results"][0]["actual_value"]["matched_evidence"][0]["value"], 58000)

    def test_vessel_tonnage_rule_prefers_contract_evidence_without_duplicates(self):
        tonnage_entry = {"value": 58000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "Tonnage: 58000"}
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"row_index": 1, "vessel_name": "MT Aurora", "vessel_tonnage": [tonnage_entry]}
                    ],
                },
                "contracts": [
                    {"contract_order": 1, "vessel_name": "MT Aurora", "vessel_tonnage": [tonnage_entry]}
                ],
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "any"}},
            },
        )
        matched = result["results"][0]["actual_value"]["matched_evidence"]
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["source"], "contracts")

    def test_vessel_tonnage_rule_fails_below_minimum(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_tonnage": [{"value": 28000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "Tonnage: 28000"}]}
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_BELOW_MINIMUM")

    def test_vessel_tonnage_rule_fails_above_maximum(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_tonnage": [{"value": 105000, "unit": "dwt", "confidence": 0.9, "evidence_text": "105000 DWT"}]}
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": None, "max_value": 80000, "unit": "dwt"}},
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_ABOVE_MAXIMUM")

    def test_vessel_tonnage_rule_fails_out_of_range_with_mixed_evidence(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_tonnage": [{"value": 25000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "25000"}]},
                        {"vessel_tonnage": [{"value": 90000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "90000"}]},
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 30000, "max_value": 80000, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_OUT_OF_RANGE")

    def test_vessel_tonnage_rule_unknown_without_evidence(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {"service_rows": [{"vessel_name": "MT Aurora"}]},
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_NOT_FOUND")

    def test_vessel_tonnage_rule_unknown_for_invalid_constraint(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_tonnage": [{"value": 58000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "Tonnage: 58000"}]}
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": None, "max_value": None, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_CONSTRAINT_INVALID")

    def test_vessel_tonnage_rule_marks_v1_1_facts_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {"facts_version": "1.1"},
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "any"}},
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_RULE_REQUIRES_V2_FACTS")

    def test_vessel_tonnage_rule_honors_unit_policy(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_tonnage": [{"value": 58000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "Tonnage: 58000"}]}
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 50000, "max_value": None, "unit": "dwt"}},
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_UNIT_NOT_FOUND")

    def test_vessel_tonnage_rule_honors_years_back_window(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2021, 1, 1),
                            "sign_out_date": date(2021, 6, 1),
                            "vessel_tonnage": [{"value": 80000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "80000"}],
                        },
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "vessel_tonnage": [{"value": 52000, "unit": "unspecified", "confidence": 0.9, "evidence_text": "52000"}],
                        },
                    ],
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["vessel_tonnage"],
                "hard_constraints": {"vessel_tonnage": {"min_value": 75000, "max_value": None, "unit": "any", "years_back": 2}},
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "VESSEL_TONNAGE_BELOW_MINIMUM")
        self.assertEqual(len(result["results"][0]["actual_value"]["evidence"]), 1)

    def test_experience_ship_type_items_pass_when_any_item_matches(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "vessel_types": ["lng carrier"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.vessel_types": {"confidence": 0.9},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["experience_ship_type"],
                "hard_constraints": {
                    "experience_ship_type": {
                        "type": "experience_ship_type",
                        "items": [
                            {"ship_family": "tanker", "minimum_months": None, "years_back": 2, "contract_count": None},
                            {"ship_family": "lng", "minimum_months": None, "years_back": 2, "contract_count": None},
                        ],
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_MATCH")

    def test_experience_ship_type_contract_count_with_undated_row_needs_review(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {"vessel_types": ["oil tanker"]},
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "vessel_types": ["bulk carrier"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.vessel_types": {"confidence": 0.9},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["experience_ship_type"],
                "hard_constraints": {
                    "experience_ship_type": {
                        "type": "experience_ship_type",
                        "items": [
                            {"ship_family": "tanker", "minimum_months": None, "years_back": None, "contract_count": 1},
                        ],
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_UNKNOWN")
        self.assertEqual(result["results"][0]["actual_value"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_NEEDS_DATE_REVIEW")

    def test_engine_experience_items_support_contract_count(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 7, 1),
                            "sign_out_date": date(2025, 12, 1),
                            "engine_types": ["dual_fuel"],
                        },
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 6, 1),
                            "engine_types": ["mitsubishi_uec"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": 0.9},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "type": "engine_experience",
                        "items": [
                            {"engine_family": "dual_fuel", "minimum_months": None, "years_back": None, "contract_count": 1},
                        ],
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_MATCH")

    def test_engine_experience_rule_honors_recent_contract_window(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "engine_types": ["mitsubishi_uec"],
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 5, 1),
                            "engine_types": ["man_b_w_me"],
                        },
                        {
                            "sign_in_date": date(2024, 6, 1),
                            "sign_out_date": date(2024, 12, 1),
                            "engine_types": ["wartsila_rt_flex"],
                        },
                        {
                            "sign_in_date": date(2023, 10, 1),
                            "sign_out_date": date(2024, 3, 1),
                            "engine_types": ["man_b_w_me"],
                        },
                        {
                            "sign_in_date": date(2022, 1, 1),
                            "sign_out_date": date(2022, 7, 1),
                            "engine_types": ["mitsubishi_uec"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": 0.8},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_values": self.analyzer._engine_type_expected_values("mitsubishi_uec"),
                        "min_months": 0,
                        "lookback_contracts": 3,
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_INSUFFICIENT")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 0)
        self.assertEqual(result["results"][0]["actual_value"]["evaluated_contracts"], 3)

    def test_engine_experience_rule_requires_all_recent_contracts_when_requested(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "engine_types": ["man_b_w_me", "mitsubishi_uec"],
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 7, 21),
                            "sign_out_date": date(2025, 11, 17),
                            "engine_types": ["man_b_w_me"],
                        },
                        {
                            "sign_in_date": date(2025, 2, 17),
                            "sign_out_date": date(2025, 6, 4),
                            "engine_types": ["man_b_w_me"],
                        },
                        {
                            "sign_in_date": date(2024, 12, 31),
                            "sign_out_date": date(2025, 1, 31),
                            "engine_types": ["mitsubishi_uec"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": 0.8},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_values": self.analyzer._engine_type_expected_values("mitsubishi_uec"),
                        "min_months": 0,
                        "lookback_contracts": 3,
                        "recent_contract_match_mode": "all",
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_INSUFFICIENT")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 1)
        self.assertEqual(result["results"][0]["actual_value"]["required_contracts"], 3)

    def test_engine_experience_rule_passes_when_all_recent_contracts_match(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "engine_types": ["mitsubishi_uec"],
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 7, 21),
                            "sign_out_date": date(2025, 11, 17),
                            "engine_types": ["mitsubishi_uec"],
                        },
                        {
                            "sign_in_date": date(2025, 2, 17),
                            "sign_out_date": date(2025, 6, 4),
                            "engine_types": ["mitsubishi_uec"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": 0.8},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_values": self.analyzer._engine_type_expected_values("mitsubishi_uec"),
                        "min_months": 0,
                        "lookback_contracts": 2,
                        "recent_contract_match_mode": "all",
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 2)
        self.assertEqual(result["results"][0]["actual_value"]["required_contracts"], 2)

    def test_engine_experience_rule_honors_minimum_months(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "engine_types": ["mitsubishi_uec"],
                    "service_rows": [
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 7, 1),
                            "engine_types": ["mitsubishi_uec"],
                        },
                        {
                            "sign_in_date": date(2023, 1, 1),
                            "sign_out_date": date(2023, 4, 1),
                            "engine_types": ["mitsubishi_uec"],
                        },
                    ],
                },
                "fact_meta": {
                    "experience.engine_types": {"confidence": 0.8},
                    "experience.service_rows": {"status": "PARSED", "confidence": 0.9},
                },
            },
            {
                "applied_constraints": ["engine_experience"],
                "hard_constraints": {
                    "engine_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_values": self.analyzer._engine_type_expected_values("mitsubishi_uec"),
                        "min_months": 12,
                        "lookback_contracts": 0,
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_INSUFFICIENT")
        self.assertEqual(result["results"][0]["actual_value"]["matched_months"], 9)

    def test_engine_vessel_experience_rule_requires_same_service_row(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "engine_types": ["mitsubishi_uec"],
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["oil tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["engine_vessel_experience"],
                "hard_constraints": {
                    "engine_vessel_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_engine_values": ["mitsubishi_uec"],
                        "vessel_type": "tanker",
                        "expected_vessel_values": self.analyzer._ship_type_expected_values("tanker"),
                        "min_months": 0,
                        "lookback_contracts": 0,
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_VESSEL_EXPERIENCE_INSUFFICIENT")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 0)

    def test_engine_vessel_experience_rule_passes_when_same_row_matches(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "engine_types": ["mitsubishi_uec"],
                            "vessel_types": ["product tanker"],
                        }
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["engine_vessel_experience"],
                "hard_constraints": {
                    "engine_vessel_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_engine_values": ["mitsubishi_uec"],
                        "vessel_type": "tanker",
                        "expected_vessel_values": self.analyzer._ship_type_expected_values("tanker"),
                        "min_months": 0,
                        "lookback_contracts": 0,
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_VESSEL_EXPERIENCE_MATCH")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 1)

    def test_engine_vessel_experience_rule_honors_recent_window(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["product tanker"],
                        },
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 6, 1),
                            "engine_types": ["mitsubishi_uec"],
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2023, 1, 1),
                            "sign_out_date": date(2023, 6, 1),
                            "engine_types": ["mitsubishi_uec"],
                            "vessel_types": ["product tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["engine_vessel_experience"],
                "hard_constraints": {
                    "engine_vessel_experience": {
                        "engine_type": "mitsubishi_uec",
                        "expected_engine_values": ["mitsubishi_uec"],
                        "vessel_type": "tanker",
                        "expected_vessel_values": self.analyzer._ship_type_expected_values("tanker"),
                        "min_months": 0,
                        "lookback_contracts": 2,
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["actual_value"]["evaluated_contracts"], 2)

    def test_engine_vessel_experience_rule_requires_all_recent_rows_when_requested(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["oil tanker"],
                        },
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["bulk carrier"],
                        },
                        {
                            "sign_in_date": date(2023, 1, 1),
                            "sign_out_date": date(2023, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["product tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["engine_vessel_experience"],
                "hard_constraints": {
                    "engine_vessel_experience": {
                        "engine_type": "man_b_w_me",
                        "expected_engine_values": self.analyzer._engine_type_expected_values("man_b_w_me"),
                        "vessel_type": "oil tanker",
                        "expected_vessel_values": self.analyzer._ship_type_expected_values("oil tanker"),
                        "min_months": 0,
                        "lookback_contracts": 3,
                        "recent_contract_match_mode": "all",
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_VESSEL_EXPERIENCE_INSUFFICIENT")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 2)
        self.assertEqual(result["results"][0]["actual_value"]["required_contracts"], 3)

    def test_engine_vessel_experience_rule_passes_when_all_recent_rows_match(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {
                    "service_rows": [
                        {
                            "sign_in_date": date(2025, 1, 1),
                            "sign_out_date": date(2025, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["oil tanker"],
                        },
                        {
                            "sign_in_date": date(2024, 1, 1),
                            "sign_out_date": date(2024, 6, 1),
                            "engine_types": ["man_b_w_me"],
                            "vessel_types": ["product tanker"],
                        },
                    ]
                },
                "fact_meta": {"experience.service_rows": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["engine_vessel_experience"],
                "hard_constraints": {
                    "engine_vessel_experience": {
                        "engine_type": "man_b_w_me",
                        "expected_engine_values": self.analyzer._engine_type_expected_values("man_b_w_me"),
                        "vessel_type": "oil tanker",
                        "expected_vessel_values": self.analyzer._ship_type_expected_values("oil tanker"),
                        "min_months": 0,
                        "lookback_contracts": 2,
                        "recent_contract_match_mode": "all",
                    }
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["actual_value"]["matched_contracts"], 2)
        self.assertEqual(result["results"][0]["actual_value"]["required_contracts"], 2)

    def test_hard_filter_skips_passport_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "logistics": {
                    "passport_expiry_date": "2028-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "passport_validity": {
                        "required": True,
                        "must_be_valid": True,
                        "requested_label": "valid passport",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_availability_rule_pass_for_immediate_window(self):
        result = self.analyzer._evaluate_availability_rule(
            {
                "logistics": {
                    "availability_date": "2026-03-30",
                    "availability_end_date": "2026-04-30",
                    "availability_status": "immediately",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.9}},
            },
            {"value_type": "status", "status": "immediately", "display_value": "available immediately"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_availability_rule_fail_when_not_immediate(self):
        result = self.analyzer._evaluate_availability_rule(
            {
                "logistics": {
                    "availability_date": "2026-05-15",
                    "availability_end_date": "2026-06-15",
                    "availability_status": "PARSED",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.9}},
            },
            {"value_type": "status", "status": "immediately", "display_value": "available immediately"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_availability_rule_pass_for_requested_date_inside_window(self):
        result = self.analyzer._evaluate_availability_rule(
            {
                "logistics": {
                    "availability_date": "2026-05-15",
                    "availability_end_date": "2026-06-15",
                    "availability_status": "PARSED",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.9}},
            },
            {"value_type": "date", "available_from_date": "2026-06-01", "display_value": "available from June 1"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_availability_rule_pass_for_immediate_single_date_window(self):
        result = self.analyzer._evaluate_availability_rule(
            {
                "logistics": {
                    "availability_date": "2026-03-30",
                    "availability_end_date": None,
                    "availability_status": "immediately",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.85}},
            },
            {"value_type": "status", "status": "immediately", "display_value": "available immediately"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_availability_rule_unknown_when_missing(self):
        result = self.analyzer._evaluate_availability_rule(
            {
                "logistics": {
                    "availability_date": None,
                    "availability_end_date": None,
                    "availability_status": "MISSING",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": None}},
            },
            {"value_type": "status", "status": "immediately", "display_value": "available immediately"},
            reference_date=date(2026, 4, 6),
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_hard_filter_skips_availability_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "logistics": {
                    "availability_date": "2026-03-30",
                    "availability_end_date": "2026-04-30",
                    "availability_status": "immediately",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.9}},
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "availability": {
                        "value_type": "status",
                        "status": "immediately",
                        "display_value": "available immediately",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_hard_filter_skips_applied_ship_type_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "application": {"applied_ship_types": ["bulk carrier"]},
                "fact_meta": {"application.applied_ship_types": {"confidence": 1.0}},
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "applied_ship_type": "Bulk Carrier",
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_hard_filter_skips_experience_ship_type_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "experience": {"vessel_types": ["tanker"]},
                "fact_meta": {"experience.vessel_types": {"confidence": 0.8}},
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "experience_ship_type": "Tanker",
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_v1_record_on_active_coc_rule_is_version_mismatch_unknown_even_without_coc_required_flag(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "certifications": {
                    "coc": {
                        "grade": None,
                        "expiry_date": None,
                        "expiry_status": "MISSING",
                        "status": "MISSING",
                    }
                },
                "fact_meta": {"certifications.coc": {"confidence": None}},
            },
            {
                "applied_constraints": ["coc_document_gate"],
                "hard_constraints": {
                    "certifications": {"coc_required": False, "coc_valid_required": True},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_v1_record_on_active_coc_grade_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "certifications": {"coc": {"grade": "chief_officer"}},
                "fact_meta": {"certifications.coc": {"confidence": 0.9}},
            },
            {
                "applied_constraints": ["coc_grade_match"],
                "hard_constraints": {
                    "coc_grade": {"required_grades": ["chief_officer"]},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_v1_record_on_active_rank_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "role": {"applied_rank_normalized": "2nd_engineer"},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            },
            {
                "applied_constraints": ["rank_match"],
                "hard_constraints": {
                    "rank": {"applied_rank_normalized": ["2nd_engineer"]},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_stcw_basic_true_pass(self):
        result = self.analyzer._evaluate_stcw_basic_rule(
            {
                "certifications": {"stcw_basic_all_valid": True},
                "fact_meta": {"certifications.stcw_basic_all_valid": {"confidence": 0.9}},
            },
            {"required": True},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_stcw_basic_null_unknown(self):
        result = self.analyzer._evaluate_stcw_basic_rule(
            {
                "certifications": {"stcw_basic_all_valid": None},
                "fact_meta": {"certifications.stcw_basic_all_valid": {"confidence": None}},
            },
            {"required": True},
        )
        self.assertEqual(result["decision"], "UNKNOWN")

    def test_endorsement_rule_pass(self):
        result = self.analyzer._evaluate_endorsement_rule(
            {
                "certifications": {"endorsements": {"dp_operational": "present"}},
                "fact_meta": {"certifications.endorsements": {"confidence": 0.9}},
            },
            {"endorsements_required": ["dp_operational"]},
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("DPO", result["message"])
        self.assertEqual(result["actual_value"]["labels"]["dp_operational"], "DPO")

    def test_endorsement_rule_pass_message_names_requested_certificates(self):
        result = self.analyzer._evaluate_endorsement_rule(
            {
                "certifications": {
                    "endorsements": {
                        "tanker_oil": "present",
                        "cert_ecdis": "present",
                        "cert_pscrb": "present",
                    }
                },
                "fact_meta": {"certifications.endorsements": {"confidence": 0.9}},
            },
            {"endorsements_required": ["tanker_oil", "cert_ecdis", "cert_pscrb"]},
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("oil tanker endorsement", result["message"])
        self.assertIn("ECDIS", result["message"])
        self.assertIn("PSCRB", result["message"])

    def test_endorsement_rule_fail_when_absent(self):
        result = self.analyzer._evaluate_endorsement_rule(
            {
                "certifications": {"endorsements": {"dp_operational": "absent"}},
                "fact_meta": {"certifications.endorsements": {"confidence": 0.9}},
            },
            {"endorsements_required": ["dp_operational"]},
        )
        self.assertEqual(result["decision"], "FAIL")
        self.assertIn("DPO (absent)", result["message"])

    def test_endorsement_rule_unknown_when_missing(self):
        result = self.analyzer._evaluate_endorsement_rule(
            {
                "certifications": {"endorsements": {"dp_operational": "unknown"}},
                "fact_meta": {"certifications.endorsements": {"confidence": None}},
            },
            {"endorsements_required": ["dp_operational"]},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")
        self.assertIn("DPO", result["message"])

    def test_company_continuity_rule_pass(self):
        result = self.analyzer._evaluate_company_continuity_rule(
            {
                "derived": {"same_company_contract_count_max": 3},
                "fact_meta": {"derived.same_company_contract_count_max": {"status": "PARSED", "confidence": 0.9}},
            },
            {"min_same_company_contract_count": 2},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_company_continuity_rule_fail(self):
        result = self.analyzer._evaluate_company_continuity_rule(
            {
                "derived": {"same_company_contract_count_max": 1},
                "fact_meta": {"derived.same_company_contract_count_max": {"status": "PARSED", "confidence": 0.9}},
            },
            {"min_same_company_contract_count": 2},
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_company_continuity_rule_source_excluded_is_unknown(self):
        result = self.analyzer._evaluate_company_continuity_rule(
            {
                "derived": {"same_company_contract_count_max": None},
                "fact_meta": {"derived.same_company_contract_count_max": {"status": "SOURCE_EXCLUDED", "confidence": None}},
            },
            {"min_same_company_contract_count": 2},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_recency_rule_pass(self):
        result = self.analyzer._evaluate_recency_rule(
            {
                "experience": {
                    "last_sign_off_date": "2026-01-15",
                    "last_sign_off_months_ago": 2,
                },
                "fact_meta": {"experience.last_sign_off_date": {"status": "PARSED", "confidence": 0.9}},
            },
            {"max_months_since_sign_off": 6},
        )
        self.assertEqual(result["decision"], "PASS")

    def test_recency_rule_fail(self):
        result = self.analyzer._evaluate_recency_rule(
            {
                "experience": {
                    "last_sign_off_date": "2025-01-15",
                    "last_sign_off_months_ago": 14,
                },
                "fact_meta": {"experience.last_sign_off_date": {"status": "PARSED", "confidence": 0.9}},
            },
            {"max_months_since_sign_off": 6},
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_recency_rule_source_excluded_is_unknown(self):
        result = self.analyzer._evaluate_recency_rule(
            {
                "experience": {
                    "last_sign_off_date": None,
                    "last_sign_off_months_ago": None,
                },
                "fact_meta": {"experience.last_sign_off_date": {"status": "SOURCE_EXCLUDED", "confidence": None}},
            },
            {"max_months_since_sign_off": 6},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "FACTUAL_UNKNOWN")

    def test_v1_record_on_active_company_continuity_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "derived": {"same_company_contract_count_max": 3},
                "fact_meta": {"derived.same_company_contract_count_max": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["company_continuity"],
                "hard_constraints": {
                    "company_continuity": {"min_same_company_contract_count": 2},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_v1_record_on_active_recency_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "experience": {
                    "last_sign_off_date": "2026-01-15",
                    "last_sign_off_months_ago": 2,
                },
                "fact_meta": {"experience.last_sign_off_date": {"status": "PARSED", "confidence": 0.9}},
            },
            {
                "applied_constraints": ["recency"],
                "hard_constraints": {
                    "recency": {"max_months_since_sign_off": 6},
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_hard_filter_skips_endorsement_rule_when_not_in_applied_constraints(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "certifications": {"endorsements": {"dp_operational": "present"}},
                "fact_meta": {"certifications.endorsements": {"confidence": 0.9}},
            },
            {
                "applied_constraints": [],
                "hard_constraints": {
                    "certifications": {
                        "endorsements_required": ["dp_operational"],
                        "endorsement_display_value": "DPO",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"], [])

    def test_v1_record_on_active_endorsement_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "certifications": {"endorsements": {"dp_operational": "present"}},
                "fact_meta": {"certifications.endorsements": {"confidence": 0.9}},
            },
            {
                "applied_constraints": ["stcw_endorsement"],
                "hard_constraints": {
                    "certifications": {
                        "endorsements_required": ["dp_operational"],
                        "endorsement_display_value": "DPO",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_v1_record_on_active_passport_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "logistics": {
                    "passport_expiry_date": "2028-05-04",
                    "passport_expiry_status": "PARSED",
                },
                "fact_meta": {"logistics.passport_expiry_date": {"confidence": 0.9}},
            },
            {
                "applied_constraints": ["passport_validity"],
                "hard_constraints": {
                    "passport_validity": {
                        "required": True,
                        "must_be_valid": True,
                        "requested_label": "valid passport",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_v1_record_on_active_availability_rule_is_version_mismatch_unknown(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": "1.1",
                "logistics": {
                    "availability_date": "2026-03-30",
                    "availability_end_date": "2026-04-30",
                    "availability_status": "immediately",
                },
                "fact_meta": {"logistics.availability_date": {"confidence": 0.9}},
            },
            {
                "applied_constraints": ["availability"],
                "hard_constraints": {
                    "availability": {
                        "value_type": "status",
                        "status": "immediately",
                        "display_value": "available immediately",
                    },
                },
            },
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["facts_version"], "1.1")
        self.assertEqual(result["results"][0]["unknown_reason"], "VERSION_MISMATCH_UNKNOWN")

    def test_evidence_review_metadata_for_factual_unknown(self):
        metadata = self.analyzer._derive_evidence_review_metadata(
            {
                "decision": "UNKNOWN",
                "results": [
                    {
                        "decision": "UNKNOWN",
                        "reason_code": "AGE_DOB_AMBIGUOUS_FORMAT",
                        "unknown_reason": "FACTUAL_UNKNOWN",
                    }
                ],
            },
            {
                "fact_meta": {
                    "personal.dob": {"status": "AMBIGUOUS_NUMERIC"},
                    "travel.visa_records": {"status": "MISSING"},
                    "role.current_rank_normalized": {"status": "MISSING"},
                    "certifications.coc": {"status": "MISSING"},
                    "certifications.stcw_basic_all_valid": {"status": "MISSING"},
                    "logistics.passport_expiry_date": {"status": "MISSING"},
                }
            },
        )
        self.assertEqual(metadata["review_path_type"], "factual_unknown")
        self.assertEqual(metadata["evidence_review_state"], "insufficient_evidence")
        self.assertEqual(metadata["evidence_review_reasons"], ["age_evidence_ambiguous"])
        self.assertEqual(metadata["document_quality_hint"], "usable_but_noisy")

    def test_evidence_review_metadata_for_version_mismatch_unknown(self):
        metadata = self.analyzer._derive_evidence_review_metadata(
            {
                "decision": "UNKNOWN",
                "results": [
                    {
                        "decision": "UNKNOWN",
                        "reason_code": "RANK_RULE_REQUIRES_V2_FACTS",
                        "unknown_reason": "VERSION_MISMATCH_UNKNOWN",
                    }
                ],
            },
            {},
        )
        self.assertEqual(metadata["review_path_type"], "version_mismatch_unknown")
        self.assertEqual(metadata["evidence_review_state"], "partial_evidence")
        self.assertEqual(metadata["evidence_review_reasons"], ["version_mismatch_partial_evaluation"])
        self.assertIsNone(metadata["document_quality_hint"])


if __name__ == "__main__":
    unittest.main()
