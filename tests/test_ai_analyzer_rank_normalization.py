import sys
import types
import unittest


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


class AIAnalyzerRankNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_normalize_rank_exact_alias_match(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("2nd Engineer")
        self.assertEqual(canonical_id, "2nd_engineer")
        self.assertEqual(department, "engine")
        self.assertEqual(seniority_bucket, "senior_officer")
        self.assertEqual(confidence, 1.0)

    def test_normalize_rank_variant_alias(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("Second Engineer")
        self.assertEqual(canonical_id, "2nd_engineer")
        self.assertEqual(department, "engine")
        self.assertEqual(seniority_bucket, "senior_officer")
        self.assertEqual(confidence, 0.9)

    def test_normalize_rank_unrecognized_returns_unknown(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("Hotel Manager")
        self.assertIsNone(canonical_id)
        self.assertIsNone(department)
        self.assertIsNone(seniority_bucket)
        self.assertIsNone(confidence)

    def test_normalize_rank_department_split(self):
        deck_canonical, deck_department, _, _ = self.analyzer._normalize_rank("Chief Officer")
        engine_canonical, engine_department, _, _ = self.analyzer._normalize_rank("2nd Engineer")
        self.assertEqual(deck_canonical, "chief_officer")
        self.assertEqual(deck_department, "deck")
        self.assertEqual(engine_canonical, "2nd_engineer")
        self.assertEqual(engine_department, "engine")

    def test_normalize_rank_explicit_abbreviation_alias(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("2/E")
        self.assertEqual(canonical_id, "2nd_engineer")
        self.assertEqual(department, "engine")
        self.assertEqual(seniority_bucket, "senior_officer")
        self.assertEqual(confidence, 0.9)

    def test_normalize_rank_email_style_engineer_aliases(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("2nd ENGG")
        self.assertEqual(canonical_id, "2nd_engineer")
        self.assertEqual(department, "engine")
        self.assertEqual(seniority_bucket, "senior_officer")
        self.assertEqual(confidence, 0.9)

    def test_normalize_rank_new_aliases(self):
        cases = {
            "first mate": "chief_officer",
            "second mate": "2nd_officer",
            "third mate": "3rd_officer",
            "Mstr": "master",
            "Capt": "master",
            "skipper": "master",
            "old man": "master",
            "E T O": "electro_technical_officer",
            "chief eng": "chief_engineer",
            "engineer in charge": "chief_engineer",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank(label)
                self.assertEqual(canonical_id, expected)
                self.assertIsNotNone(department)
                self.assertIsNotNone(seniority_bucket)
                self.assertIn(confidence, (0.9, 1.0))

    def test_normalize_rank_ordinary_seaman_alias(self):
        canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank("Ordinary Seaman")
        self.assertEqual(canonical_id, "os")
        self.assertEqual(department, "deck")
        self.assertEqual(seniority_bucket, "rating")
        self.assertEqual(confidence, 0.9)

    def test_normalize_rank_ab_variants(self):
        for label in ["AB", "A/B", "Able Bodied Seaman", "Able Seaman"]:
            with self.subTest(label=label):
                canonical_id, department, seniority_bucket, confidence = self.analyzer._normalize_rank(label)
                self.assertEqual(canonical_id, "ab")
                self.assertEqual(department, "deck")
                self.assertEqual(seniority_bucket, "rating")
                self.assertIn(confidence, (0.9, 1.0))

    def test_extract_rank_fact_from_text_uses_labeled_rank(self):
        result = self.analyzer._extract_rank_fact_from_text("Present Rank: Chief Officer")
        self.assertEqual(result["raw_rank"], "Chief Officer")
        self.assertEqual(result["canonical_id"], "chief_officer")
        self.assertEqual(result["department"], "deck")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_from_inline_availability_header(self):
        text = (
            "Availability Details Applied For Rank Master Present Rank Master "
            "From date - Till date 01-Feb-2026 - 31-Mar-2026 Personal & Contact Details"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "Master")
        self.assertEqual(result["canonical_id"], "master")
        self.assertEqual(result["department"], "deck")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_prefers_present_rank_over_applied_for_rank(self):
        text = (
            "Availability Details Applied For Rank Master,Chief Officer "
            "Present Rank Chief Officer From date - Till date 15-May-2026 - 31-May-2026"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "Chief Officer")
        self.assertEqual(result["canonical_id"], "chief_officer")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_falls_back_to_applied_for_when_present_rank_is_unrecognized(self):
        text = (
            "Availability Details Applied For Rank 4th Engineer,Junior 4th Engineer,Engine Fitter "
            "Present Rank Engine Fitter From date - Till date 01-Oct-2025 - 01-Nov-2025"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "4th Engineer")
        self.assertEqual(result["canonical_id"], "4th_engineer")
        self.assertEqual(result["source_label"], "applied_for_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_falls_back_to_applied_for_junior_rank_when_present_rank_is_trainee(self):
        text = (
            "Availability Details Applied For Rank Junior 4th Engineer,5th Engineer,Junior Engineer "
            "Present Rank Trainee Cadet From date - Till date 05-Sep-2025 - 08-Sep-2025"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "Junior 4th Engineer")
        self.assertEqual(result["canonical_id"], "4th_engineer")
        self.assertEqual(result["source_label"], "applied_for_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_falls_back_to_post_applied_for_email_style_label(self):
        text = (
            "RESUME POST APPLIED FOR : O.S NAME : VASUPALLI SURESH "
            "DATE OF BIRTH : 24/04/2002 NATIONALITY : INDIAN"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "O.S")
        self.assertEqual(result["canonical_id"], "os")
        self.assertEqual(result["source_label"], "applied_for_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_falls_back_to_applied_for_without_rank_word(self):
        text = (
            "APPLIED FOR :- Second Engineer ACADEMIC PERFORMANCE: University Topper "
            "SEA SERVICE EXPERIENCE"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "Second Engineer")
        self.assertEqual(result["canonical_id"], "2nd_engineer")
        self.assertEqual(result["source_label"], "applied_for_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_falls_back_to_post_applied_without_for_for_ab(self):
        text = (
            "RESUME Post Applied A/B Surname First Name Middle Name Satwant Singh "
            "Date of Birth Place of Birth Nationality"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "A/B")
        self.assertEqual(result["canonical_id"], "ab")
        self.assertEqual(result["source_label"], "applied_for_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_from_position_header(self):
        text = (
            "Position: Second Engineer Desired Type of Ship: Offshore fleet "
            "Full Name: Ievgen Bakanurskyi"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "Second Engineer")
        self.assertEqual(result["canonical_id"], "2nd_engineer")
        self.assertEqual(result["source_label"], "current_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_from_appraisee_rank_header(self):
        text = (
            "Appraisee’s Rank: THIRD ENGINEER Department Head: Chief Engineer "
            "Highest License Held MEO CL IV"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "THIRD ENGINEER")
        self.assertEqual(result["canonical_id"], "3rd_engineer")
        self.assertEqual(result["source_label"], "current_rank")
        self.assertEqual(result["status"], "PARSED")

    def test_extract_rank_fact_trims_narrative_after_present_rank(self):
        text = (
            "Present Rank 2nd Engineer teams to drive operational excellence. "
            "From date 01-Jan 2026"
        )
        result = self.analyzer._extract_rank_fact_from_text(text)
        self.assertEqual(result["raw_rank"], "2nd Engineer")
        self.assertEqual(result["canonical_id"], "2nd_engineer")
        self.assertEqual(result["source_label"], "current_rank")
        self.assertEqual(result["status"], "PARSED")


if __name__ == "__main__":
    unittest.main()
