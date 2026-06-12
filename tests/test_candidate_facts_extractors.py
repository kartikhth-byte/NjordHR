import tempfile
import unittest
from pathlib import Path

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
                "coc": {
                    "grade": "chief_officer",
                    "country": "india",
                    "issue_authority": "Indian",
                    "certificate_type": "Chief Officer(FG)",
                    "expiry_date": "2028-01-01",
                    "status": "VALID",
                },
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
                        "vessel_tonnage": [
                            {
                                "value": 58000,
                                "unit": "unspecified",
                                "source_label": "Tonnage",
                                "confidence": 0.90,
                                "evidence_text": "Tonnage: 58000",
                            }
                        ],
                        "engine_family": "wartsila_rt_flex",
                        "engine_types": ["wartsila_rt_flex"],
                        "engine_details": [
                            {
                                "engine_type": "wartsila_rt_flex",
                                "engine_family": "wartsila_rt_flex",
                                "manufacturer": "Wartsila/Sulzer",
                                "lineage": "RT-flex",
                                "category": "low_speed_2_stroke",
                                "control_type": "electronic_common_rail",
                                "fuel_family": "fuel_oil",
                                "fuel_tags": ["fuel_oil"],
                                "dual_fuel": False,
                                "match_source": "model_token",
                                "raw_mention": "RTFlex",
                            }
                        ],
                        "months_total": 60,
                    }
                ],
                "engine_types": ["wartsila_rt_flex"],
                "engine_details": [
                    {
                        "engine_type": "wartsila_rt_flex",
                        "engine_family": "wartsila_rt_flex",
                        "manufacturer": "Wartsila/Sulzer",
                        "lineage": "RT-flex",
                        "category": "low_speed_2_stroke",
                        "control_type": "electronic_common_rail",
                        "fuel_family": "fuel_oil",
                        "fuel_tags": ["fuel_oil"],
                        "dual_fuel": False,
                        "match_source": "model_token",
                        "raw_mention": "RTFlex",
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
        self.assertIn("Jane Doe", payload["identity"]["candidate_name"]["snippet"])
        self.assertIn("2nd Engineer", payload["rank"]["snippet"])
        self.assertTrue(payload["evidence"])

    def test_generic_pdf_extractor_uses_tight_excerpt_for_ocr_spaced_rank_labels(self):
        payload = generic_pdf.extract_candidate_facts(
            filename="resume-2",
            rank="Electrical Officer",
            chunks=[],
            raw_text=(
                "https://www .seajob.net\n"
                "Download by Njorships Management India Pvt Ltd\n"
                "Availability Details\n"
                "Applied For Rank Electrical Of ficer\n"
                "Present Rank Electrical Engineer\n"
            ),
        )
        self.assertIn("Applied For Rank", payload["rank"]["snippet"])
        self.assertNotIn("https://www", payload["rank"]["snippet"])
        self.assertLessEqual(len(payload["rank"]["snippet"]), 120)

    def test_generic_pdf_extractor_prefers_label_value_fragment_for_names(self):
        payload = generic_pdf.extract_candidate_facts(
            filename="resume-3",
            rank="2nd Engineer",
            chunks=[],
            raw_text="Personal & Contact Details\nName Ashish Kumar Email Address aditi.ashish30@gmail.com\nDate Of Birth 26-Oct-1983",
        )
        self.assertEqual(payload["identity"]["candidate_name"]["snippet"], "Name Ashish Kumar")
        self.assertNotIn("Email Address", payload["identity"]["candidate_name"]["snippet"])

    def test_generic_pdf_extractor_normalizes_windows_style_paths_for_display(self):
        payload = generic_pdf.build_candidate_facts_v1(
            _FakeAnalyzer(),
            "C:\\Users\\Kartik\\Downloads\\resume.pdf",
            "2nd Engineer",
            [],
            original_path="C:\\Users\\Kartik\\Downloads\\resume.pdf",
            raw_text="Jane Doe\n2nd Engineer",
        )
        self.assertEqual(payload["source"]["file_name"], "resume.pdf")
        self.assertEqual(payload["source"]["candidate_id"], "resume.pdf")
        self.assertEqual(payload["evidence"][0]["source_id"], "resume.pdf")
        self.assertIn("Jane Doe", payload["identity"]["candidate_name"]["snippet"])

    def test_generic_pdf_extractor_rejects_section_labels_as_names(self):
        payload = generic_pdf.extract_candidate_facts(
            filename="EMAIL_20260425_125559_AAMkADI5NjU4_MANISH_RESUME.pdf",
            rank="2nd Engineer",
            chunks=[],
            raw_text="RESUME\nVESSEL\nNAME TYPE OF\nVESSEL GRT ENGINE\nTYPE ENGINE\nBHP COMPANY",
        )
        self.assertEqual(payload["identity"]["candidate_name"]["value"], "Manish")

    def test_generic_pdf_extractor_stays_conservative_on_unsupported_layouts(self):
        payload = generic_pdf.extract_candidate_facts(
            filename="unsupported-layout.pdf",
            rank="2nd Engineer",
            chunks=[],
            raw_text="Jane Doe\n2nd Engineer\nPassport\nEmail: jane@example.com\nDate Of Birth 26-Oct-1983",
        )

        self.assertEqual(payload["identity"]["candidate_name"]["value"], "Jane Doe")
        self.assertEqual(payload["rank"]["value"], "2nd_engineer")
        self.assertEqual(payload["identity"]["dob"]["value"], None)
        self.assertEqual(payload["identity"]["dob"]["presence"], "unobserved_unknown")
        self.assertEqual(payload["documents"], [])
        self.assertEqual(payload["certificates"], [])
        self.assertEqual(payload["endorsements"], [])
        self.assertEqual(payload["courses"], [])
        self.assertEqual(payload["contracts"], [])
        self.assertEqual(payload["rank_experience"], [])
        self.assertEqual(payload["engine_experience"], [])
        self.assertEqual(payload["vessel_experience"], [])
        self.assertEqual(payload["application"]["applied_ship_types"], [])
        self.assertEqual(payload["derived"], {})
        self.assertEqual(payload["extraction"]["provenance"]["fallback_reason"], "unsupported_layout")
        self.assertIn("generic_candidate_facts_fallback", payload["extraction"]["warnings"])

    def test_generic_pdf_extractor_uses_email_sidecar_sender_when_pdf_name_is_generic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "EMAIL_20260428_080520_AAMkADI5NjU4_2.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            Path(str(pdf_path) + ".json").write_text(
                '{"candidate_name": "Anubhav Kumar Vessel", "mail_sender": "anubhavkumargautam@gmail.com", "attachment_name": "2. All Promotion Letters & Appraisals.pdf"}',
                encoding="utf-8",
            )
            payload = generic_pdf.build_candidate_facts_v1(
                _FakeAnalyzer(),
                pdf_path.name,
                "2nd Engineer",
                [],
                original_path=str(pdf_path),
                raw_text="Vessel Type BHP\nContract appraisal pages",
            )
        self.assertEqual(payload["identity"]["candidate_name"]["value"], "Anubhav Kumar")

    def test_generic_pdf_extractor_recovers_names_from_profile_fields_and_metadata(self):
        cases = [
            (
                "EMAIL_20260430_110207_AAMkADI5NjU4_CO_Rakesh_Dhankhar_CV.pdf",
                "PERSONAL DATA\nFirst Name : Rakesh Middle Name : Surname : Dhankhar",
                {},
                "Rakesh Dhankhar",
            ),
            (
                "EMAIL_20260430_164916_AAMkADI5NjU4_Annipa_Sakkarai.pdf",
                "COLOR PHOTOGRAPH\nSURNAME: SAKKARAI FIRST NAME: ANNIPA OTHER NAME:",
                {},
                "Annipa Sakkarai",
            ),
            (
                "EMAIL_20260507_023421_AAMkADI5NjU4_Saina_Goutham_Narayan_-_Chief_Mate.pdf",
                "Mobile\nEmail\nLinked In\nVessel Name Vessel flag ME Type Vessel owner",
                {"candidate_name": "Of Owners", "attachment_name": "Saina Goutham Narayan - Chief Mate.pdf"},
                "Saina Goutham Narayan",
            ),
            (
                "EMAIL_20260428_171956_AAMkADI5NjU4_pdf.pdf",
                "Maksym Kadema, 31 16-07-1994 maxkadema@gmail.com\nCitizenship Residence Closest Airport",
                {"candidate_name": "Vessel", "attachment_name": "pdf.pdf"},
                "Maksym Kadema",
            ),
            (
                "EMAIL_20260413_090624_AAMkADI5NjU4_CV_ABHISEK_JANA_3RD_OFFICER_TANKER.pdf",
                "SURNAME JANA FIRST NAME ABHISEK\nFULL NAME ANINDITA BASU RELATIONSHIP WIFE",
                {},
                "Abhisek Jana",
            ),
            (
                "3rd-Officer_Bulk-Carrier_240600_2025-09-04_22-40-10.pdf",
                "SeaJobs\nName Alan Frankie Fernandes Email Address alan@example.com\nAvailability Details",
                {},
                "Alan Frankie Fernandes",
            ),
            (
                "EMAIL_20260506_152453_AAMkADI5NjU4_Updated_CV.pdf",
                "VAIBHAV's Details\nFirst Name VAIBHA V Middle Name Sur Name MATHUR",
                {"candidate_name": "RankCompany"},
                "Vaibhav Mathur",
            ),
            (
                "EMAIL_20260504_114413_AAMkADI5NjU4_CV_3rd_Eng_Denys_Romanovskyi.pdf",
                "Ship kW Till\nCertificate pages",
                {"candidate_name": "Type Of", "attachment_name": "CV 3rd Eng Denys Romanovskyi.pdf"},
                "Denys Romanovskyi",
            ),
            (
                "EMAIL_20260402_063603_AAMkADI5NjU4_RESUME_2ND_OFF_GAURAV_GOYAL.pdf",
                "Personal Details\nNameasinPassport GOYAL GAURAV",
                {"attachment_name": "RESUME 2ND OFF GAURAV GOYAL.docx"},
                "Gaurav Goyal",
            ),
            (
                "EMAIL_20260505_144137_AAMkADI5NjU4_RESUME_MAR_25.pdf",
                "Resume March 2025",
                {"candidate_name": "Raghvendra . B . Singh", "attachment_name": "RESUME MAR 25 .docx NEW.pdf"},
                "Raghvendra B Singh",
            ),
            (
                "3rd-Officer_Crude-Oil-Tanker_194193_2025-10-29_20-07-48.pdf",
                "Name AKIN JOHN\nExperience details",
                {},
                "Akin John",
            ),
        ]
        for filename, raw_text, sidecar, expected_name in cases:
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory() as tmpdir:
                    pdf_path = Path(tmpdir) / filename
                    pdf_path.write_bytes(b"%PDF-1.4\n")
                    if sidecar:
                        Path(str(pdf_path) + ".json").write_text(
                            __import__("json").dumps(sidecar),
                            encoding="utf-8",
                        )
                    payload = generic_pdf.build_candidate_facts_v1(
                        _FakeAnalyzer(),
                        filename,
                        "Chief Officer",
                        [],
                        original_path=str(pdf_path),
                        raw_text=raw_text,
                    )
                self.assertEqual(payload["identity"]["candidate_name"]["value"], expected_name)

    def test_remaining_source_extractors_raise_not_implemented(self):
        modules = [certificates, endorsements, contracts, engines]
        for module in modules:
            with self.subTest(module=module.__name__):
                with self.assertRaises(NotImplementedError):
                    module.extract_candidate_facts()

    def test_seajobs_extractor_translates_legacy_facts_into_candidate_facts_v1(self):
        source_text = (
            "Jane Doe\n"
            "Passport Expiry Date 01-Jan-2029\n"
            "CoC Grade II/2 Expiry Date 01-Jan-2028\n"
            "Seamen Experience Details\n"
            "1 2nd Engineer MV Aurora Bulk Carrier ABC Shipping 01-Jan-2023 01-Jun-2023\n"
        )
        payload = seajobs.build_candidate_facts_v1(
            _FakeAnalyzer(),
            "resume-1",
            "2nd Engineer",
            [],
            original_path="resume.pdf",
            text_cache={"resume.pdf": source_text},
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
        self.assertEqual(payload["experience"]["engine_types"], ["wartsila_rt_flex"])
        self.assertEqual(payload["experience"]["engine_details"][0]["lineage"], "RT-flex")
        self.assertEqual(payload["experience"]["vessel_tonnage_values"], [58000])
        self.assertEqual(payload["experience"]["max_vessel_tonnage"], 58000)
        self.assertEqual(payload["experience"]["min_vessel_tonnage"], 58000)
        passport_fact = next(doc for doc in payload["documents"] if doc["document_type"] == "passport")
        coc_fact = next(cert for cert in payload["certificates"] if cert["certificate_type"] == "coc")
        contract_fact = next(contract for contract in payload["contracts"] if contract.get("snippet"))
        self.assertEqual(contract_fact["engine_family"], "wartsila_rt_flex")
        self.assertEqual(contract_fact["engine_details"][0]["control_type"], "electronic_common_rail")
        self.assertEqual(contract_fact["vessel_tonnage"][0]["value"], 58000)
        self.assertEqual(contract_fact["vessel_tonnage"][0]["unit"], "unspecified")
        self.assertEqual(coc_fact["country"], "india")
        self.assertEqual(coc_fact["issue_authority"], "Indian")
        self.assertEqual(coc_fact["certificate_type_raw"], "Chief Officer(FG)")
        self.assertIn("Passport Expiry Date", passport_fact["snippet"])
        self.assertIn("CoC Grade", coc_fact["snippet"])
        self.assertIn("Bulk Carrier", contract_fact["snippet"])

    def test_orchestrator_routes_seajobs_like_pdf_even_without_metadata(self):
        source_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "1 2nd Engineer Synergy Maritime / Oil Tanker 58000 MAN B&W 09-Jan-2024 03-Apr-2024\n"
        )
        payload = build_candidate_facts_v1(
            _FakeAnalyzer(),
            "2nd_Engineer_120969.pdf",
            "2nd Engineer",
            [{"metadata": {"raw_text": source_text}}],
            source_origin="manual_upload",
            detected_layout="unknown",
        )
        self.assertEqual(payload["source"]["source_origin"], "seajobs_download")
        self.assertEqual(payload["source"]["detected_layout"], "seajobs")
        self.assertEqual(payload["contracts"][0]["vessel_tonnage"][0]["value"], 58000)

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
