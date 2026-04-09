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


REAL_RESUME_DOB_SAMPLES = [
    {
        "filename": "2nd_Engineer_120969.pdf",
        "text": "Personal Details\nDate of Birth : 04-Jan-1971\nNationality: Indian",
        "expected_dob": date(1971, 1, 4),
        "expected_age_on_2026_04_02": 55,
    },
    {
        "filename": "2nd_Engineer_17698.pdf",
        "text": "BIO DATA\nDOB 03-Feb-1974\nPassport No: X123456",
        "expected_dob": date(1974, 2, 3),
        "expected_age_on_2026_04_02": 52,
    },
    {
        "filename": "2nd_Engineer_288.pdf",
        "text": "Date of Birth 24-Dec-1965\nCurrent Rank: 2/E",
        "expected_dob": date(1965, 12, 24),
        "expected_age_on_2026_04_02": 60,
    },
    {
        "filename": "2nd_Engineer_315781.pdf",
        "text": "D.O.B. : 26-Feb-1995\nMarital Status: Single",
        "expected_dob": date(1995, 2, 26),
        "expected_age_on_2026_04_02": 31,
    },
    {
        "filename": "2nd_Engineer_349740.pdf",
        "text": "Date of Birth: 04-Nov-1989\nPlace of birth: Kerala",
        "expected_dob": date(1989, 11, 4),
        "expected_age_on_2026_04_02": 36,
    },
]


class AIAnalyzerDobParsingTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_real_resume_samples_parse_to_expected_dates_and_ages(self):
        reference_date = date(2026, 4, 2)

        for sample in REAL_RESUME_DOB_SAMPLES:
            with self.subTest(filename=sample["filename"]):
                dob_fact = self.analyzer._extract_dob_fact_from_text(sample["text"])
                self.assertEqual(dob_fact["status"], "PARSED")
                self.assertEqual(dob_fact["dob"], sample["expected_dob"])
                self.assertEqual(
                    self.analyzer._calculate_age(dob_fact["dob"], reference_date=reference_date),
                    sample["expected_age_on_2026_04_02"],
                )

    def test_supported_unambiguous_labeled_formats(self):
        cases = [
            ("Date of Birth: 1995-02-26", date(1995, 2, 26)),
            ("DOB 26 February 1995", date(1995, 2, 26)),
            ("D.O.B  February 26 1995", date(1995, 2, 26)),
            ("Date of Birth 26/Feb/1995", date(1995, 2, 26)),
        ]

        for raw_text, expected in cases:
            with self.subTest(raw_text=raw_text):
                dob_fact = self.analyzer._extract_dob_fact_from_text(raw_text)
                self.assertEqual(dob_fact["status"], "PARSED")
                self.assertEqual(dob_fact["dob"], expected)

    def test_ambiguous_numeric_labeled_formats_are_marked_unknown(self):
        cases = [
            "Date of Birth: 04/11/1989",
            "DOB 03-02-1974",
            "D.O.B. 11.04.89",
        ]

        for raw_text in cases:
            with self.subTest(raw_text=raw_text):
                dob_fact = self.analyzer._extract_dob_fact_from_text(raw_text)
                self.assertEqual(dob_fact["status"], "AMBIGUOUS_NUMERIC")
                self.assertIsNone(dob_fact["dob"])

    def test_unlabeled_dates_remain_unknown(self):
        raw_text = "CDC issued 2024-08-17\nPassport expiry 04-Nov-2029\nJoined vessel 12-Jan-2023"
        dob_fact = self.analyzer._extract_dob_fact_from_text(raw_text)
        self.assertEqual(dob_fact["status"], "MISSING")
        self.assertIsNone(dob_fact["dob"])

    def test_age_rule_surfaces_ambiguous_dob_as_unknown(self):
        candidate_facts = {
            "personal": {"dob": None, "dob_parse_status": "AMBIGUOUS_NUMERIC"},
            "derived": {"age_years": None},
        }
        result = self.analyzer._evaluate_age_rule(
            candidate_facts,
            {"min_age": 30, "max_age": 50},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["reason_code"], "AGE_DOB_AMBIGUOUS_FORMAT")
        self.assertIn("ambiguous numeric format", result["message"])

    def test_calculate_age_handles_exact_birthday_boundary(self):
        dob = date(1989, 11, 4)
        self.assertEqual(
            self.analyzer._calculate_age(dob, reference_date=date(2026, 11, 3)),
            36,
        )
        self.assertEqual(
            self.analyzer._calculate_age(dob, reference_date=date(2026, 11, 4)),
            37,
        )

    def test_resolved_candidate_age_extracts_explicit_stated_age(self):
        raw_text = "Date of Birth: 04-Nov-1989\nAge: 36\nNationality: Indian"
        age_info = self.analyzer._resolve_candidate_age(
            [{"metadata": {"raw_text": raw_text}}],
            original_path=None,
            text_cache={},
        )
        self.assertEqual(age_info["dob"], date(1989, 11, 4))
        self.assertEqual(age_info["age"], self.analyzer._calculate_age(date(1989, 11, 4)))
        self.assertEqual(age_info["stated_age"], 36)
        self.assertEqual(age_info["stated_age_status"], "PARSED")


if __name__ == "__main__":
    unittest.main()
