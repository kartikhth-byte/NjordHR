import sys
import tempfile
import types
import unittest
from datetime import date
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


class _FakeRegistry:
    def get_resume_id(self, file_path):
        return Path(file_path).stem

    def needs_processing(self, *_args, **_kwargs):
        return False


class _FakeFeedbackStore:
    def get_recent_feedback(self, *_args, **_kwargs):
        return []


class _FakeConfig:
    def __init__(self, download_root):
        self.download_root = str(download_root)
        self.min_similarity_score = 0.0


class AIAnalyzerVisaFilterTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.download_root = Path(self.temp_dir.name)
        self.rank = "2nd Engineer"
        self.rank_folder = self.download_root / "2nd_Engineer"
        self.rank_folder.mkdir(parents=True, exist_ok=True)

        self.analyzer.config = _FakeConfig(self.download_root)
        self.analyzer.registry = _FakeRegistry()
        self.analyzer.feedback = _FakeFeedbackStore()
        self.analyzer._ingest_folder = lambda *_args, **_kwargs: []

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_supported_visa_types_are_extracted_from_prompt(self):
        cases = [
            ("has a valid C1/D (USA) visa", ["C1/D (USA)"]),
            ("candidate has valid C1 (USA) visa", ["C1 (USA)"]),
            ("candidate has valid D (USA) visa", ["D (USA)"]),
            ("has a valid Australia Entry visa", ["Australia Entry visa"]),
            ("has valid MCV (Australia) visa", ["MCV (Australia)"]),
            ("has a valid Schengen visa", ["Schengen"]),
        ]
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                constraint = self.analyzer._extract_us_visa_constraint(prompt)
                self.assertEqual(constraint["accepted_types"], expected)

    def test_generic_us_visa_prompt_accepts_supported_us_visa_family(self):
        constraint = self.analyzer._extract_us_visa_constraint("has a valid US visa")
        self.assertEqual(
            constraint["accepted_types"],
            ["C1/D (USA)", "B1/B2 (USA)", "C1 (USA)", "D (USA)", "US Visa (USA)"],
        )

    def test_supported_visa_types_are_extracted_from_resume_text(self):
        cases = [
            ("Visa: C1/D (USA) Expiry: 04-May-2028", "C1/D (USA)"),
            ("Visa Type: C1 visa Expiry: 04-May-2028", "C1 (USA)"),
            ("Visa Type: D visa Expiry: 04-May-2028", "D (USA)"),
            ("Visa: Australia Entry visa Expiry: 04-May-2028", "Australia Entry visa"),
            ("Visa: MCV (Australia) Expiry: 04-May-2028", "MCV (Australia)"),
            ("Visa: Schengen visa Expiry: 04-May-2028", "Schengen"),
        ]
        for raw_text, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                fact = self.analyzer._extract_us_visa_fact_from_text(raw_text)
                self.assertEqual(fact["status"], "PARSED")
                self.assertEqual(fact["visa_records"][0]["visa_type"], expected_type)
                self.assertEqual(fact["visa_records"][0]["expiry_date"], date(2028, 5, 4))

    def test_us_visa_table_layout_uses_expiry_from_issue_date_expiry_row(self):
        raw_text = (
            "Passport Details\n"
            "Issue Date - Expiry Date 05-Dec-2022 - 04-Dec-2032\n"
            "US Visa / Other US Visa\n"
            "Issue Authority US\n"
            "Issue Date - Expiry Date 10-Feb-2018 - 09-Feb-2023\n"
        )
        fact = self.analyzer._extract_us_visa_fact_from_text(raw_text)
        self.assertEqual(fact["status"], "PARSED")
        self.assertEqual(fact["visa_records"][0]["visa_type"], "US Visa (USA)")
        self.assertEqual(fact["visa_records"][0]["expiry_date"], date(2023, 2, 9))

    def test_structured_only_prompt_recognizes_supported_visa_queries(self):
        self.assertTrue(self.analyzer._is_structured_only_prompt("has a valid Schengen visa"))
        self.assertTrue(self.analyzer._is_structured_only_prompt("has a valid Australia Entry visa"))
        self.assertTrue(self.analyzer._is_structured_only_prompt("having valid UK visa"))

    def test_unsupported_country_visa_prompt_is_marked_for_deterministic_unknown(self):
        constraint = self.analyzer._extract_us_visa_constraint("having valid UK visa")
        self.assertEqual(constraint["requested_label"], "valid UK visa")
        self.assertFalse(constraint["supported"])

        candidate_facts = {
            "travel": {
                "us_visa_status": "PARSED",
                "visa_records": [{
                    "status": "PARSED",
                    "visa_type": "US Visa (USA)",
                    "visa_group": "usa",
                    "expiry_date": date(2028, 6, 26),
                    "expiry_status": "PARSED",
                }],
                "visa_types": ["US Visa (USA)"],
            }
        }
        result = self.analyzer._evaluate_us_visa_rule(candidate_facts, constraint, reference_date=date(2026, 4, 2))
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["reason_code"], "VISA_FILTER_UNSUPPORTED")

    def test_visa_rule_marks_ambiguous_or_missing_expiry_as_unknown(self):
        constraint = self.analyzer._extract_us_visa_constraint("has a valid Schengen visa")

        ambiguous = {
            "travel": {
                "us_visa_status": "PARSED",
                "visa_records": [{
                    "status": "PARSED",
                    "visa_type": "Schengen",
                    "visa_group": "schengen",
                    "expiry_date": None,
                    "expiry_status": "AMBIGUOUS_NUMERIC",
                }],
                "visa_types": ["Schengen"],
            }
        }
        missing = {
            "travel": {
                "us_visa_status": "PARSED",
                "visa_records": [{
                    "status": "PARSED",
                    "visa_type": "Schengen",
                    "visa_group": "schengen",
                    "expiry_date": None,
                    "expiry_status": "MISSING",
                }],
                "visa_types": ["Schengen"],
            }
        }

        ambiguous_result = self.analyzer._evaluate_us_visa_rule(ambiguous, constraint, reference_date=date(2026, 4, 2))
        missing_result = self.analyzer._evaluate_us_visa_rule(missing, constraint, reference_date=date(2026, 4, 2))

        self.assertEqual(ambiguous_result["decision"], "UNKNOWN")
        self.assertEqual(missing_result["decision"], "UNKNOWN")

    def test_visa_only_structured_full_scan_excludes_fail_and_unknown_before_llm(self):
        resume_specs = [
            {
                "filename": "2nd_Engineer_1001.pdf",
                "raw_text": "Visa: Schengen visa Expiry: 04-May-2028",
                "decision": "PASS",
            },
            {
                "filename": "2nd_Engineer_1002.pdf",
                "raw_text": "Visa: Schengen visa Expiry: 04-May-2020",
                "decision": "FAIL",
            },
            {
                "filename": "2nd_Engineer_1003.pdf",
                "raw_text": "Visa: Schengen visa",
                "decision": "UNKNOWN",
            },
            {
                "filename": "2nd_Engineer_1004.pdf",
                "raw_text": "Visa: C1/D (USA) Expiry: 04-May-2028",
                "decision": "FAIL",
            },
        ]

        for spec in resume_specs:
            (self.rank_folder / spec["filename"]).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(spec["filename"]).stem: [
                {
                    "id": f"chunk-{Path(spec['filename']).stem}",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(spec["filename"]).stem,
                        "rank": self.rank,
                        "raw_text": spec["raw_text"],
                    },
                }
            ]
            for spec in resume_specs
        }

        llm_prompts = []
        self.analyzer._reason_with_llm = lambda prompt, retrieved_chunks, past_feedback: llm_prompts.append(prompt) or {
            "is_match": True,
            "reason": "Candidate has the required visa.",
            "confidence": 0.92,
        }

        events = list(self.analyzer.run_analysis_stream(self.rank, "has a valid Schengen visa"))
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertEqual([match["filename"] for match in complete_event["verified_matches"]], ["2nd_Engineer_1001.pdf"])
        self.assertEqual([match["filename"] for match in complete_event["unknown_matches"]], ["2nd_Engineer_1003.pdf"])
        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["failed"], 2)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 1)
        self.assertEqual(len(llm_prompts), 1)
        self.assertIn("Schengen", complete_event["verified_matches"][0]["hard_filter_reasons"][0]["message"])

    def test_unsupported_uk_visa_prompt_does_not_reach_llm_reasoning(self):
        resume_specs = [
            {
                "filename": "Chief_Officer_1001.pdf",
                "raw_text": "US Visa / Other US Visa Issue Date - Expiry Date 10-Feb-2018 - 26-Jun-2028",
            },
            {
                "filename": "Chief_Officer_1002.pdf",
                "raw_text": "Certificate of Competency: First Mate (FG) UK valid until 18-May-2027",
            },
        ]

        for spec in resume_specs:
            (self.rank_folder / spec["filename"]).write_bytes(b"%PDF-1.4")

        self.rank = "Chief Officer"
        self.rank_folder = self.download_root / "Chief_Officer"
        self.rank_folder.mkdir(parents=True, exist_ok=True)
        for spec in resume_specs:
            (self.rank_folder / spec["filename"]).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(spec["filename"]).stem: [
                {
                    "id": f"chunk-{Path(spec['filename']).stem}",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(spec["filename"]).stem,
                        "rank": self.rank,
                        "raw_text": spec["raw_text"],
                    },
                }
            ]
            for spec in resume_specs
        }

        llm_prompts = []
        self.analyzer._reason_with_llm = lambda prompt, retrieved_chunks, past_feedback: llm_prompts.append(prompt) or {
            "is_match": True,
            "reason": "Should not be called.",
            "confidence": 0.99,
        }

        events = list(self.analyzer.run_analysis_stream(self.rank, "having valid UK visa"))
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 0)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 2)
        self.assertEqual(len(complete_event["verified_matches"]), 0)
        self.assertEqual(len(llm_prompts), 0)
        self.assertTrue(
            all(
                result["reason_code"] == "VISA_FILTER_UNSUPPORTED"
                for match in complete_event["unknown_matches"]
                for result in match["hard_filter_reasons"]
            )
        )


if __name__ == "__main__":
    unittest.main()
