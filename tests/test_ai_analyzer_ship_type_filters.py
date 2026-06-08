import json
import configparser
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from contextlib import redirect_stdout


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


def _write_fake_pdf(path):
    path.write_bytes(f"%PDF-1.4\n% {path.name}\n%%EOF\n".encode("utf-8"))


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
        parser = configparser.ConfigParser()
        parser["ShipTypes"] = {
            "ship_type_options": "\n".join([
                "Bulk Carrier",
                "Tanker",
                "Product Tanker",
                "VLCC",
                "Dredger",
                "Survey Vessel",
            ])
        }
        self.config = parser


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
            _write_fake_pdf(self.rank_folder / spec["filename"])
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
        self.assertEqual(len(llm_calls), 0)
        self.assertEqual(complete_event["hard_filter_summary"]["passed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["failed"], 1)
        self.assertEqual(complete_event["hard_filter_summary"]["unknown"], 1)

    def test_experienced_ship_type_is_extracted_from_resume_text(self):
        vessel_types = self.analyzer._extract_experienced_ship_types_from_text(
            "Sea Service: Chief Officer on Product Tanker, VLCC and Bulk Carrier vessels."
        )
        self.assertEqual(vessel_types, ["product tanker", "vlcc", "bulk carrier"])

    def test_experienced_ship_type_no_match_does_not_warn_when_configured(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            vessel_types = self.analyzer._extract_experienced_ship_types_from_text(
                "Sea Service: Chief Officer with dates, company names, and port details."
            )

        self.assertEqual(vessel_types, [])
        self.assertNotIn("no configured ship-type labels found", stdout.getvalue())

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

    def test_experience_ship_type_prompt_populates_applied_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "Need candidates with tanker experience and valid US visa"
        )
        self.assertIn("experience_ship_type", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["experience_ship_type"], "tanker")

    def test_experience_ship_type_prompt_constraint_supports_configured_value(self):
        constraint = self.analyzer._extract_experience_ship_type_constraint(
            "Need candidates with dredger experience and valid US visa"
        )
        self.assertEqual(constraint, "dredger")

    def test_dual_fuel_experience_prompt_populates_applied_constraint(self):
        constraints = self.analyzer._extract_job_constraints("has dual fuel experience")

        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["engine_type"], "dual_fuel")
        self.assertNotIn("has dual fuel experience", constraints["parsing_notes"])

    def test_dual_fuel_engine_experience_is_extracted_from_resume_text(self):
        engine_types = self.analyzer._extract_engine_types_from_text(
            "Main engine: MAN B&W ME-GI dual fuel engine. Sea service on Bulk Carrier."
        )

        self.assertEqual(engine_types, ["man_b_w_me", "man_b_w_me_gi", "dual_fuel"])

    def test_experience_ship_type_hard_filter_is_separate_from_applied_ship_type(self):
        candidate_facts = {
            "application": {"applied_ship_types": ["Bulk Carrier"]},
            "experience": {"vessel_types": ["tanker"]},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "applied_constraints": ["applied_ship_type", "experience_ship_type"],
            "hard_constraints": {
                "applied_ship_type": "Bulk Carrier",
                "experience_ship_type": "Tanker",
            }
        })
        self.assertEqual(result["decision"], "PASS")

    def test_experience_ship_type_family_matches_tanker_subtypes(self):
        candidate_facts = {
            "experience": {"vessel_types": ["oil tanker", "bulk carrier"]},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "applied_constraints": ["experience_ship_type"],
            "hard_constraints": {"experience_ship_type": "tanker"}
        })
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_MATCH")

    def test_ship_type_expected_values_expand_alias_requested_value(self):
        expected_values = self.analyzer._ship_type_expected_values("container vessel")
        self.assertIn("container", expected_values)
        self.assertIn("container vessel", expected_values)

    def test_engine_experience_family_matches_dual_fuel_aliases(self):
        candidate_facts = {
            "experience": {"engine_types": ["wingd_x_df"]},
            "fact_meta": {"experience.engine_types": {"confidence": 0.8}},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "applied_constraints": ["engine_experience"],
            "hard_constraints": {
                "engine_experience": {
                    "engine_type": "dual_fuel",
                    "expected_values": self.analyzer._engine_type_expected_values("dual_fuel"),
                }
            },
        })
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["results"][0]["reason_code"], "ENGINE_EXPERIENCE_MATCH")

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
        self.assertEqual(
            facts["identity"],
            {"full_name": None, "full_name_snippet": "", "nationality": None},
        )
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
                    "igf_advanced_cop": "unknown",
                    "igf_basic_cop": "unknown",
                    "tanker_oil": "unknown",
                    "tanker_oil_basic_cop": "unknown",
                    "tanker_oil_advanced_cop": "unknown",
                    "tanker_chemical": "unknown",
                    "tanker_chemical_basic_cop": "unknown",
                    "tanker_chemical_advanced_cop": "unknown",
                    "tanker_gas": "unknown",
                    "tanker_gas_basic_cop": "unknown",
                    "tanker_gas_advanced_cop": "unknown",
                    "cert_ecdis": "unknown",
                    "cert_arpa": "unknown",
                    "cert_brm_btm": "unknown",
                    "cert_erm": "unknown",
                    "cert_pscrb": "unknown",
                    "cert_aff": "unknown",
                    "cert_mfa": "unknown",
                    "cert_medical_care": "unknown",
                    "cert_sso": "unknown",
                    "dp_operational": "unknown",
                    "gmdss": "unknown",
                },
            },
        )
        self.assertEqual(
            facts["logistics"],
            {
                "passport_expiry_date": None,
                "passport_expiry_status": "MISSING",
                "passport_valid": None,
                "us_visa_valid": None,
                "us_visa_status": None,
                "us_visa_expiry_date": None,
                "availability_date": None,
                "availability_end_date": None,
                "availability_status": "MISSING",
                "salary_expectation_usd": None,
            },
        )
        self.assertTrue(facts["derived"]["age_is_cached"])
        self.assertEqual(facts["experience"]["vessel_types"], ["product tanker", "bulk carrier"])

    def test_build_candidate_facts_carries_passport_expiry_status(self):
        self.analyzer._resolve_candidate_age = lambda *args, **kwargs: {
            "dob": None,
            "age": None,
            "dob_parse_status": "MISSING",
        }
        raw_text = (
            "Passport Details Passport No. Z6128495 Issue Authority RANCHI "
            "Issue Date - Expiry Date 30-Dec-2020 - 29-Dec-2030"
        )
        facts = self.analyzer._build_candidate_facts(
            "2nd_Engineer_120969.pdf",
            self.rank,
            [{"metadata": {"raw_text": raw_text}}],
            folder_metadata={},
        )
        self.assertEqual(facts["logistics"]["passport_expiry_date"], "2030-12-29")
        self.assertEqual(facts["logistics"]["passport_expiry_status"], "PARSED")

    def test_experience_ship_type_missing_is_unknown(self):
        candidate_facts = {
            "application": {"applied_ship_types": ["Bulk Carrier"]},
            "experience": {"vessel_types": []},
        }
        result = self.analyzer._evaluate_hard_filters(candidate_facts, {
            "applied_constraints": ["experience_ship_type"],
            "hard_constraints": {"experience_ship_type": "Tanker"}
        })
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["results"][0]["reason_code"], "EXPERIENCE_SHIP_TYPE_MISSING")


if __name__ == "__main__":
    unittest.main()
