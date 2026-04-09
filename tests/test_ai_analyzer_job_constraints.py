import sys
import tempfile
import time
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


class AIAnalyzerJobConstraintTests(unittest.TestCase):
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

    def test_age_and_rank_query_populates_applied_constraints(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer between 30 and 50 years old", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range", "rank_match"])

    def test_configured_supported_rank_prompts_populate_rank_match(self):
        prompts = {
            "electrical officer": "electrical_officer",
            "bosun": "bosun",
            "pumpman": "pumpman",
            "fitter": "fitter",
            "oiler": "oiler",
            "chief cook": "chief_cook",
            "deck cadet": "deck_cadet",
            "junior engineer": "junior_engineer",
        }
        for prompt, expected_rank in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["rank_match"])
                self.assertEqual(constraints["hard_constraints"]["rank"]["applied_rank_normalized"], [expected_rank])
                self.assertEqual(constraints["unapplied_constraints"], [])
                self.assertEqual(constraints["parsing_notes"], [])

    def test_age_and_sea_service_query_populates_unapplied(self):
        constraints = self.analyzer._extract_job_constraints("between 30 and 50 with 7+ years sea service", rank=self.rank)
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["sea_service"]["min_total_months"], 84)

    def test_rank_and_vessel_type_query_preserves_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer on bulk carrier", rank=self.rank)
        self.assertIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["vessel_type"]["required"], ["bulk carrier"])

    def test_rank_and_availability_query_preserves_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer available immediately", rank=self.rank)
        self.assertIn("availability", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["status"], "immediately")

    def test_rank_and_endorsement_query_preserves_canonical_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer DPO required", rank=self.rank)
        self.assertIn("stcw_endorsement", constraints["unapplied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["dp_operational"],
        )

    def test_ambiguous_tanker_endorsement_goes_to_parsing_notes(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer tanker endorsement", rank=self.rank)
        self.assertIn("tanker endorsement", constraints["parsing_notes"])
        self.assertNotIn("stcw_endorsement", constraints["unapplied_constraints"])

    def test_ambiguous_dp_phrase_goes_to_parsing_notes(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer DP2", rank=self.rank)
        self.assertIn("DP2", constraints["parsing_notes"])
        self.assertNotIn("stcw_endorsement", constraints["unapplied_constraints"])

    def test_unclassifiable_fragment_populates_parsing_notes(self):
        constraints = self.analyzer._extract_job_constraints("VLCC-ish but not exactly", rank=self.rank)
        self.assertIn("VLCC-ish but not exactly", constraints["parsing_notes"])

    def test_all_unsupported_query_leaves_applied_empty(self):
        constraints = self.analyzer._extract_job_constraints("7+ years sea service and bulk carrier", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], [])
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertIn("vessel_type", constraints["unapplied_constraints"])

    def test_complete_event_includes_constraint_summary_fields(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                }
            ]
        }
        self.analyzer._is_structured_only_prompt = lambda *_args, **_kwargs: True
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._evaluate_hard_filters = lambda *args, **kwargs: {
            "decision": "PASS",
            "results": [],
            "evaluation_date_used": "2026-04-06",
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer available immediately"))
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertEqual(complete_event["applied_constraints"], ["rank_match"])
        self.assertEqual(complete_event["unapplied_constraints"], ["availability"])
        self.assertEqual(complete_event["parsing_notes"], [])

    def test_all_parsing_notes_query_gracefully_fails(self):
        events = list(self.analyzer.run_analysis_stream(self.rank, "VLCC-ish but not exactly"))
        failure_event = next(event for event in events if event["type"] == "graceful_failure")

        self.assertIn("supported hard constraints or recognizable semantic intent", failure_event["message"])
        self.assertEqual(failure_event["applied_constraints"], [])
        self.assertEqual(failure_event["unapplied_constraints"], [])
        self.assertEqual(failure_event["parsing_notes"], ["VLCC-ish but not exactly"])

    def test_all_unsupported_query_gracefully_fails_with_constraint_warning(self):
        events = list(self.analyzer.run_analysis_stream(self.rank, "7+ years sea service and bulk carrier"))
        failure_event = next(event for event in events if event["type"] == "graceful_failure")

        self.assertIn("minimum sea service", failure_event["message"])
        self.assertIn("vessel type", failure_event["message"])
        self.assertEqual(failure_event["applied_constraints"], [])
        self.assertEqual(failure_event["unapplied_constraints"], ["min_sea_service", "vessel_type"])

    def test_mixed_query_still_proceeds(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")
        enumerate_called = {"value": False}

        def fake_enumerate(*_args, **_kwargs):
            enumerate_called["value"] = True
            return {
                Path(filename).stem: [
                    {
                        "id": "chunk-1",
                        "score": 1.0,
                        "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                    }
                ]
            }

        self.analyzer._enumerate_rank_candidates = fake_enumerate
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._evaluate_hard_filters = lambda *args, **kwargs: {
            "decision": "PASS",
            "results": [],
            "evaluation_date_used": "2026-04-06",
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer with strong leadership under pressure"))

        self.assertTrue(enumerate_called["value"])
        self.assertFalse(any(event["type"] == "graceful_failure" for event in events))
        self.assertTrue(any(event["type"] == "complete" for event in events))

    def test_version_mismatch_unknown_is_preserved_in_candidate_routing(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": "1.1",
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        unknown_event = next(event for event in events if event["type"] == "hard_filter_unknown")

        self.assertEqual(unknown_event["match"]["unknown_reason_types"], ["VERSION_MISMATCH_UNKNOWN"])

    def test_sync_reextract_upgrades_v1_candidate_before_hard_filter(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": "1.1",
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._synchronous_reextract_candidate_facts = lambda *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertEqual(len(complete_event["verified_matches"]), 1)
        self.assertFalse(complete_event["partial_evaluation"]["occurred"])
        self.assertEqual(complete_event["partial_evaluation_notice"], "")

    def test_sync_reextract_per_search_limit_surfaces_partial_evaluation_notice(self):
        filenames = [f"2nd_Engineer_10{i}.pdf" for i in range(2)]
        for filename in filenames:
            (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer.SYNC_REEXTRACT_PER_SEARCH_LIMIT = 1
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": f"chunk-{Path(filename).stem}",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": "Present Rank: 2nd Engineer",
                        "source_path": str(self.rank_folder / filename),
                    },
                }
            ]
            for filename in filenames
        }
        self.analyzer._build_candidate_facts = lambda filename, *args, **kwargs: {
            "facts_version": "1.1",
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._synchronous_reextract_candidate_facts = lambda filename, *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        complete_event = next(event for event in events if event["type"] == "complete")
        unknown_event = next(event for event in events if event["type"] == "hard_filter_unknown")

        self.assertEqual(len(complete_event["verified_matches"]), 1)
        self.assertTrue(complete_event["partial_evaluation"]["occurred"])
        self.assertIn("per_search_limit", complete_event["partial_evaluation"]["reasons"])
        self.assertTrue(complete_event["partial_evaluation_notice"])
        self.assertEqual(unknown_event["match"]["partial_evaluation_reason"], "per_search_limit")

    def test_sync_reextract_timeout_falls_back_to_partial_evaluation(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer.SYNC_REEXTRACT_TIMEOUT_SECONDS = 0.01
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": "1.1",
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }

        def slow_reextract(*args, **kwargs):
            time.sleep(0.05)
            return {
                "facts_version": AIResumeAnalyzer.FACTS_VERSION,
                "candidate_id": filename,
                "role": {"applied_rank_normalized": "2nd_engineer"},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
                "personal": {"dob": None},
                "derived": {"age_years": None},
                "application": {"applied_ship_types": []},
                "experience": {"vessel_types": []},
            }

        self.analyzer._synchronous_reextract_candidate_facts = slow_reextract
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        complete_event = next(event for event in events if event["type"] == "complete")
        unknown_event = next(event for event in events if event["type"] == "hard_filter_unknown")

        self.assertTrue(complete_event["partial_evaluation"]["occurred"])
        self.assertIn("timeout", complete_event["partial_evaluation"]["reasons"])
        self.assertEqual(unknown_event["match"]["partial_evaluation_reason"], "timeout")

    def test_sync_reextract_waiter_timeout_exceeds_owner_timeout(self):
        self.assertGreater(
            self.analyzer.SYNC_REEXTRACT_WAIT_SECONDS,
            self.analyzer.SYNC_REEXTRACT_TIMEOUT_SECONDS,
        )

    def test_sync_reextract_cooldown_skips_immediate_repeat_attempt(self):
        filename = "2nd_Engineer_1001.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        call_count = {"value": 0}
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Present Rank: 2nd Engineer"},
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": "1.1",
            "candidate_id": filename,
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }

        def fast_reextract(*args, **kwargs):
            call_count["value"] += 1
            return {
                "facts_version": AIResumeAnalyzer.FACTS_VERSION,
                "candidate_id": filename,
                "role": {"applied_rank_normalized": "2nd_engineer"},
                "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
                "personal": {"dob": None},
                "derived": {"age_years": None},
                "application": {"applied_ship_types": []},
                "experience": {"vessel_types": []},
            }

        self.analyzer._synchronous_reextract_candidate_facts = fast_reextract
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {"is_match": True, "reason": "ok", "confidence": 0.9}

        first_events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        second_events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        second_complete = next(event for event in second_events if event["type"] == "complete")
        second_unknown = next(event for event in second_events if event["type"] == "hard_filter_unknown")

        self.assertTrue(any(event["type"] == "complete" for event in first_events))
        self.assertEqual(call_count["value"], 1)
        self.assertTrue(second_complete["partial_evaluation"]["occurred"])
        self.assertIn("cooldown", second_complete["partial_evaluation"]["reasons"])
        self.assertEqual(second_unknown["match"]["partial_evaluation_reason"], "cooldown")


if __name__ == "__main__":
    unittest.main()
