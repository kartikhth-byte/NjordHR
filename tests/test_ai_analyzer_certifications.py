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


class AIAnalyzerCertificationTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_extract_coc_fact_valid(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate of Competency: 2nd Engineer\nValid Until: 04-May-2028"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_engineer")
        self.assertEqual(fact["expiry_date"], date(2028, 5, 4))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_expired(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "COC: Chief Engineer\nExpiry Date: 04-May-2020"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_engineer")
        self.assertEqual(fact["expiry_date"], date(2020, 5, 4))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_absent(self):
        fact = self.analyzer._extract_coc_fact_from_text("Passport only. No competency document listed.")
        self.assertEqual(fact["status"], "MISSING")
        self.assertIsNone(fact["grade"])
        self.assertIsNone(fact["expiry_date"])

    def test_extract_coc_fact_missing_expiry_is_unknownish(self):
        fact = self.analyzer._extract_coc_fact_from_text("COC: 2nd Engineer")
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_engineer")
        self.assertIsNone(fact["expiry_date"])
        self.assertEqual(fact["expiry_status"], "MISSING")

    def test_extract_coc_fact_supports_expiry_format_variants(self):
        cases = [
            ("COC: 2nd Engineer\nValid Until: 2028-05-04", date(2028, 5, 4)),
            ("COC: 2nd Engineer\nValid Until: 04/May/2028", date(2028, 5, 4)),
            ("COC: 2nd Engineer\nValid Until: May 4, 2028", date(2028, 5, 4)),
        ]
        for raw_text, expected_date in cases:
            with self.subTest(raw_text=raw_text):
                fact = self.analyzer._extract_coc_fact_from_text(raw_text)
                self.assertEqual(fact["expiry_date"], expected_date)
                self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_certificate_table_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
            "IF0017969 Master(FG) India 28-Feb-2025 27-Feb-2030 02DL4857"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "master")
        self.assertEqual(fact["country"], "india")
        self.assertEqual(fact["issue_authority"], "India")
        self.assertEqual(fact["certificate_type"], "Master(FG)")
        self.assertEqual(fact["expiry_date"], date(2030, 2, 27))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_indian_certificate_details_table_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate Details\n"
            "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
            "95X5838 MEO Class II (Motor) Indian 11-Mar-2024 22-Apr-2029 99EL3260"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_engineer")
        self.assertEqual(fact["country"], "india")
        self.assertEqual(fact["issue_authority"], "Indian")
        self.assertEqual(fact["certificate_type"], "MEO Class II (Motor)")
        self.assertEqual(fact["expiry_date"], date(2029, 4, 22))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_additional_demonym_country_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate Details\n"
            "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
            "ZA844428 Second Mate (FG) Iranian 20-Dec-2021 20-Dec-2026 16NL2433"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_officer")
        self.assertEqual(fact["country"], "iran")
        self.assertEqual(fact["issue_authority"], "Iranian")
        self.assertEqual(fact["certificate_type"], "Second Mate (FG)")
        self.assertEqual(fact["expiry_date"], date(2026, 12, 20))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_long_tail_demonym_country_rows(self):
        cases = {
            "Colombian": "colombia",
            "Bolivian": "bolivia",
            "Peruvian": "peru",
            "Venezuelan": "venezuela",
            "Ecuadorian": "ecuador",
            "Algerian": "algeria",
            "Tunisian": "tunisia",
            "Libyan": "libya",
            "Moroccan": "morocco",
            "Serbian": "serbia",
            "Bulgarian": "bulgaria",
            "Hungarian": "hungary",
            "Belarusian": "belarus",
            "Estonian": "estonia",
            "Latvian": "latvia",
            "Lithuanian": "lithuania",
            "Thai": "thailand",
            "Saudi": "saudi arabia",
            "Bahraini": "bahrain",
            "Omani": "oman",
            "Qatari": "qatar",
            "Emirati": "uae",
            "Nigerian": "nigeria",
            "Kenyan": "kenya",
            "South African": "south africa",
        }
        for issue_authority, expected_country in cases.items():
            with self.subTest(issue_authority=issue_authority):
                fact = self.analyzer._extract_coc_fact_from_text(
                    "Certificate Details\n"
                    "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
                    f"ZA844428 Second Mate (FG) {issue_authority} 20-Dec-2021 20-Dec-2026 16NL2433"
                )
                self.assertEqual(fact["status"], "PARSED")
                self.assertEqual(fact["grade"], "2nd_officer")
                self.assertEqual(fact["country"], expected_country)
                self.assertEqual(fact["issue_authority"], issue_authority)
                self.assertEqual(fact["certificate_type"], "Second Mate (FG)")
                self.assertEqual(fact["expiry_date"], date(2026, 12, 20))
                self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_long_tail_maritime_demonym_rows(self):
        cases = {
            "Maltese": "malta",
            "Lebanese": "lebanon",
            "Burmese": "myanmar",
            "Nepalese": "nepal",
            "Sudanese": "sudan",
            "Taiwanese": "taiwan",
            "Iraqi": "iraq",
            "Kuwaiti": "kuwait",
            "Yemeni": "yemen",
        }
        for issue_authority, expected_country in cases.items():
            with self.subTest(issue_authority=issue_authority):
                fact = self.analyzer._extract_coc_fact_from_text(
                    "Certificate Details\n"
                    "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
                    f"ZA844428 Second Mate (FG) {issue_authority} 20-Dec-2021 20-Dec-2026 16NL2433"
                )
                self.assertEqual(fact["country"], expected_country)

    def test_extract_coc_fact_from_multitoken_issue_authority_rows(self):
        cases = {
            "DG Shipping": "india",
            "Government of India": "india",
            "Republic of Liberia": "liberia",
            "Bahamas Maritime Authority": "bahamas",
            "Marshall Islands Registry": "marshall islands",
            "Transport Malta": "malta",
        }
        for issue_authority, expected_country in cases.items():
            with self.subTest(issue_authority=issue_authority):
                fact = self.analyzer._extract_coc_fact_from_text(
                    "Certificate Details\n"
                    "Certificate No Certificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
                    f"ZA844428 Second Mate (FG) {issue_authority} 20-Dec-2021 20-Dec-2026 16NL2433"
                )
                self.assertEqual(fact["status"], "PARSED")
                self.assertEqual(fact["country"], expected_country)
                self.assertEqual(fact["issue_authority"], issue_authority)

    def test_extract_coc_country_from_snippet_does_not_treat_in_as_india(self):
        self.assertIsNone(self.analyzer._extract_coc_country_from_snippet("COC issued in 2019"))

    def test_extract_coc_fact_does_not_infer_country_from_adjacent_employer_demonym(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate of Competency\n"
            "Chief Officer\n"
            "Expiry Date 20-Dec-2026\n"
            "Employer: South African Marine Corporation"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertIsNone(fact["country"])

    def test_extract_coc_fact_from_certificate_table_row_without_explicit_coc_label(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "D10008212 Chief Officer(FG) Singapore 08-Apr-2025 04-Apr-2030 14HL9555"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_officer")
        self.assertEqual(fact["country"], "singapore")
        self.assertEqual(fact["issue_authority"], "Singapore")
        self.assertEqual(fact["certificate_type"], "Chief Officer(FG)")
        self.assertEqual(fact["expiry_date"], date(2030, 4, 4))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_certificate_details_table(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Certificate Details\n"
            "Certificate NoCertificate Type Issue Authority Issue Date Expiry Date Indos No.\n"
            "202990536 Panama First EngineerPanama 17-Sep-202103-Sep-202601RL5314\n"
            "Academic Details\n"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_engineer")
        self.assertEqual(fact["expiry_date"], date(2026, 9, 3))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_first_mate_certificate_table_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "CoC0097228 First Mate (FG) UK 14-Jun-2022 18-May-2027 07NL1786"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_officer")
        self.assertEqual(fact["expiry_date"], date(2027, 5, 18))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_second_mate_certificate_table_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "IF39438 Second Mate (FG) Indian 23-Apr-2023 19-May-2028 10NL3975"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_officer")
        self.assertEqual(fact["expiry_date"], date(2028, 5, 19))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_meo_class_iv_certificate_table_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "95Z40337 MEO Class IV INDIAN 20-Nov-2024 01-Oct-2029 21EM1865"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "4th_engineer")
        self.assertEqual(fact["expiry_date"], date(2029, 10, 1))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_meo_class_i_motor_certificate_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "95W10185 MEO Class I (Motor) Indian 22-Nov-2018 28-Mar-2028 07EL1949"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_engineer")
        self.assertEqual(fact["expiry_date"], date(2028, 3, 28))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_meo_class_ii_motor_certificate_row(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "95X5838 MEO Class II (Motor) Indian 11-Mar-2024 22-Apr-2029 99EL3260"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_engineer")
        self.assertEqual(fact["expiry_date"], date(2029, 4, 22))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_meo_cl_variant(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "COC (MEO CL-2) 95X-15888 DELHI 02.02.2022 16.01.2027"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "2nd_engineer")

    def test_extract_coc_fact_from_dotted_coc_held_label_without_expiry(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "RANK APPLIED FOR: CHIEF OFFICER - VLCC C.O.C HELD: MASTER F.G IFOO -10201 (MMD DELHI / INDIA )"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "master")
        self.assertIsNone(fact["expiry_date"])
        self.assertEqual(fact["expiry_status"], "MISSING")

    def test_extract_coc_fact_from_highest_license_held_without_expiry(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "Appraisee's Rank: THIRD ENGINEER Highest License Held MEO CL IV Master: Capt.A.P.Anaokar"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "4th_engineer")
        self.assertIsNone(fact["expiry_date"])
        self.assertEqual(fact["expiry_status"], "MISSING")

    def test_extract_stcw_all_present_is_true(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "PST Valid Until: 04-May-2028\n"
            "FPFF Valid Until: 04-May-2028\n"
            "EFA Valid Until: 04-May-2028\n"
            "PSSR Valid Until: 04-May-2028\n"
        )
        self.assertTrue(fact["stcw_basic_all_valid"])

    def test_extract_stcw_one_expired_is_false(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "PST Valid Until: 04-May-2028\n"
            "FPFF expired 04-May-2020\n"
            "EFA Valid Until: 04-May-2028\n"
            "PSSR Valid Until: 04-May-2028\n"
        )
        self.assertFalse(fact["stcw_basic_all_valid"])

    def test_extract_stcw_one_absent_is_false(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "PST Valid Until: 04-May-2028\n"
            "No FPFF\n"
            "EFA Valid Until: 04-May-2028\n"
            "PSSR Valid Until: 04-May-2028\n"
        )
        self.assertFalse(fact["stcw_basic_all_valid"])

    def test_extract_stcw_one_pending_is_null(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "PST pending\n"
            "FPFF Valid Until: 04-May-2028\n"
            "EFA Valid Until: 04-May-2028\n"
            "PSSR Valid Until: 04-May-2028\n"
        )
        self.assertIsNone(fact["stcw_basic_all_valid"])

    def test_extract_stcw_one_not_mentioned_is_null(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "PST Valid Until: 04-May-2028\n"
            "FPFF Valid Until: 04-May-2028\n"
            "EFA Valid Until: 04-May-2028\n"
        )
        self.assertIsNone(fact["stcw_basic_all_valid"])

    def test_extract_stcw_section_absent_is_null(self):
        fact = self.analyzer._extract_stcw_fact_from_text("COC: 2nd Engineer\nValid Until: 04-May-2028")
        self.assertIsNone(fact["stcw_basic_all_valid"])

    def test_extract_stcw_dense_certificate_list_with_all_four_aliases_is_true(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "Advance Fire Fighting (AFF), ARPA, Elementary First Aid (EFA), "
            "Fire Prevention and Fire Fighting (FPFF), GMDSS, Medical First Aid (MFA), "
            "Personal Safety & Social Responsibility (PSSR), PERSONAL SURVIVAL TECHNIQUES, "
            "Proficiency in Survival Techniques (PST)"
        )
        self.assertTrue(fact["stcw_basic_all_valid"])
        self.assertEqual(fact["certificates"]["pst"], "present")
        self.assertEqual(fact["certificates"]["fpff"], "present")
        self.assertEqual(fact["certificates"]["efa"], "present")
        self.assertEqual(fact["certificates"]["pssr"], "present")

    def test_extract_stcw_dense_certificate_list_with_punctuated_abbreviations_is_true(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "COURSES DETAILS COURSE CERT NO. DATE OF ISSUE PLACE OF ISSUE "
            "P.S.T 4140386101220472 06-12-2022 VIZIANAGARAM "
            "F.P.F.F 4140386101220472 06-12-2022 VIZIANAGARAM "
            "E.F.A 4140386101220472 06-12-2022 VIZIANAGARAM "
            "P.S.S.R 4140386101220472 06-12-2022 VIZIANAGARAM"
        )
        self.assertTrue(fact["stcw_basic_all_valid"])
        self.assertEqual(fact["certificates"]["pst"], "present")
        self.assertEqual(fact["certificates"]["fpff"], "present")
        self.assertEqual(fact["certificates"]["efa"], "present")
        self.assertEqual(fact["certificates"]["pssr"], "present")

    def test_extract_stcw_dense_certificate_list_with_ampersand_phrase_variants_is_true(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "Elementary First Aid (EFA) Fire Prevention and Fire Fighting (FPFF) "
            "Personal Safety & Social Responsibility (PSSR) "
            "Personal Survival Technique"
        )
        self.assertTrue(fact["stcw_basic_all_valid"])
        self.assertEqual(fact["certificates"]["pst"], "present")
        self.assertEqual(fact["certificates"]["fpff"], "present")
        self.assertEqual(fact["certificates"]["efa"], "present")
        self.assertEqual(fact["certificates"]["pssr"], "present")

    def test_extract_stcw_alias_with_unrelated_year_does_not_mark_expired(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "Pre Sea Training IMU 2009 80% Course Details "
            "Proficiency in Survival Techniques (PST) "
            "Advance Fire Fighting (AFF), Elementary First Aid (EFA), "
            "Fire Prevention and Fire Fighting (FPFF), "
            "Personal Safety & Social Responsibility (PSSR)"
        )
        self.assertNotEqual(fact["certificates"]["pst"], "expired")
        self.assertTrue(fact["stcw_basic_all_valid"])

    def test_extract_stcw_pst_not_expired_from_later_endorsement_expiry_date(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "Personal Safety & Social Responsibility (PSSR) "
            "PERSONAL SURVIVAL TECHNIQUES "
            "Proficiency in Survival Techniques (PST) "
            "RADAR & NAVIGATION SIMULATOR COURSE "
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Support 18-Feb-2016"
        )
        self.assertNotEqual(fact["certificates"]["pst"], "expired")
        self.assertIsNone(fact["stcw_basic_all_valid"])

    def test_extract_stcw_pssr_not_expired_from_later_endorsement_expiry_date(self):
        fact = self.analyzer._extract_stcw_fact_from_text(
            "Medical First Aid (MFA) "
            "Oil Tanker Familiarization (OTFC) "
            "Personal Safety & Social Responsibility (PSSR) "
            "Proficiency in Survival Craft and Rescue Boat (PSCRB) "
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Management 09-Nov-2027"
        )
        self.assertNotEqual(fact["certificates"]["pssr"], "expired")

    def test_extract_advanced_igf_cop_from_certificate_list(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Certificates: "
            "Certificate of Proficiency in Advanced Training for Ships Subject to the IGF Code "
            "Valid Until: 04-May-2028"
        )
        self.assertEqual(endorsements["igf_advanced_cop"], "present")

    def test_extract_advanced_igf_cop_alias_from_dense_list(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Courses completed: Advanced IGF CoP, ECDIS, BRM"
        )
        self.assertEqual(endorsements["igf_advanced_cop"], "present")

    def test_extract_advanced_igf_endorsement_alias_from_course_details(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Course Details Advance Fire Fighting (AFF) Advanced IGF Endorsement ARPA"
        )
        self.assertEqual(endorsements["igf_advanced_cop"], "present")

    def test_extract_basic_igf_cop_from_certificate_list(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Certificate of Proficiency in Basic Training for Ships Subject to the IGF Code "
            "Valid Until: 04-May-2028"
        )
        self.assertEqual(endorsements["igf_basic_cop"], "present")

    def test_extract_tanker_dce_support_and_management_aliases(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Support 18-Feb-2028 "
            "Chemical Tanker Management 09-Nov-2027 "
            "Gas Tanker DC Management Valid Until 04-May-2028"
        )
        self.assertEqual(endorsements["tanker_oil"], "present")
        self.assertEqual(endorsements["tanker_oil_basic_cop"], "present")
        self.assertEqual(endorsements["tanker_chemical"], "present")
        self.assertEqual(endorsements["tanker_chemical_advanced_cop"], "present")
        self.assertEqual(endorsements["tanker_gas"], "present")
        self.assertEqual(endorsements["tanker_gas_advanced_cop"], "present")

    def test_extract_dce_management_uses_date_after_matching_row(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Management 20-Jan-2026 "
            "LPG Carrier Management 06-Oct-2027 "
            "Chemical Tanker Management 02-Oct-2027"
        )
        self.assertEqual(endorsements["tanker_chemical"], "present")
        self.assertEqual(endorsements["tanker_chemical_advanced_cop"], "present")

    def test_extract_dce_section_marks_missing_tanker_type_absent(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Management 20-Jan-2029"
        )
        self.assertEqual(endorsements["tanker_oil"], "present")
        self.assertEqual(endorsements["tanker_chemical"], "absent")
        self.assertEqual(endorsements["tanker_chemical_advanced_cop"], "absent")

    def test_extract_lpg_carrier_dce_as_gas_tanker_endorsement(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "LPG Carrier Management 06-Oct-2027"
        )
        self.assertEqual(endorsements["tanker_gas"], "present")
        self.assertEqual(endorsements["tanker_gas_advanced_cop"], "present")

    def test_gas_tanker_familiarization_does_not_satisfy_dce_support(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Dangerous Cargo Endorsement Type of Ship DCE Levels Expiry Date "
            "Oil Tanker Management 24-May-2029 "
            "Courses: GASCO Liquefied Gas Tanker Familiarization Medical First Aid"
        )
        self.assertEqual(endorsements["tanker_gas"], "absent")
        self.assertEqual(endorsements["tanker_gas_basic_cop"], "absent")

    def test_extract_common_course_certificates_from_dense_list(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Courses completed: ECDIS, ARPA, BRM, ERM, PSCRB, AFF, "
            "Medical First Aid, Medical Care, Ship Security Officer"
        )
        self.assertEqual(endorsements["cert_ecdis"], "present")
        self.assertEqual(endorsements["cert_arpa"], "present")
        self.assertEqual(endorsements["cert_brm_btm"], "present")
        self.assertEqual(endorsements["cert_erm"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_aff"], "present")
        self.assertEqual(endorsements["cert_mfa"], "present")
        self.assertEqual(endorsements["cert_medical_care"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")

    def test_course_details_marks_missing_common_certificate_absent(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Course Details Advance Fire Fighting (AFF) "
            "Medical First Aid (MFA) "
            "Proficiency in Survival Craft and Rescue Boat (PSCRB)"
        )
        self.assertEqual(endorsements["cert_aff"], "present")
        self.assertEqual(endorsements["cert_mfa"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_ecdis"], "absent")
        self.assertEqual(endorsements["igf_advanced_cop"], "absent")
        self.assertEqual(endorsements["gmdss"], "absent")

    def test_course_details_keeps_ecdis_present_when_listed(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Course Details ECDIS Advance Fire Fighting (AFF)"
        )
        self.assertEqual(endorsements["cert_ecdis"], "present")

    def test_course_details_keeps_gmdss_present_when_listed(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Course Details GMDSS ECDIS ARPA"
        )
        self.assertEqual(endorsements["gmdss"], "present")

    def test_course_details_marks_compact_listed_courses_present(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Course Details Advance Fire Fighting (AFF)ARPAAUTOMATIC RADAR PLOTTING "
            "AIDSElementary First Aid (EFA)GMDSSMedical First Aid (MFA)Personal "
            "Safety & Social Responsibility (PSSR)PERSONAL SURVIVAL TECHNIQUES"
            "Proficiency in Survival Craft and Rescue Boat Courses (PSCRB)ROCSSO"
            "STSDSDSHIP SECURITY OFFICER"
        )
        self.assertEqual(endorsements["gmdss"], "present")
        self.assertEqual(endorsements["cert_arpa"], "present")
        self.assertEqual(endorsements["cert_mfa"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")

    def test_stcw_cert_details_heading_marks_missing_common_certificate_absent(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "STCW CERT. DETAILS DATE OF DATE OF PLACE OF COURSE CERTIFICATE NO. "
            "ARPA 150099 21/08/2015 - MUMBAI "
            "SSO 2070436511230221 29/07/2023 - MUMBAI "
            "PSCRB (RF) 2010016212252493 18/10/2025 17/10/2030 MUMBAI"
        )
        self.assertEqual(endorsements["cert_arpa"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")
        self.assertEqual(endorsements["cert_mfa"], "absent")

    def test_compact_details_of_courses_heading_is_treated_as_course_section(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "DETAILSOFCOURSES&CERTIFICATESFOROFFICERS: Courses CertificateNo. "
            "Medicare 3030146421260023 SEI KOLKATA 26.02.2026 NA "
            "ProficiencyinSurvivalCraft&RescueBoat(PSCRB) 3030166212260095 "
            "SSO(ShipSecurityOfficersCourse) 201001104190302 "
            "ARPA(AutomaticRadarPlottingAid) ARPA/0016/002/13 "
            "GMDSS GOC-M-5522 GOI 20.07.2022 19.07.2042"
        )
        self.assertEqual(endorsements["gmdss"], "present")
        self.assertEqual(endorsements["cert_arpa"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")
        self.assertEqual(endorsements["cert_mfa"], "absent")

    def test_certificates_details_heading_marks_missing_common_certificate_absent(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "CERTIFICATES DETAILS : CERTIFICATE CERTIFICATE NO. INSTITUTE DATE OF ISSUE "
            "Proficiency In Survival Craft/R.B 2010016212221977 B.P. Marine 25/07/2022 "
            "ECDIS 1111961/848 South Tyneside 16/11/2012 "
            "GMDSS GOC/M/112 Anglo Eastern Signal 18/03/2015 "
            "SSO BPMA/SSO/728/2018 B.P. Marine 08/09/2018"
        )
        self.assertEqual(endorsements["gmdss"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")
        self.assertEqual(endorsements["cert_arpa"], "absent")
        self.assertEqual(endorsements["cert_mfa"], "absent")

    def test_stcw_and_other_certificates_heading_handles_table_headers(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "STCW AND OTHER CERTIFICATES STCW Courses Certificate No Date of Date of "
            "Place of Issuing REVISION : 1 Issue Expiry Issue Authority YES NO "
            "EFA / MFA / MEDICARE 0120090022005 12.03.05 MUM TS REHMAN "
            "PST / PSCRB (Survival) 0050120022005 25.03.05 MUM TS REHMAN "
            "FP & FF / AFF (Fire Fighting) 0020110022005 19.03.05 MUM TS REHMAN "
            "BRM / ERM / VRM LARGE VESSEL HANDLING SIMULATOR COURSE"
        )
        self.assertEqual(endorsements["cert_mfa"], "present")
        self.assertEqual(endorsements["cert_medical_care"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_aff"], "present")
        self.assertEqual(endorsements["cert_erm"], "present")

    def test_courses_certificate_no_heading_and_engine_room_resource_variant(self):
        endorsements = self.analyzer._extract_endorsements_from_text(
            "Courses Certificate No. Issued By DateIssued Date Of Expiry "
            "MEDICARE 41/2020 BPMA 23/02/2019 NA "
            "PSCRBU & PSTU 46 SIMS 07/11/2023 06/11/2028 "
            "SSO - Ship Security Officers Course 53 SCOT 11/09/2013 NA "
            "RANSCO - Radar, Arpa & Navigation Simulator NZMS 07/07/2009 NA "
            "Engine room resource 1565329 26.04.2023 26.04.2028 (management level)"
        )
        self.assertEqual(endorsements["cert_medical_care"], "present")
        self.assertEqual(endorsements["cert_pscrb"], "present")
        self.assertEqual(endorsements["cert_sso"], "present")
        self.assertEqual(endorsements["cert_arpa"], "present")
        self.assertEqual(endorsements["cert_erm"], "present")


if __name__ == "__main__":
    unittest.main()
