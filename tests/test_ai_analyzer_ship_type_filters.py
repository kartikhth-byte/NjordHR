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
                        "source_path": str(self.rank_folder / spec["filename"]),
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
        self.assertEqual(complete_event["verified_matches"][0]["applied_ship_types"], ["Bulk Carrier"])
        self.assertEqual(complete_event["unknown_matches"][0]["applied_ship_types"], [])
        self.assertEqual(len(llm_calls), 1)
        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["failed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 1)

    def test_experienced_ship_type_is_extracted_from_resume_text(self):
        vessel_types = self.analyzer._extract_experienced_ship_types_from_text(
            "Sea Service: Chief Officer on Product Tanker, VLCC and Bulk Carrier vessels."
        )
        self.assertEqual(vessel_types, ["product tanker", "vlcc", "bulk carrier"])

    def test_configured_ship_type_constraint_preserves_exact_config_value(self):
        constraint = self.analyzer._extract_vessel_type_constraint(
            "Need chief officer for Dredger and Survey Vessel"
        )
        self.assertEqual(constraint["required"], ["dredger", "survey vessel"])

    def test_experience_ship_type_prompt_constraint_is_extracted(self):
        constraint = self.analyzer._extract_experience_ship_type_constraint(
            "Need candidates with tanker experience and valid US visa"
        )
        self.assertEqual(constraint, "tanker")

    def test_experience_ship_type_prompt_constraint_supports_configured_value(self):
        constraint = self.analyzer._extract_experience_ship_type_constraint(
            "Need candidates with dredger experience and valid US visa"
        )
        self.assertEqual(constraint, "dredger")

    def test_experience_ship_type_hard_filter_is_separate_from_applied_ship_type(self):
        candidate_facts = {
            "application": {"applied_ship_types": ["Bulk Carrier"]},
            "experience": {"vessel_types": ["tanker"]},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "hard_constraints": {
                "applied_ship_type": "Bulk Carrier",
                "experience_ship_type": "Tanker",
            }
        })
        self.assertEqual(result["decision"], "PASS")

    def test_build_candidate_facts_exposes_experienced_ship_types(self):
        self.analyzer._resolve_candidate_age = lambda *args, **kwargs: {
            "dob": None,
            "age": None,
            "dob_parse_status": "MISSING",
        }
        facts = self.analyzer._build_candidate_facts(
            "2nd_Engineer_1001.pdf",
            self.rank,
            [{"metadata": {"raw_text": "Sea Service on Product Tanker and Bulk Carrier"}}],
            folder_metadata={},
        )
        self.assertEqual(facts["facts_version"], AIResumeAnalyzer.FACTS_VERSION)
        self.assertEqual(facts["identity"], {"full_name": None, "nationality": None})
        self.assertEqual(
            facts["role"],
            {
                "current_rank_raw": None,
                "current_rank_normalized": None,
                "applied_rank_raw": self.rank,
                "applied_rank_normalized": "2nd_engineer",
                "department": "engine",
                "seniority_bucket": "senior_officer",
            },
        )
        self.assertEqual(
            facts["certifications"],
            {
                "coc": {
                    "grade": None,
                    "expiry_date": None,
                    "expiry_status": "MISSING",
                    "status": "MISSING",
                },
                "stcw_basic_all_valid": None,
                "endorsements": {
                    "tanker_oil": "unknown",
                    "tanker_chemical": "unknown",
                    "tanker_gas": "unknown",
                    "dp_operational": "unknown",
                    "gmdss": "unknown",
                },
            },
        )
        self.assertEqual(
            facts["logistics"],
            {
                "passport_expiry_date": None,
                "passport_valid": None,
                "us_visa_valid": None,
                "us_visa_status": None,
                "us_visa_expiry_date": None,
                "availability_date": None,
                "salary_expectation_usd": None,
            },
        )
        self.assertTrue(facts["derived"]["age_is_cached"])
        self.assertEqual(facts["experience"]["vessel_types"], ["product tanker", "bulk carrier"])

    def test_experience_ship_type_missing_is_unknown(self):
        candidate_facts = {
            "application": {"applied_ship_types": ["Bulk Carrier"]},
            "experience": {"vessel_types": []},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "hard_constraints": {"experience_ship_type": "Tanker"}
        })
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_MISSING")


if __name__ == "__main__":
    unittest.main()
