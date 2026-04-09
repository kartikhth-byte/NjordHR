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


if __name__ == "__main__":
    unittest.main()
