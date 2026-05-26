import sys
import tempfile
import types
import unittest
from pathlib import Path


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
from candidate_facts.review_summary import _longest_same_company_run  # noqa: E402


class _FakeConfig:
    def __init__(self, download_root):
        self.download_root = str(download_root)
        self.min_similarity_score = 0.0


class AIAnalyzerCompanyContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.download_root = Path(self.temp_dir.name)
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.analyzer.config = _FakeConfig(self.download_root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_same_company_contract_count_from_seajobs_rows(self):
        text = """
https://www.seajob.net
Download by Njorships Management India Pvt Ltd
Seamen Experience Details
Sign In Sign Out
# Rank Company Name / Ship Type Tonnage Engine Date Date
1 Chief Officer Fleetmanagementlimited / Bulk Carrier 19999 01-Oct-2024 12-Apr-2025
2 Chief Officer Fleet management ltd / Bulk Carrier 36106 09-Oct-2023 18-Mar-2024
3 2nd Officer FLEET MANAGEMENT LTD / Bulk Carrier 43012 04-Jul-2023 09-Aug-2023
4 2nd Officer Other Company / Bulk Carrier 31505 24-Jan-2020 21-Oct-2020
Certificate Details
"""
        fact = self.analyzer._extract_same_company_contract_count_fact_from_text(
            text,
            original_path=Path("/tmp/Chief-Officer_Bulk-Carrier_1.pdf"),
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["count"], 3)
        self.assertTrue(fact["repeat_employer_present"])

    def test_extract_same_company_contract_count_accepts_named_download_banner(self):
        text = """
Download by : R Aditya (Njorships Management India Pvt Ltd)
Download on 2026-04-01 16:37:12
Seamen Experience Details
Sign In Sign Out
1 2nd Engineer ESM / Bitumen Tanker 10830 03-Mar-2025 06-Jul-2025
2 2nd Engineer ESM / Bitumen Tanker 28164 18-Nov-2023 05-Jul-2024
3 2nd Engineer FML / Oil/Chem Tanker 29940 13-Jul-2016 22-Jan-2017
4 3rd Engineer DMICO TANKERS / Oil/Chem Tanker 28381 25-Jul-2001 26-Jan-2002
5 3rd Engineer DMICO TANKERS / General Cargo 30719 09-Feb-2001 09-May-2001
Certificate Details
"""
        fact = self.analyzer._extract_same_company_contract_count_fact_from_text(
            text,
            original_path=Path("/tmp/2nd_Engineer_120969.pdf"),
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["count"], 2)
        self.assertTrue(fact["repeat_employer_present"])

    def test_email_resume_source_is_excluded(self):
        text = """
https://www.seajob.net
Download by Njorships Management India Pvt Ltd
Seamen Experience Details
1 Chief Officer Fleet management ltd / Bulk Carrier 36106 09-Oct-2023 18-Mar-2024
"""
        fact = self.analyzer._extract_same_company_contract_count_fact_from_text(
            text,
            original_path=Path("/tmp/EMAIL_20260508_resume.pdf"),
        )
        self.assertEqual(fact["status"], "SOURCE_EXCLUDED")
        self.assertIsNone(fact["count"])

    def test_build_candidate_facts_carries_seajobs_only_continuity_field(self):
        text = """
https://www.seajob.net
Download by Njorships Management India Pvt Ltd
Seamen Experience Details
1 Chief Officer Fleetmanagementlimited / Bulk Carrier 19999 01-Oct-2024 12-Apr-2025
2 Chief Officer Fleet management ltd / Bulk Carrier 36106 09-Oct-2023 18-Mar-2024
3 2nd Officer FLEET MANAGEMENT LTD / Bulk Carrier 43012 04-Jul-2023 09-Aug-2023
4 2nd Officer Other Company / Bulk Carrier 31505 24-Jan-2020 21-Oct-2020
Certificate Details
"""
        candidate_facts = self.analyzer._build_candidate_facts(
            "Chief-Officer_Bulk-Carrier_1.pdf",
            "Chief Officer",
            [{"metadata": {"raw_text": text}}],
            original_path=Path("/tmp/Chief-Officer_Bulk-Carrier_1.pdf"),
            text_cache={},
        )
        self.assertEqual(candidate_facts["derived"]["same_company_contract_count_max"], 3)
        self.assertEqual(
            candidate_facts["fact_meta"]["derived.same_company_contract_count_max"]["status"],
            "PARSED",
        )

    def test_build_candidate_facts_excludes_email_resume_continuity_field(self):
        text = """
SEA EXPERIENCE
COMPANY VESSEL TYPE RANK GRT NRT FROM TO
SCORPIO MARINE STI SOLACE TANKER OS 63915 32079 31/03/2022 28/07/2022
"""
        candidate_facts = self.analyzer._build_candidate_facts(
            "EMAIL_20260508_resume.pdf",
            "OS",
            [{"metadata": {"raw_text": text}}],
            original_path=Path("/tmp/EMAIL_20260508_resume.pdf"),
            text_cache={},
        )
        self.assertIsNone(candidate_facts["derived"]["same_company_contract_count_max"])
        self.assertEqual(
            candidate_facts["fact_meta"]["derived.same_company_contract_count_max"]["status"],
            "SOURCE_EXCLUDED",
        )

    def test_unknown_company_breaks_same_company_run(self):
        contracts = [
            {"company": "ABC Shipping"},
            {"company": ""},
            {"company": "ABC Shipping"},
        ]
        self.assertEqual(
            _longest_same_company_run(contracts),
            1,
        )

    def test_review_alignment_ignores_list_order_noise(self):
        analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        live_candidate_facts = {
            "schema_version": "candidate_facts.v1",
            "identity": {"candidate_name": {"value": "Jane Doe", "confidence": "high", "evidence_ids": ["ev-1"]}},
            "rank": {"value": "2nd_engineer", "confidence": "high", "evidence_ids": ["ev-1"]},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": ["tanker", "bulk carrier"]},
            "certifications": {"coc": {"grade": None, "expiry_date": None, "status": None}},
            "logistics": {"passport_expiry_date": None, "passport_valid": None},
            "derived": {},
            "evidence": [{"evidence_id": "ev-1", "source_kind": "raw_text_chunk", "source_id": "chunk-1"}],
        }
        review_candidate_facts = {
            "schema_version": "candidate_facts.v1",
            "identity": {"candidate_name": {"value": "Jane Doe", "confidence": "high", "evidence_ids": ["ev-1"]}},
            "rank": {"value": "2nd_engineer", "confidence": "high", "evidence_ids": ["ev-1"]},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": ["bulk carrier", "tanker"]},
            "certifications": {"coc": {"grade": None, "expiry_date": None, "status": None}},
            "logistics": {"passport_expiry_date": None, "passport_valid": None},
            "derived": {},
            "evidence": [{"evidence_id": "ev-1", "source_kind": "raw_text_chunk", "source_id": "chunk-1"}],
        }

        alignment = analyzer._build_review_alignment_report(live_candidate_facts, review_candidate_facts)
        self.assertEqual(alignment["status"], "match")
        self.assertEqual(alignment["mismatch_count"], 0)

    def test_review_alignment_flags_metadata_mismatch_for_same_value(self):
        analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        live_candidate_facts = {
            "schema_version": "candidate_facts.v1",
            "identity": {"candidate_name": {"value": "Jane Doe", "confidence": "high", "evidence_ids": ["ev-1"]}},
            "rank": {"value": "2nd_engineer", "confidence": "high", "evidence_ids": ["ev-1"]},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": ["tanker"]},
            "certifications": {"coc": {"grade": None, "expiry_date": None, "status": None}},
            "logistics": {"passport_expiry_date": None, "passport_valid": None},
            "derived": {},
            "evidence": [{"evidence_id": "ev-1", "source_kind": "raw_text_chunk", "source_id": "chunk-1"}],
        }
        review_candidate_facts = {
            "schema_version": "candidate_facts.v1",
            "identity": {"candidate_name": {"value": "Jane Doe", "confidence": "low", "evidence_ids": []}},
            "rank": {"value": "2nd_engineer", "confidence": "high", "evidence_ids": ["ev-1"]},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": ["tanker"]},
            "certifications": {"coc": {"grade": None, "expiry_date": None, "status": None}},
            "logistics": {"passport_expiry_date": None, "passport_valid": None},
            "derived": {},
            "evidence": [{"evidence_id": "ev-1", "source_kind": "raw_text_chunk", "source_id": "chunk-1"}],
        }

        alignment = analyzer._build_review_alignment_report(live_candidate_facts, review_candidate_facts)
        self.assertEqual(alignment["status"], "mismatch")
        self.assertEqual(alignment["mismatch_count"], 1)
        self.assertEqual(alignment["mismatches"][0]["reason"], "metadata_mismatch")


if __name__ == "__main__":
    unittest.main()
