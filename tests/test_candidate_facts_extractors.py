import unittest

from candidate_facts.extractors import certificates, contracts, endorsements, engines, generic_pdf, seajobs
from candidate_facts.orchestrator import build_candidate_facts_v1


class _FakeAnalyzer:
    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        return {
            "candidate_id": filename,
            "identity": {"full_name": "Jane Doe"},
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "personal": {"dob": "1988-02-03"},
            "certifications": {
                "coc": {"grade": "chief_officer", "expiry_date": "2028-01-01", "status": "VALID"},
                "stcw_basic_all_valid": True,
                "endorsements": {"tanker_gas": "advanced"},
            },
            "logistics": {
                "passport_expiry_date": "2029-01-01",
                "passport_valid": True,
                "us_visa_status": "VALID",
                "us_visa_expiry_date": "2028-06-01",
            },
            "experience": {
                "service_rows": [
                    {
                        "rank_normalized": "2nd_engineer",
                        "vessel_name": "MV Aurora",
                        "months_total": 60,
                    }
                ],
                "rank_duration_rows": [
                    {
                        "rank_normalized": "2nd_engineer",
                        "months_total": 60,
                    }
                ],
            },
            "application": {"applied_ship_types": ["tanker"]},
            "derived": {
                "age_years": 37,
                "current_rank_months_total": 60,
                "same_company_contract_count_max": 2,
                "has_contract_gap_over_6_months": False,
            },
        }


class _EmptyAnalyzer:
    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        return {}


class _HollowAnalyzer:
    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        return {
            "identity": {"full_name": None},
            "role": {"applied_rank_normalized": None},
            "personal": {"dob": None},
            "certifications": {"coc": {"grade": None, "expiry_date": None, "status": None}},
            "logistics": {"passport_valid": None, "us_visa_status": None},
            "experience": {"service_rows": [], "rank_duration_rows": []},
            "application": {"applied_ship_types": []},
            "derived": {"age_years": None},
        }


class CandidateFactsExtractorStubTests(unittest.TestCase):
    def test_generic_pdf_extractor_builds_portable_candidate_facts(self):
        payload = generic_pdf.extract_candidate_facts(
            filename="resume-1",
            rank="2nd Engineer",
            chunks=[],
            raw_text="Jane Doe\n2nd Engineer\nPassport\nEmail: jane@example.com",
        )
        self.assertEqual(payload["schema_version"], "candidate_facts.v1")
        self.assertEqual(payload["source"]["source_origin"], "manual_upload")
        self.assertEqual(payload["source"]["detected_layout"], "unknown")
        self.assertEqual(payload["rank"]["value"], "2nd_engineer")
        self.assertEqual(payload["validation"]["status"], "degraded")
        self.assertEqual(payload["identity"]["candidate_name"]["value"], "Jane Doe")
        self.assertTrue(payload["evidence"])

    def test_remaining_source_extractors_raise_not_implemented(self):
        modules = [certificates, endorsements, contracts, engines]
        for module in modules:
            with self.subTest(module=module.__name__):
                with self.assertRaises(NotImplementedError):
                    module.extract_candidate_facts()

    def test_seajobs_extractor_translates_legacy_facts_into_candidate_facts_v1(self):
        payload = seajobs.build_candidate_facts_v1(
            _FakeAnalyzer(),
            "resume-1",
            "2nd Engineer",
            [],
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
            folder_metadata={},
        )
        self.assertEqual(payload["schema_version"], "candidate_facts.v1")
        self.assertEqual(payload["source"]["source_origin"], "seajobs_download")
        self.assertEqual(payload["source"]["detected_layout"], "seajobs")
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["rank"]["value"], "2nd_engineer")
        self.assertTrue(any(doc["document_type"] == "passport" for doc in payload["documents"]))
        self.assertTrue(any(doc["document_type"] == "us_visa" for doc in payload["documents"]))
        self.assertTrue(any(cert["certificate_type"] == "coc" for cert in payload["certificates"]))
        self.assertTrue(any(cert["certificate_type"] == "stcw_basic" for cert in payload["certificates"]))
        self.assertTrue(any(end["endorsement_type"] == "tanker_gas" for end in payload["endorsements"]))
        self.assertTrue(payload["evidence"])

    def test_seajobs_extractor_marks_empty_extraction_partial(self):
        payload = seajobs.build_candidate_facts_v1(
            _EmptyAnalyzer(),
            "resume-empty",
            "2nd Engineer",
            [],
            original_path=None,
            text_cache={},
            folder_metadata={},
        )
        self.assertEqual(payload["extraction"]["status"], "partial")

    def test_seajobs_extractor_marks_hollow_shell_partial(self):
        payload = seajobs.build_candidate_facts_v1(
            _HollowAnalyzer(),
            "resume-hollow",
            "2nd Engineer",
            [],
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe"},
            folder_metadata={},
        )
        self.assertEqual(payload["validation"]["status"], "degraded")
        self.assertEqual(payload["extraction"]["status"], "partial")

    def test_seajobs_orchestrator_routes_to_seajobs_bridge(self):
        payload = build_candidate_facts_v1(
            _FakeAnalyzer(),
            "resume-1",
            "2nd Engineer",
            [],
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
            folder_metadata={},
            source_origin="seajobs_download",
            detected_layout="seajobs",
        )
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertEqual(payload["source"]["source_origin"], "seajobs_download")
        self.assertEqual(payload["source"]["detected_layout"], "seajobs")

    def test_orchestrator_falls_back_to_generic_partial_shell_for_unknown_layout(self):
        payload = build_candidate_facts_v1(
            _FakeAnalyzer(),
            "resume-2",
            "2nd Engineer",
            [],
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
            folder_metadata={},
            source_origin="manual_upload",
            detected_layout="unknown",
        )
        self.assertEqual(payload["validation"]["status"], "degraded")
        self.assertEqual(payload["source"]["source_origin"], "manual_upload")
        self.assertEqual(payload["source"]["detected_layout"], "unknown")
        self.assertEqual(payload["extraction"]["status"], "partial")
        self.assertEqual(payload["documents"], [])
        self.assertEqual(payload["rank"]["value"], "2nd_engineer")


if __name__ == "__main__":
    unittest.main()
