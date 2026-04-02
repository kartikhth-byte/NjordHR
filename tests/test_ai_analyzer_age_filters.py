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


REAL_AGE_VALIDATION_SET = [
    {"filename": "2nd_Engineer_120969.pdf", "dob": date(1971, 1, 4), "age": 55, "decision": "FAIL"},
    {"filename": "2nd_Engineer_17698.pdf", "dob": date(1974, 2, 3), "age": 52, "decision": "FAIL"},
    {"filename": "2nd_Engineer_288.pdf", "dob": date(1965, 12, 24), "age": 60, "decision": "FAIL"},
    {"filename": "2nd_Engineer_315781.pdf", "dob": date(1995, 2, 26), "age": 31, "decision": "PASS"},
    {"filename": "2nd_Engineer_349740.pdf", "dob": date(1989, 11, 4), "age": 36, "decision": "PASS"},
]


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


class AIAnalyzerAgeFilterTests(unittest.TestCase):
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

    def test_real_age_validation_set_yields_expected_pass_fail_decisions(self):
        constraint = {"min_age": 30, "max_age": 50}

        for sample in REAL_AGE_VALIDATION_SET:
            with self.subTest(filename=sample["filename"]):
                candidate_facts = {
                    "personal": {
                        "dob": sample["dob"],
                        "dob_parse_status": "PARSED",
                    },
                    "derived": {
                        "age_years": sample["age"],
                    },
                }
                result = self.analyzer._evaluate_age_rule(candidate_facts, constraint)
                self.assertEqual(result["decision"], sample["decision"])

    def test_hard_filter_stream_excludes_fail_and_unknown_before_llm_reasoning(self):
        resume_specs = [
            {"filename": "2nd_Engineer_120969.pdf", "dob": date(1971, 1, 4), "age": 55, "status": "PARSED"},
            {"filename": "2nd_Engineer_315781.pdf", "dob": date(1995, 2, 26), "age": 31, "status": "PARSED"},
            {"filename": "2nd_Engineer_349740.pdf", "dob": date(1989, 11, 4), "age": 36, "status": "PARSED"},
            {"filename": "2nd_Engineer_unknown.pdf", "dob": None, "age": None, "status": "AMBIGUOUS_NUMERIC"},
        ]
        fact_by_filename = {spec["filename"]: spec for spec in resume_specs}

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
                        "raw_text": spec["filename"],
                    },
                }
            ]
            for spec in resume_specs
        }

        def fake_build_candidate_facts(filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
            spec = fact_by_filename[filename]
            return {
                "candidate_id": filename,
                "rank_folder": rank,
                "personal": {
                    "dob": spec["dob"],
                    "dob_parse_status": spec["status"],
                },
                "derived": {
                    "age_years": spec["age"],
                },
            }

        llm_calls = []

        def fake_reason_with_llm(prompt, retrieved_chunks, past_feedback):
            llm_calls.append({"prompt": prompt, "chunks": retrieved_chunks})
            return {
                "is_match": True,
                "reason": "Matched after deterministic age gate.",
                "confidence": 0.91,
            }

        self.analyzer._build_candidate_facts = fake_build_candidate_facts
        self.analyzer._reason_with_llm = fake_reason_with_llm

        events = list(
            self.analyzer.run_analysis_stream(
                self.rank,
                "should be within the ages of 30 and 50 years old",
            )
        )

        complete_event = next(event for event in events if event["type"] == "complete")
        verified_filenames = [match["filename"] for match in complete_event["verified_matches"]]
        unknown_filenames = [match["filename"] for match in complete_event["unknown_matches"]]

        self.assertEqual(
            set(verified_filenames),
            {"2nd_Engineer_315781.pdf", "2nd_Engineer_349740.pdf"},
        )
        self.assertEqual(unknown_filenames, ["2nd_Engineer_unknown.pdf"])
        self.assertEqual(complete_event["hard_filter_summary"]["scanned"], 4)
        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 2)
        self.assertEqual(complete_event["hard_filter_summary"]["failed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["matched"], 2)

        self.assertEqual(len(llm_calls), 2)
        llm_prompts = [call["prompt"] for call in llm_calls]
        self.assertTrue(all("deterministic age gate" in prompt for prompt in llm_prompts))
        self.assertTrue(all("2nd_Engineer_120969.pdf" not in prompt for prompt in llm_prompts))
        self.assertTrue(all("2nd_Engineer_unknown.pdf" not in prompt for prompt in llm_prompts))


if __name__ == "__main__":
    unittest.main()
