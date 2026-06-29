import sys
import types
import unittest
from datetime import date
from unittest.mock import patch


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
from candidate_facts.extractors import seajobs  # noqa: E402


class AIAnalyzerLogisticsTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.reference_date = date(2026, 4, 6)

    def test_extract_logistics_valid_visa(self):
        fact = self.analyzer._extract_logistics_from_text(
            "Visa: Schengen visa Expiry: 04-May-2028",
            reference_date=self.reference_date,
        )
        self.assertTrue(fact["us_visa_valid"])
        self.assertEqual(fact["us_visa_status"], "Schengen")
        self.assertEqual(fact["us_visa_expiry_date"], date(2028, 5, 4))

    def test_extract_logistics_expired_visa(self):
        fact = self.analyzer._extract_logistics_from_text(
            "Visa: Schengen visa Expiry: 04-May-2020",
            reference_date=self.reference_date,
        )
        self.assertFalse(fact["us_visa_valid"])
        self.assertEqual(fact["us_visa_status"], "Schengen")
        self.assertIsNone(fact["us_visa_expiry_date"])

    def test_extract_logistics_visa_absent(self):
        fact = self.analyzer._extract_logistics_from_text(
            "Passport Details only. No visa listed.",
            reference_date=self.reference_date,
        )
        self.assertIsNone(fact["us_visa_valid"])
        self.assertIsNone(fact["us_visa_status"])

    def test_extract_logistics_passport_expiry_variants(self):
        cases = [
            ("Passport Expiry: 2029-11-04", date(2029, 11, 4)),
            ("Passport expiry 04-Nov-2029", date(2029, 11, 4)),
            ("Passport Expiry: November 4, 2029", date(2029, 11, 4)),
        ]
        for raw_text, expected_date in cases:
            with self.subTest(raw_text=raw_text):
                fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
                self.assertEqual(fact["passport_expiry_date"], expected_date)
                self.assertEqual(fact["passport_expiry_status"], "PARSED")
                self.assertTrue(fact["passport_valid"])

    def test_extract_logistics_passport_absent(self):
        fact = self.analyzer._extract_logistics_from_text(
            "No passport details on file.",
            reference_date=self.reference_date,
        )
        self.assertIsNone(fact["passport_expiry_date"])
        self.assertEqual(fact["passport_expiry_status"], "MISSING")
        self.assertIsNone(fact["passport_valid"])

    def test_extract_logistics_passport_table_uses_second_date_as_expiry(self):
        raw_text = (
            "Document Number Place of Issue Date of Issue Validity "
            "Passport Z8030880 Mumbai 07/10/2024 06/10/2034 "
            "US VISA 20220566560005 Mumbai 01/03/2021 24/02/2027"
        )
        fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertEqual(fact["passport_expiry_date"], date(2034, 10, 6))
        self.assertEqual(fact["passport_expiry_status"], "PARSED")
        self.assertTrue(fact["passport_valid"])

    def test_extract_logistics_visa_table_uses_second_date_as_expiry(self):
        raw_text = (
            "PASSPORT ZA282236 31/10/2025 MUMBAI 30/10/2035 "
            "US VISA 20223044630023 13/12/2022 MUMBAI 11/12/2027 "
            "COC IF36205 20/02/2017 MUMBAI 10/03/2031"
        )
        fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertTrue(fact["us_visa_valid"])
        self.assertEqual(fact["us_visa_status"], "US Visa (USA)")
        self.assertEqual(fact["us_visa_expiry_date"], date(2027, 12, 11))

    def test_extract_logistics_availability_window_marks_immediate_when_today_inside(self):
        raw_text = (
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer "
            "From date - Till date 30-Mar-2026 - 30-Apr-2026 Personal & Contact Details"
        )
        fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertEqual(fact["availability_date"], date(2026, 3, 30))
        self.assertEqual(fact["availability_end_date"], date(2026, 4, 30))
        self.assertEqual(fact["availability_status"], "immediately")

    def test_extract_logistics_availability_window_parses_future_range(self):
        raw_text = (
            "Availability Details Applied For Rank Chief Officer Present Rank Chief Officer "
            "From date - Till date 15-May-2026 - 15-Jun-2026 Personal & Contact Details"
        )
        fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertEqual(fact["availability_date"], date(2026, 5, 15))
        self.assertEqual(fact["availability_end_date"], date(2026, 6, 15))
        self.assertEqual(fact["availability_status"], "PARSED")

    def test_extract_logistics_availability_window_single_date_keeps_immediate_and_threshold_confidence(self):
        raw_text = (
            "Availability Details Applied For Rank Chief Officer Present Rank Chief Officer "
            "From date - Till date 30-Mar-2026 Personal & Contact Details"
        )
        fact = self.analyzer._extract_availability_fact_from_text(raw_text, reference_date=self.reference_date)
        self.assertEqual(fact["availability_date"], date(2026, 3, 30))
        self.assertIsNone(fact["availability_end_date"])
        self.assertEqual(fact["availability_status"], "immediately")
        self.assertEqual(fact["confidence"], 0.85)

    def test_availability_v1_extracts_seajobs_window(self):
        raw_text = (
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer "
            "From date - Till date 30-Apr-2026 - 01-May-2026 Personal & Contact Details"
        )
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            raw_text,
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(fact["version"], "v1")
        self.assertEqual(fact["availability_date"], date(2026, 4, 30))
        self.assertEqual(fact["availability_end_date"], date(2026, 5, 1))
        self.assertEqual(fact["extraction_state"], "PARSED")
        self.assertEqual(fact["availability_source_label"], "availability_details")
        self.assertEqual(fact["availability_extracted_on_date"], self.reference_date)

    def test_availability_v1_extracts_single_available_from_date(self):
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            "Date of availability: Available from 15-May-2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(fact["availability_date"], date(2026, 5, 15))
        self.assertIsNone(fact["availability_end_date"])
        self.assertEqual(fact["extraction_state"], "PARSED")

    def test_availability_v1_extracts_available_until_as_window_ending(self):
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            "Candidate Available until 15-Jun-2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(fact["availability_date"], self.reference_date)
        self.assertEqual(fact["availability_end_date"], date(2026, 6, 15))
        self.assertEqual(fact["extraction_state"], "PARSED")

    def test_availability_v1_extracts_immediate_and_notice_period(self):
        immediate = self.analyzer._extract_availability_fact_v1_from_text(
            "Availability: Immediate",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(immediate["availability_date"], self.reference_date)
        self.assertEqual(immediate["extraction_state"], "PARSED")

        immediate_phrases = [
            "Immediately available",
            "Ready to join",
            "Join ASAP",
        ]
        for phrase in immediate_phrases:
            with self.subTest(phrase=phrase):
                phrase_fact = self.analyzer._extract_availability_fact_v1_from_text(
                    phrase,
                    availability_extracted_on_date=self.reference_date,
                )
                self.assertEqual(phrase_fact["availability_date"], self.reference_date)
                self.assertEqual(phrase_fact["extraction_state"], "PARSED")

        notice = self.analyzer._extract_availability_fact_v1_from_text(
            "Notice period: 30 days",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(notice["availability_date"], date(2026, 5, 6))
        self.assertEqual(notice["availability_source_label"], "notice_period")
        self.assertEqual(notice["extraction_state"], "PARSED")

        can_join = self.analyzer._extract_availability_fact_v1_from_text(
            "Can join in 45 days",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(can_join["availability_date"], date(2026, 5, 21))
        self.assertEqual(can_join["availability_source_label"], "notice_period")

    def test_availability_v1_extracts_contract_end_and_future_sign_off(self):
        contract_end = self.analyzer._extract_availability_fact_v1_from_text(
            "Current contract ends 20-Aug-2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(contract_end["availability_date"], date(2026, 8, 20))
        self.assertEqual(contract_end["availability_source_label"], "contract_end")
        self.assertEqual(contract_end["extraction_state"], "PARSED")

        sign_off = self.analyzer._extract_availability_fact_v1_from_text(
            "Sign-off date 20-Aug-2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(sign_off["availability_date"], date(2026, 8, 20))
        self.assertEqual(sign_off["availability_source_label"], "sign_off")
        self.assertEqual(sign_off["extraction_state"], "PARSED")

    def test_availability_v1_marks_ambiguous_numeric_dates(self):
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            "Available from 03/04/2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(fact["availability_date"], None)
        self.assertEqual(fact["extraction_state"], "AMBIGUOUS_NUMERIC")

    def test_availability_v1_disambiguates_numeric_when_one_position_over_12(self):
        day_first = self.analyzer._extract_availability_fact_v1_from_text(
            "Available from 13/04/2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(day_first["availability_date"], date(2026, 4, 13))
        self.assertEqual(day_first["extraction_state"], "PARSED")

        month_first = self.analyzer._extract_availability_fact_v1_from_text(
            "Available from 04/13/2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(month_first["availability_date"], date(2026, 4, 13))
        self.assertEqual(month_first["extraction_state"], "PARSED")

    def test_availability_v1_marks_non_overlapping_sources_contradictory(self):
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            "Availability Details From date - Till date 01-Jan-2026 - 01-Mar-2026 "
            "Contract ends 20-Aug-2026",
            availability_extracted_on_date=self.reference_date,
        )
        self.assertEqual(fact["extraction_state"], "CONTRADICTORY")
        self.assertIsNone(fact["availability_date"])

    def test_availability_v1_compatible_lower_precedence_is_dropped(self):
        extracted_on = date(2025, 12, 15)
        fact = self.analyzer._extract_availability_fact_v1_from_text(
            "Availability Details From date - Till date 01-Jan-2026 - 01-Mar-2026 "
            "Notice period: 30 days",
            availability_extracted_on_date=extracted_on,
        )
        self.assertEqual(fact["availability_date"], date(2026, 1, 1))
        self.assertEqual(fact["availability_end_date"], date(2026, 3, 1))
        self.assertEqual(fact["availability_source_label"], "availability_details")
        self.assertEqual(fact["extraction_state"], "PARSED")

    def test_logistics_v1_dual_write_is_opt_in_and_keeps_legacy_fields(self):
        raw_text = (
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer "
            "From date - Till date 30-Apr-2026 - 01-May-2026 Personal & Contact Details"
        )
        default_fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertIsNone(default_fact["availability_v1_fact"])
        self.assertEqual(default_fact["availability_date"], date(2026, 4, 30))
        self.assertEqual(default_fact["availability_status"], "PARSED")

        with patch.dict("os.environ", {"NJORDHR_AVAILABILITY_EXTRACTION_MODE": "v1"}):
            v1_fact = self.analyzer._extract_logistics_from_text(raw_text, reference_date=self.reference_date)
        self.assertEqual(v1_fact["availability_date"], date(2026, 4, 30))
        self.assertEqual(v1_fact["availability_status"], "PARSED")
        self.assertEqual(v1_fact["availability_v1_fact"]["version"], "v1")
        self.assertEqual(v1_fact["availability_v1_fact"]["availability_date"], date(2026, 4, 30))

    def test_extract_last_sign_off_fact_handles_multiline_split_seajobs_dates(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "2nd SNP Shipmanagement Pvt. Ltd. / Bulk 24-May-\n"
            "1 43158 MAN & B&W 29-Oct-2025\n"
            "Engineer Carrier 2025\n"
            "01-Jul-\n"
            "2 3rd Engineer Synergy Maritime Ltd. / Bulk Carrier 32837 MAN & B&W 06-Jan-2025\n"
            "2024\n"
        )
        fact = self.analyzer._extract_last_sign_off_fact_from_text(
            raw_text,
            original_path="/tmp/2nd_Engineer_349740.pdf",
            reference_date=self.reference_date,
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["last_sign_off_date"], date(2025, 10, 29))
        self.assertEqual(fact["last_sign_off_months_ago"], 5)

    def test_extract_ordered_date_tokens_from_seajobs_row_rebuilds_split_dates(self):
        row_lines = [
            "2nd Jubilant Ship management pvt L / Oil/Chem MAN B&W 14-Sep- 06-Dec-",
            "1 45000",
            "Engineer Tanker SMC 2024 2024",
        ]
        tokens = self.analyzer._extract_ordered_date_tokens_from_seajobs_row(row_lines)
        self.assertEqual(tokens, ["14-Sep-2024", "06-Dec-2024"])

    def test_extract_seajobs_experience_rows_parses_rank_and_dates_from_multiline_window(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "2nd Jubilant Ship management pvt L / Oil/Chem MAN B&W 14-Sep- 06-Dec-\n"
            "1 45000\n"
            "Engineer Tanker SMC 2024 2024\n"
            "2nd HMS maritime services pvt Ltd / Crude Oil MAN B&W 04-Oct- 20-Dec-\n"
            "2 30000\n"
            "Engineer Tanker SMC 2023 2023\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_engineer")
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2024, 9, 14))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2024, 12, 6))
        self.assertEqual(fact["rows"][0]["vessel_types"], ["tanker"])

    def test_extract_row_ship_types_from_seajobs_row_uses_real_container_shape(self):
        row_lines = [
            "2nd MAN B&W 03-Mar-",
            "4 ALTITUDE MARINE / Container Vessel 12000 06-Jun-2024",
            "Engineer SMC 2024",
        ]
        self.assertEqual(
            self.analyzer._extract_row_ship_types_from_seajobs_row(row_lines),
            ["container"],
        )

    def test_extract_row_ship_types_from_seajobs_row_handles_fragmented_bulk_carrier(self):
        row_lines = [
            "Chief dockendale ship management / Bulk Electronic Eng 05-Oct- 31-Mar-",
            "1 34657 Engineer Carrier B&W 2024 2025",
        ]
        self.assertEqual(
            self.analyzer._extract_row_ship_types_from_seajobs_row(row_lines),
            ["bulk carrier"],
        )

    def test_build_candidate_facts_extracts_seajobs_candidate_name(self):
        source_text = (
            "https://www .seajob.net\n"
            "Download by Njorships Management India Pvt Ltd\n"
            "Availability Details\n"
            "Applied For Rank Electrical Of ficer\n"
            "Present Rank Electrical Engineer\n"
            "Personal & Contact Details\n"
            "Name Ashish Kumar\n"
            "Email Address aditi.ashish30@gmail.com\n"
            "Date Of Birth 26-Oct-1983\n"
        )
        facts = self.analyzer._build_candidate_facts(
            "resume-test",
            "Electrical Officer",
            [{"metadata": {"raw_text": source_text}}],
            original_path="/tmp/resume-test.pdf",
            text_cache={"/tmp/resume-test.pdf": source_text},
            folder_metadata={},
        )
        self.assertEqual(facts["identity"]["full_name"], "Ashish Kumar")
        self.assertEqual(facts["identity"]["full_name_snippet"], "Name Ashish Kumar")

        payload = seajobs.build_candidate_facts_v1(
            self.analyzer,
            "resume-test",
            "Electrical Officer",
            [{"metadata": {"raw_text": source_text}}],
            original_path="/tmp/resume-test.pdf",
            text_cache={"/tmp/resume-test.pdf": source_text},
            folder_metadata={},
        )
        self.assertEqual(payload["identity"]["candidate_name"]["value"], "Ashish Kumar")
        self.assertEqual(payload["identity"]["candidate_name"]["snippet"], "Name Ashish Kumar")
        self.assertIn("vessel_types", payload["experience"])

    def test_extract_seajobs_experience_rows_includes_engine_types(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "1 2nd Engineer Synergy Maritime / Oil Tanker 35973 Dual Fuel (X-DF) ENGINE 09-Jan-2024 03-Apr-2024\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["rows"][0]["engine_types"], ["wingd_x_df", "dual_fuel"])
        self.assertEqual(fact["rows"][0]["engine_family"], "wingd_x_df")
        self.assertEqual(fact["rows"][0]["engine_details"][0]["manufacturer"], "WinGD")
        self.assertEqual(fact["rows"][0]["engine_details"][0]["fuel_family"], "lng_gas")
        self.assertTrue(fact["rows"][0]["engine_details"][0]["dual_fuel"])

    def test_extract_seajobs_experience_rows_handles_engine_model_spellings(self):
        cases = [
            ("RTFlex ENGINE", "wartsila_rt_flex", "Wartsila/Sulzer", "electronic_common_rail"),
            ("RT-flex96C ENGINE", "wartsila_rt_flex", "Wartsila/Sulzer", "electronic_common_rail"),
            ("12RTA96C ENGINE", "wartsila_rta", "Wartsila/Sulzer", "mechanical"),
            ("6S50MC-C ENGINE", "man_b_w_mc", "MAN B&W", "mechanical"),
            ("6S60ME-C10.5 ENGINE", "man_b_w_me_c", "MAN B&W", "electronic"),
            ("6S60ME-C8.2-GI ENGINE", "man_b_w_me_c_gi", "MAN B&W", "electronic"),
            ("7G70ME-C9.6-LGIM ENGINE", "man_b_w_me_lgim", "MAN B&W", "electronic"),
            ("10X92DF-2.0 ENGINE", "wingd_x_df", "WinGD", "electronic"),
        ]
        for engine_text, expected_engine_type, expected_manufacturer, expected_control_type in cases:
            with self.subTest(engine_text=engine_text):
                raw_text = (
                    "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
                    "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
                    "Seamen Experience Details\n"
                    "Sign In Sign Out\n"
                    "# Rank Company Name / Ship Type Tonnage Engine\n"
                    "Date Date\n"
                    f"1 2nd Engineer Synergy Maritime / Oil Tanker 35973 {engine_text} 09-Jan-2024 03-Apr-2024\n"
                )
                fact = self.analyzer._extract_seajobs_experience_rows(
                    raw_text,
                    original_path="/tmp/2nd_Engineer_288.pdf",
                )
                row = fact["rows"][0]
                self.assertEqual(row["engine_types"], [expected_engine_type])
                self.assertEqual(row["engine_family"], expected_engine_type)
                self.assertEqual(row["engine_details"][0]["manufacturer"], expected_manufacturer)
                self.assertEqual(row["engine_details"][0]["control_type"], expected_control_type)

    def test_meo_class_ii_certificate_does_not_match_me_engine_alias(self):
        details = self.analyzer._extract_engine_details_from_text(
            "MEO Class II (Motor) Indian 11-Mar-2024"
        )
        self.assertEqual(details, [])

    def test_extract_engine_details_maps_generic_man_b_w_mentions_to_generic_family(self):
        details = self.analyzer._extract_engine_details_from_text("B&W")
        self.assertEqual([detail["engine_type"] for detail in details], ["man_b_w"])

        details = self.analyzer._extract_engine_details_from_text("MAN & B&W")
        self.assertEqual([detail["engine_type"] for detail in details], ["man_b_w"])

    def test_extract_engine_details_handles_engine_map_regressions(self):
        cases = [
            ("Everllence B&W", ["man_b_w"]),
            ("MAN B&W LMC", ["man_b_w_mc"]),
            ("B&W Mc", ["man_b_w_mc"]),
            ("X92DF-HP", ["wingd_x_df_hp"]),
            ("X-DF-P", []),
            ("X-DF-E", []),
        ]
        for raw_text, expected in cases:
            with self.subTest(raw_text=raw_text):
                details = self.analyzer._extract_engine_details_from_text(raw_text)
                self.assertEqual([detail["engine_type"] for detail in details], expected)

    def test_extract_engine_details_skips_negated_engine_mentions(self):
        cases = [
            "never operated ME-GI engines",
            "without RT flex experience",
            "no methanol engine background",
        ]
        for raw_text in cases:
            with self.subTest(raw_text=raw_text):
                details = self.analyzer._extract_engine_details_from_text(raw_text)
                self.assertEqual(details, [])

    def test_extract_engine_details_does_not_cross_sentence_boundary_for_negation(self):
        cases = [
            ("Held no formal certifications. Operated ME-GI engines for 18 months.", "man_b_w_me_gi"),
            ("No prior training noted.\nWorked on RT-flex engines during last contract.", "wartsila_rt_flex"),
            ("No experience\nMAN B&W", "man_b_w"),
            ("never operated MC : also operated ME-GI", "man_b_w_me_gi"),
        ]
        for raw_text, expected_engine in cases:
            with self.subTest(raw_text=raw_text):
                details = self.analyzer._extract_engine_details_from_text(raw_text)
                self.assertEqual([detail["engine_type"] for detail in details], [expected_engine])

    def test_extract_engine_details_respects_compact_alias_negation_and_sentence_boundaries(self):
        negated = self.analyzer._extract_engine_details_from_text("no manb&w engine experience")
        self.assertEqual(negated, [])

        positive = self.analyzer._extract_engine_details_from_text("manb&w")
        self.assertEqual([detail["engine_type"] for detail in positive], ["man_b_w"])

    def test_extract_engine_details_handles_negated_and_positive_repeat_mentions(self):
        cases = [
            ("NO ME-GI. Operated ME-GI.", "man_b_w_me_gi"),
            ("no man b&w engine. then MAN B&W", "man_b_w"),
        ]
        for raw_text, expected_engine in cases:
            with self.subTest(raw_text=raw_text):
                details = self.analyzer._extract_engine_details_from_text(raw_text)
                self.assertEqual([detail["engine_type"] for detail in details], [expected_engine])

    def test_extract_engine_experience_constraint_prefers_specific_subtype_over_generic_bucket(self):
        constraint = self.analyzer._extract_engine_experience_constraint(
            "has ME-LGIM methanol engine experience"
        )
        self.assertIsNotNone(constraint)
        self.assertEqual(constraint["engine_type"], "man_b_w_me_lgim")

    def test_extract_engine_experience_constraint_uses_generic_bucket_when_no_specific_subtype_exists(self):
        methanol_constraint = self.analyzer._extract_engine_experience_constraint(
            "has methanol engine experience"
        )
        self.assertIsNotNone(methanol_constraint)
        self.assertEqual(methanol_constraint["engine_type"], "methanol_engine")

        ammonia_constraint = self.analyzer._extract_engine_experience_constraint(
            "has ammonia engine experience"
        )
        self.assertIsNotNone(ammonia_constraint)
        self.assertEqual(ammonia_constraint["engine_type"], "ammonia_engine")

    def test_extract_seajobs_experience_rows_captures_generic_engine_brands(self):
        cases = [
            ("Wartsila", "wartsila"),
            ("Caterpillar", "caterpillar"),
            ("Caterpilliar", "caterpillar"),
            ("Mitsui", "mitsui"),
            ("MaK", "mak"),
            ("Pielstick", "pielstick"),
            ("Nohab", "nohab"),
            ("Gotaverken", "gotaverken"),
            ("Sulzer", "sulzer"),
            ("Mitsubishi", "mitsubishi"),
            ("Yanmar", "yanmar"),
            ("Bergen", "bergen"),
        ]
        for engine_text, expected_engine_type in cases:
            with self.subTest(engine_text=engine_text):
                raw_text = (
                    "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
                    "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
                    "Seamen Experience Details\n"
                    "Sign In Sign Out\n"
                    "# Rank Company Name / Ship Type Tonnage Engine\n"
                    "Date Date\n"
                    f"1 2nd Engineer Synergy Maritime / Oil Tanker 35973 {engine_text} 09-Jan-2024 03-Apr-2024\n"
                )
                fact = self.analyzer._extract_seajobs_experience_rows(
                    raw_text,
                    original_path="/tmp/2nd_Engineer_288.pdf",
                )
                row = fact["rows"][0]
                self.assertEqual(row["engine_types"], [expected_engine_type])
                self.assertEqual(row["engine_family"], expected_engine_type)

    def test_extract_seajobs_experience_rows_handles_empty_engine_column(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "1 2nd Engineer Synergy Maritime / Oil Tanker 35973 09-Jan-2024 03-Apr-2024\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        row = fact["rows"][0]
        self.assertEqual(row["engine_types"], [])
        self.assertIsNone(row["engine_family"])
        self.assertEqual(row["engine_details"], [])

    def test_seajobs_tonnage_clean_integer(self):
        self.assertEqual(
            self.analyzer._parse_vessel_tonnage_cell("58000"),
            [{"value": 58000, "unit": "unspecified", "source_label": "Tonnage", "confidence": 0.90, "evidence_text": "58000"}],
        )

    def test_seajobs_tonnage_comma_separated(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58,000")[0]["value"], 58000)

    def test_seajobs_tonnage_decimal_zero(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58000.0")[0]["value"], 58000)

    def test_seajobs_tonnage_non_whole_decimal(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58000.5"), [])

    def test_seajobs_tonnage_shorthand_rejected(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58k"), [])

    def test_seajobs_tonnage_zero_is_missing(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("0"), [])

    def test_seajobs_tonnage_dash_sentinels(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("-"), [])
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("--"), [])

    def test_seajobs_tonnage_na_sentinels(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("NA"), [])
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("N/A"), [])

    def test_seajobs_tonnage_question_sentinel(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("?"), [])

    def test_seajobs_tonnage_null_string(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("null"), [])
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("none"), [])

    def test_seajobs_tonnage_whitespace_only(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("   \n\t"), [])

    def test_seajobs_tonnage_noisy_with_unit(self):
        entries = self.analyzer._parse_vessel_tonnage_cell("58000 MT")
        self.assertEqual(entries[0]["value"], 58000)
        self.assertEqual(entries[0]["unit"], "unspecified")
        self.assertEqual(entries[0]["confidence"], 0.70)

    def test_seajobs_tonnage_label_prefix(self):
        entries = self.analyzer._parse_vessel_tonnage_cell("Tonnage 58000")
        self.assertEqual(entries[0]["value"], 58000)
        self.assertEqual(entries[0]["confidence"], 0.70)

    def test_seajobs_tonnage_dwt_labeled(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("105000 DWT")[0]["unit"], "dwt")

    def test_seajobs_tonnage_gt_labeled(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58000 GT")[0]["unit"], "gt")

    def test_seajobs_tonnage_grt_labeled(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58000 GRT")[0]["unit"], "grt")

    def test_seajobs_tonnage_multi_labeled_split(self):
        entries = self.analyzer._parse_vessel_tonnage_cell("58000 GT / 105000 DWT")
        self.assertEqual(entries, [
            {"value": 58000, "unit": "gt", "source_label": "GT", "confidence": 0.90, "evidence_text": "58000 GT / 105000 DWT"},
            {"value": 105000, "unit": "dwt", "source_label": "DWT", "confidence": 0.90, "evidence_text": "58000 GT / 105000 DWT"},
        ])

    def test_seajobs_tonnage_multi_unlabeled_skip(self):
        self.assertEqual(self.analyzer._parse_vessel_tonnage_cell("58000 60000"), [])

    def test_seajobs_tonnage_attaches_to_row_without_engine_model_false_positives(self):
        for engine_text in ("6S60ME-C10.5", "RT-flex96C", "12RTA96C", "6S50MC-C"):
            with self.subTest(engine_text=engine_text):
                raw_text = (
                    "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
                    "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
                    "Seamen Experience Details\n"
                    "Sign In Sign Out\n"
                    "# Rank Company Name / Ship Type Tonnage Engine\n"
                    "Date Date\n"
                    f"1 2nd Engineer Synergy Maritime / Oil Tanker 58,000 {engine_text} ENGINE 09-Jan-2024 03-Apr-2024\n"
                )
                fact = self.analyzer._extract_seajobs_experience_rows(
                    raw_text,
                    original_path="/tmp/2nd_Engineer_288.pdf",
                )
                row = fact["rows"][0]
                self.assertEqual(row["vessel_tonnage"][0]["value"], 58000)
                self.assertEqual(row["vessel_tonnage"][0]["unit"], "unspecified")
                self.assertEqual(row["vessel_tonnage"][0]["confidence"], 0.70)

    def test_seajobs_tonnage_strips_three_digit_row_numbers(self):
        entries = self.analyzer._extract_vessel_tonnage_from_seajobs_row(
            ["101 2nd Engineer Synergy Maritime / Oil Tanker 58,000 ENGINE 09-Jan-2024 03-Apr-2024"]
        )
        self.assertEqual(entries[0]["value"], 58000)

    def test_seajobs_tonnage_handles_split_date_fragments_around_row(self):
        entries = self.analyzer._extract_vessel_tonnage_from_seajobs_row([
            "2nd 03-Mar-",
            "1 ESM / Bitumen Tanker 10830 Wartsila 06-Jul-2025",
            "Engineer 2025",
        ])
        self.assertEqual(entries[0]["value"], 10830)
        self.assertEqual(entries[0]["unit"], "unspecified")

    def test_seajobs_tonnage_does_not_treat_split_year_as_grt_value(self):
        entries = self.analyzer._extract_vessel_tonnage_from_seajobs_row([
            "28434 18-Jun-",
            "6 3rd Engineer Synergy marine services / Oil Tanker MAN & B&W GRT 2021",
        ])
        self.assertEqual(entries, [])

    def test_extract_vessel_tonnage_constraint_minimum(self):
        constraint = self.analyzer._extract_vessel_tonnage_constraint("has experience on vessels above 50000 tonnage")
        self.assertEqual(constraint["min_value"], 50000)
        self.assertIsNone(constraint["max_value"])
        self.assertEqual(constraint["unit"], "any")

    def test_extract_vessel_tonnage_constraint_range_and_unit(self):
        constraint = self.analyzer._extract_vessel_tonnage_constraint("vessel tonnage between 30000 and 80000 GRT")
        self.assertEqual(constraint["min_value"], 30000)
        self.assertEqual(constraint["max_value"], 80000)
        self.assertEqual(constraint["unit"], "gt_grt")

    def test_extract_vessel_tonnage_constraint_dwt(self):
        constraint = self.analyzer._extract_vessel_tonnage_constraint("served on vessels above 100000 dwt")
        self.assertEqual(constraint["min_value"], 100000)
        self.assertEqual(constraint["unit"], "dwt")

    def test_extract_vessel_tonnage_constraint_maximum_grt(self):
        constraint = self.analyzer._extract_vessel_tonnage_constraint("up to 60000 grt")
        self.assertIsNone(constraint["min_value"])
        self.assertEqual(constraint["max_value"], 60000)
        self.assertEqual(constraint["unit"], "gt_grt")

    def test_extract_vessel_tonnage_constraint_bare_value(self):
        constraint = self.analyzer._extract_vessel_tonnage_constraint("50000 tonnage")
        self.assertEqual(constraint["min_value"], 50000)
        self.assertIsNone(constraint["max_value"])
        self.assertEqual(constraint["unit"], "any")

    def test_extract_vessel_tonnage_constraint_ignores_service_duration(self):
        self.assertIsNone(self.analyzer._extract_vessel_tonnage_constraint("minimum 12 months experience in oil tanker"))

    def test_extract_job_constraints_does_not_treat_tonnage_as_age(self):
        constraints = self.analyzer._extract_job_constraints("has experience on vessels above 50000 tonnage")
        self.assertIn("vessel_tonnage", constraints["hard_constraints"])
        self.assertNotIn("age_years", constraints["hard_constraints"])

    def test_extract_job_constraints_handles_age_and_tonnage_together(self):
        constraints = self.analyzer._extract_job_constraints(
            "above 50000 tonnage and between 30 and 50 years old"
        )
        self.assertEqual(
            constraints["hard_constraints"]["vessel_tonnage"],
            {"min_value": 50000, "max_value": None, "unit": "any", "display_value": "above 50000 tonnage"},
        )
        self.assertEqual(
            constraints["hard_constraints"]["age_years"],
            {"min_age": 30, "max_age": 50},
        )

    def test_extract_seajobs_experience_rows_preserves_multiple_engine_mentions(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "1 2nd Engineer Synergy Maritime / Oil Tanker 35973 ME-C engine and MC-C engine 09-Jan-2024 03-Apr-2024\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        row = fact["rows"][0]
        self.assertCountEqual(row["engine_types"], ["man_b_w_me_c", "man_b_w_mc"])
        self.assertIn(row["engine_family"], row["engine_types"])
        self.assertCountEqual(
            [detail["engine_family"] for detail in row["engine_details"]],
            ["man_b_w_me_c", "man_b_w_mc"],
        )

    def test_extract_email_experience_rows_parses_date_complete_table_rows(self):
        raw_text = (
            "SEA EXPERIENCE\n"
            "VESSEL TYPE OF GRT ENGINE ENGINE COMPANY RANK FROM TO\n"
            "NAME VESSEL TYPE BHP\n"
            "MT OCEAN OIL 61,653 B&W 19,150 NORTHPOLE 2EO 06.11.2024 25.04.2025\n"
            "FAYE TANKER 7S60MC MARINE\n"
            "MT DESH OIL 61,978 B&W 14,640 THE SCI LTD 2EO 26.08.2022 29.06.2023\n"
            "GAURAV TANKER 6S60MC\n"
            "DOCUMENTS\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["source_label"], "email_resume")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2024, 11, 6))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2025, 4, 25))
        self.assertEqual(fact["rows"][0]["vessel_types"], ["tanker"])
        self.assertEqual(fact["rows"][0]["engine_types"], ["man_b_w_mc"])
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_engineer")

    def test_extract_email_experience_rows_uses_continuation_lines_for_ship_type(self):
        raw_text = (
            "SEA EXPERIENCE:\n"
            "VesselName Company Name Vessel Type DWT Rank SignOn SignOff\n"
            "MT SCYLLA PRODUCT 03.09.2023 01.03.2024\n"
            "MARSHAL SHIPMANAGEMENT 74401 2O\n"
            "TANKER\n"
            "MV TAMILNADU SCI BULK CARRIER 45792 TNOC 12.03.2011 28.10.2011\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["vessel_types"], ["tanker"])
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_officer")
        self.assertEqual(fact["rows"][1]["vessel_types"], ["bulk carrier"])

    def test_extract_email_experience_rows_parses_service_record_two_digit_years(self):
        raw_text = (
            "SEA SERVICE RECORD:\n"
            "SHIP NAME COMPANY IMO TYPE GRT SIGN ON SIGN OFF RANK\n"
            "M.V. MAHA FIVE STAR SHIPPING 9231004 BULK 38731 24.04.19 31.12.19 CADET\n"
            "ROOS COMPANY PVT LTD\n"
            "M.V. MAHA FIVE STAR SHIPPING 9525613 BULK 45999 12.06.24 21.07.24 2ND\n"
            "YAYA COMPANY PVT LTD OFFICER\n"
            "DOCUMENT DETAILS\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2019, 4, 24))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2019, 12, 31))
        self.assertEqual(fact["rows"][0]["rank_normalized"], "deck_cadet")
        self.assertEqual(fact["rows"][1]["rank_normalized"], "2nd_officer")

    def test_extract_email_experience_rows_parses_split_date_range_rows(self):
        raw_text = (
            "Seamen Experience Details\n"
            "18/04/2025 - Norse ship management\n"
            "04/08/2025 2nd Engineer\n"
            "Raffles pride / Oil/Chem Tanker 8539GT MAN & B&W\n"
            "21/09/2024 - fleet management\n"
            "14/11/2024 2nd Engineer\n"
            "MT GISELE / Product Tanker 40953 MAN & B&W\n"
            "Academic Details\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2025, 4, 18))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2025, 8, 4))
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_engineer")
        self.assertEqual(fact["rows"][0]["vessel_types"], ["tanker"])
        self.assertEqual(fact["rows"][0]["engine_types"], ["man_b_w"])

    def test_extract_email_experience_rows_parses_to_delimited_table_rows(self):
        raw_text = (
            "Sea Service Details\n"
            "Vessel GRT/\n"
            "Company Vessel Rank Sea Time Sign Off Reason\n"
            "Type DWT\n"
            "Chellaram 26/01/2024\n"
            "M.V. Darya Bulk 35035 t/ Second\n"
            "Shipping Private To Finished Contract\n"
            "Rama Carrier 61212 t Officer\n"
            "Limited 11/08/2024\n"
            "M.V. LONG 04/08/2025\n"
            "54675 t/ Second\n"
            "PG Maritime BEACH Container To Finished Contract\n"
            "68618 t Officer\n"
            "EXPRESS 06/02/2026\n"
            "Documents Held\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 2)
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2024, 1, 26))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2024, 8, 11))
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_officer")
        self.assertEqual(fact["rows"][0]["vessel_types"], ["bulk carrier"])
        self.assertEqual(fact["rows"][1]["sign_in_date"], date(2025, 8, 4))
        self.assertEqual(fact["rows"][1]["sign_out_date"], date(2026, 2, 6))
        self.assertEqual(fact["rows"][1]["vessel_types"], ["container"])

    def test_extract_email_experience_rows_rebuilds_indexed_day_month_year_fragments(self):
        raw_text = (
            "Sea service Experience Details\n"
            "Sign Sign\n"
            "Company\n"
            "# Rank Tonnage Engine In Out\n"
            "Name / Ship\n"
            "/BHP Date Date\n"
            "Type & Name\n"
            "1 Holy angel Marine 81396/18 MAN 17-\n"
            "2nd Engineer 01-\n"
            "services/Crude oil 660kw B&W MC-CApr-2025\n"
            "Oct-202\n"
            "Tanker/MT LILIANA\n"
            "5\n"
            "Chief 12-\n"
            "SCI / 18-Jul-\n"
            "2 Engineer 427/2X6 Yanmar Feb-\n"
            "Passenger 2023\n"
            ",SCI 62 KW 2024\n"
            "Ship/MV RANI\n"
            "CHANGA\n"
            "Academic Details\n"
        )
        fact = self.analyzer._extract_seajobs_experience_rows(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["rows"][0]["sign_in_date"], date(2025, 4, 17))
        self.assertEqual(fact["rows"][0]["sign_out_date"], date(2025, 10, 1))
        self.assertEqual(fact["rows"][0]["rank_normalized"], "2nd_engineer")
        self.assertEqual(fact["rows"][0]["vessel_types"], ["tanker"])
        self.assertEqual(fact["rows"][0]["engine_types"], ["man_b_w_mc"])
        self.assertEqual(fact["rows"][1]["sign_in_date"], date(2023, 7, 18))
        self.assertEqual(fact["rows"][1]["sign_out_date"], date(2024, 2, 12))
        self.assertEqual(fact["rows"][1]["rank_normalized"], "chief_engineer")
        self.assertEqual(fact["rows"][1]["engine_types"], ["yanmar"])

    def test_extract_rank_from_seajobs_row_window_handles_anchor_line_with_row_index(self):
        row_lines = [
            "02-Nov-",
            "1 2nd Officer Sygnius / Bulk Carrier 18-Jan-2025",
            "2024",
        ]
        fact = self.analyzer._extract_rank_from_seajobs_row_window(row_lines)
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["canonical_id"], "2nd_officer")

    def test_extract_rank_from_seajobs_row_window_handles_ocr_split_engine_rank(self):
        row_lines = [
            "1 2nd",
            "Engineer",
            "FLEET MANAGEMENT LTD /",
            "Oil/Chem Tanker 29256 Electronic Eng",
            "B&W",
            "27-Oct-",
            "2024",
            "21-Apr-",
            "2025",
        ]
        fact = self.analyzer._extract_rank_from_seajobs_row_window(row_lines)
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["canonical_id"], "2nd_engineer")

    def test_extract_rank_from_seajobs_row_window_rejects_trainee_context(self):
        row_lines = [
            "4 Trainee Electrical Officer",
            "FLEET MANAGEMENT LTD /",
            "Oil/Chem Tanker 29256 Electronic Eng",
            "B&W",
            "27-Oct-",
            "2024",
            "21-Apr-",
            "2025",
        ]
        fact = self.analyzer._extract_rank_from_seajobs_row_window(row_lines)
        self.assertEqual(fact["status"], "MISSING")
        self.assertIsNone(fact["canonical_id"])

    def test_extract_current_rank_months_fact_sums_matching_seajobs_rows(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "2nd Jubilant Ship management pvt L / Oil/Chem MAN B&W 14-Sep- 06-Dec-\n"
            "1 45000\n"
            "Engineer Tanker SMC 2024 2024\n"
            "2nd HMS maritime services pvt Ltd / Crude Oil MAN B&W 04-Oct- 20-Dec-\n"
            "2 30000\n"
            "Engineer Tanker SMC 2023 2023\n"
            "3rd Quadrant Maritime pvt Ltd / Container 04-Mar-\n"
            "3 1204\n"
            "Engineer 2011 08-Apr-2011\n"
        )
        fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["matched_rows"], 2)
        self.assertEqual(fact["months_total"], 4)

    def test_extract_current_rank_months_fact_ignores_trainee_rows(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank Electrical Officer Present Rank Electrical Officer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "4 Trainee Electrical Officer FLEET MANAGEMENT LTD / Oil/Chem Tanker 29256 Electronic Eng B&W 27-Oct- 2024 21-Apr- 2025\n"
            "5 Electrical Officer FLEET MANAGEMENT LTD / Oil/Chem Tanker 29256 Electronic Eng B&W 22-Apr- 2025 21-Oct- 2025\n"
        )
        fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/Electrical_Officer_Resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["matched_rows"], 1)
        self.assertEqual(fact["months_total"], 6)
        self.assertEqual(fact["source"], "service_rows")

    def test_extract_current_rank_months_fact_ignores_pre_sea_training_context(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank Electrical Officer Present Rank Electrical Officer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "4 Pre Sea Training Electrical Officer FLEET MANAGEMENT LTD / Oil/Chem Tanker 29256 Electronic Eng B&W 27-Oct- 2024 21-Apr- 2025\n"
            "5 Electrical Officer FLEET MANAGEMENT LTD / Oil/Chem Tanker 29256 Electronic Eng B&W 22-Apr- 2025 21-Oct- 2025\n"
        )
        fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/Electrical_Officer_Resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["matched_rows"], 1)
        self.assertEqual(fact["months_total"], 6)
        self.assertEqual(fact["source"], "service_rows")

    def test_extract_seajobs_total_experience_rows_skips_placeholder_rank_durations(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Total Experience\n"
            "Rank 2nd Engineer\n"
            "Experience YY Year 8 Month 22 Days\n"
            "Rank 4th Engineer\n"
            "Experience 2 Year 0 Month 22 Days\n"
            "Rank 5th Engineer\n"
            "Experience Year 7 Month 13 Days\n"
        )
        fact = self.analyzer._extract_seajobs_total_experience_rows(
            raw_text,
            original_path="/tmp/2nd_Engineer_315781.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(len(fact["rows"]), 1)
        self.assertEqual(fact["rows"][0]["rank_normalized"], "4th_engineer")
        self.assertEqual(fact["rows"][0]["months_total"], 24)
        self.assertEqual(fact["rows"][0]["days"], 22)

    def test_extract_current_rank_months_fact_keeps_service_row_sum_with_total_experience(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank Chief Officer Present Rank Chief Officer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "1 Chief Officer Sygnius / Bulk Carrier 18000 01-Jan-2025 31-Jan-2025\n"
            "Total Experience\n"
            "Rank Chief Officer\n"
            "Experience 04 Year 6 Month 20 Days\n"
        )
        fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/Chief-Officer_11617.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["months_total"], 1)
        self.assertEqual(fact["extraction_method"], "seajobs_service_history_rank_duration_sum")

    def test_extract_seajobs_total_experience_rows_and_current_rank_months_from_rank_history(self):
        raw_text = (
            "Download by : R Aditya (NJORSHIPS MANAGEMENT INDIA PVT LTD)\n"
            "Availability Details Applied For Rank Electrical Officer Present Rank Electrical Officer\n"
            "Seamen Experience Details\n"
            "Total Experience\n"
            "Rank Electrical Engineer\n"
            "Experience 15 Year 1 Month Days\n"
            "Rank Electrical Officer\n"
            "Experience Year 6 Month Days\n"
            "Rank Electrical Officer\n"
            "Experience Year 12 Month Days\n"
        )
        total_fact = self.analyzer._extract_seajobs_total_experience_rows(
            raw_text,
            original_path="/tmp/Electrical_Officer_Resume.pdf",
        )
        self.assertEqual(total_fact["status"], "PARSED")
        self.assertEqual(len(total_fact["rows"]), 2)
        self.assertEqual(total_fact["rows"][0]["rank_normalized"], "electrical_officer")
        self.assertEqual(total_fact["rows"][0]["months_total"], 6)
        self.assertEqual(total_fact["rows"][1]["months_total"], 12)

        months_fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/Electrical_Officer_Resume.pdf",
            experience_rows_fact={
                "rows": [],
                "status": "MISSING",
                "source_label": "seajobs_resume",
            },
            rank_duration_rows_fact=total_fact,
        )
        self.assertEqual(months_fact["status"], "PARSED")
        self.assertEqual(months_fact["months_total"], 18)
        self.assertEqual(months_fact["matched_rows"], 2)
        self.assertEqual(months_fact["source"], "rank_duration_rows")
        self.assertEqual(months_fact["extraction_method"], "seajobs_total_experience_rank_duration_sum")

    def test_extract_current_rank_months_fact_ignores_pre_sea_total_experience_rows(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank Electrical Officer Present Rank Electrical Officer\n"
            "Seamen Experience Details\n"
            "Total Experience\n"
            "Rank Pre Sea Training Electrical Officer\n"
            "Experience 04 Year 6 Month 20 Days\n"
            "Rank Electrical Officer\n"
            "Experience 01 Year 0 Month 00 Days\n"
        )
        fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/Electrical_Officer_Resume.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["matched_rows"], 1)
        self.assertEqual(fact["months_total"], 12)
        self.assertEqual(fact["source"], "rank_duration_rows")

    def test_extract_contract_gap_fact_flags_gap_over_six_months(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "2nd HMS maritime services pvt Ltd / Crude Oil MAN B&W 04-Oct- 20-Dec-\n"
            "2 30000\n"
            "Engineer Tanker SMC 2023 2023\n"
            "2nd Jubilant Ship management pvt L / Oil/Chem MAN B&W 14-Sep- 06-Dec-\n"
            "1 45000\n"
            "Engineer Tanker SMC 2024 2024\n"
        )
        fact = self.analyzer._extract_contract_gap_fact_from_text(
            raw_text,
            original_path="/tmp/2nd_Engineer_288.pdf",
        )
        self.assertEqual(fact["status"], "PARSED")
        self.assertTrue(fact["has_gap_over_6_months"])
        self.assertGreater(fact["max_gap_days"], 183)

    def test_default_insight_facts_exclude_email_resumes(self):
        raw_text = (
            "Download by : R Aditya (Njordships Management India Pvt Ltd)\n"
            "Availability Details Applied For Rank 2nd Engineer Present Rank 2nd Engineer\n"
            "Seamen Experience Details\n"
            "Sign In Sign Out\n"
            "# Rank Company Name / Ship Type Tonnage Engine\n"
            "Date Date\n"
            "2nd Jubilant Ship management pvt L / Oil/Chem MAN B&W 14-Sep- 06-Dec-\n"
            "1 45000\n"
            "Engineer Tanker SMC 2024 2024\n"
        )
        months_fact = self.analyzer._extract_current_rank_months_fact_from_text(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        gap_fact = self.analyzer._extract_contract_gap_fact_from_text(
            raw_text,
            original_path="/tmp/EMAIL_20260512_resume.pdf",
        )
        self.assertEqual(months_fact["status"], "SOURCE_EXCLUDED")
        self.assertEqual(gap_fact["status"], "SOURCE_EXCLUDED")


if __name__ == "__main__":
    unittest.main()
