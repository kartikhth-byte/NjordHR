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
