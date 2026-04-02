import json
import sys
import tempfile
import types
import unittest
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


class AIAnalyzerShipTypeFilterTests(unittest.TestCase):
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

    def test_rank_manifest_metadata_is_loaded_for_ai_search(self):
        manifest_path = self.rank_folder / "manifest.json"
        manifest_path.write_text(json.dumps({
            "version": 1,
            "files": {
                "2nd_Engineer_1001.pdf": {
                    "candidate_id": "1001",
                    "rank": self.rank,
                    "applied_ship_types": ["Bulk Carrier"],
                }
            }
        }), encoding="utf-8")
        metadata = self.analyzer._rank_manifest_metadata(self.rank_folder)
        self.assertEqual(metadata["2nd_Engineer_1001.pdf"]["applied_ship_types"], ["Bulk Carrier"])

    def test_applied_ship_type_hard_filter_uses_manifest_metadata(self):
        resume_specs = [
            {"filename": "2nd_Engineer_1001.pdf", "ship_types": ["Bulk Carrier"]},
            {"filename": "2nd_Engineer_1002.pdf", "ship_types": ["Tanker"]},
            {"filename": "2nd_Engineer_1003.pdf", "ship_types": []},
        ]
        for spec in resume_specs:
            (self.rank_folder / spec["filename"]).write_bytes(b"%PDF-1.4")
        (self.rank_folder / "manifest.json").write_text(json.dumps({
            "version": 1,
            "files": {
                spec["filename"]: {
                    "candidate_id": Path(spec["filename"]).stem,
                    "rank": self.rank,
                    "applied_ship_types": spec["ship_types"],
                }
                for spec in resume_specs
            }
        }), encoding="utf-8")

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
        self.analyzer._build_candidate_facts = AIResumeAnalyzer._build_candidate_facts.__get__(self.analyzer, AIResumeAnalyzer)
        self.analyzer._resolve_candidate_age = lambda *args, **kwargs: {
            "dob": None,
            "age": None,
            "dob_parse_status": "MISSING",
        }

        llm_calls = []
        self.analyzer._reason_with_llm = lambda prompt, retrieved_chunks, past_feedback: llm_calls.append(prompt) or {
            "is_match": True,
            "reason": "Match",
            "confidence": 0.9,
        }

        events = list(self.analyzer.run_analysis_stream(self.rank, "show candidates", applied_ship_type="Bulk Carrier"))
        complete_event = next(event for event in events if event["type"] == "complete")

        verified = [match["filename"] for match in complete_event["verified_matches"]]
        unknown = [match["filename"] for match in complete_event["unknown_matches"]]

        self.assertEqual(verified, ["2nd_Engineer_1001.pdf"])
        self.assertEqual(unknown, ["2nd_Engineer_1003.pdf"])
        self.assertEqual(len(llm_calls), 1)
        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["failed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
