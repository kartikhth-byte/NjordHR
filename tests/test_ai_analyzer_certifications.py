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
        self.assertEqual(fact["expiry_date"], date(2030, 2, 27))
        self.assertEqual(fact["expiry_status"], "PARSED")

    def test_extract_coc_fact_from_certificate_table_row_without_explicit_coc_label(self):
        fact = self.analyzer._extract_coc_fact_from_text(
            "D10008212 Chief Officer(FG) Singapore 08-Apr-2025 04-Apr-2030 14HL9555"
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["grade"], "chief_officer")
        self.assertEqual(fact["expiry_date"], date(2030, 4, 4))
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


if __name__ == "__main__":
    unittest.main()
