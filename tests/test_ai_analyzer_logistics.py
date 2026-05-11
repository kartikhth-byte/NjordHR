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


if __name__ == "__main__":
    unittest.main()
