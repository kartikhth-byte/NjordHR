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

    def generate_resume_id(self, file_path):
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

    def test_below_the_age_of_prompt_populates_age_constraint(self):
        constraints = self.analyzer._extract_job_constraints("is below the age of 50", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": None, "max_age": 49})
        self.assertEqual(constraints["parsing_notes"], [])

    def test_age_family_exclusive_max_variants_map_to_same_constraint(self):
        prompts = [
            "is below the age of 50",
            "below age 50",
            "less than 50 years old",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["age_range"])
                self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": None, "max_age": 49})
                self.assertEqual(constraints["parsing_notes"], [])

    def test_age_family_inclusive_max_variants_map_to_same_constraint(self):
        prompts = [
            "maximum age should be 50",
            "not more than 50 years old",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["age_range"])
                self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": None, "max_age": 50})
                self.assertEqual(constraints["parsing_notes"], [])

    def test_age_family_exclusive_min_variants_map_to_same_constraint(self):
        prompts = [
            "above the age of 25",
            "over 25",
            "over the age of 25 years",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["age_range"])
                self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": 26, "max_age": None})
                self.assertEqual(constraints["parsing_notes"], [])

    def test_age_family_inclusive_min_variants_map_to_same_constraint(self):
        constraints = self.analyzer._extract_job_constraints("minimum age should be 25", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": 25, "max_age": None})
        self.assertEqual(constraints["parsing_notes"], [])

    def test_age_family_range_variants_map_to_same_constraint(self):
        prompts = [
            "between ages 30 and 45",
            "age between 30 to 45",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["age_range"])
                self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": 30, "max_age": 45})
                self.assertEqual(constraints["parsing_notes"], [])

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

    def test_rank_family_variants_map_to_expected_rank(self):
        prompts = {
            "need chief mate": "chief_officer",
            "looking for second engineer": "2nd_engineer",
            "require bosun candidate": "bosun",
            "junior engineer profile": "junior_engineer",
        }
        for prompt, expected_rank in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["rank_match"])
                self.assertEqual(constraints["hard_constraints"]["rank"]["applied_rank_normalized"], [expected_rank])

    def test_age_and_sea_service_query_populates_unapplied(self):
        constraints = self.analyzer._extract_job_constraints("between 30 and 50 with 7+ years sea service", rank=self.rank)
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["sea_service"]["min_total_months"], 84)

    def test_sea_service_family_variants_preserve_minimum_months(self):
        prompts = {
            "minimum 24 months sea service": 24,
            "at least 5 years experience": 60,
            "3 years sea time": 36,
            "6+ months sailing experience": 6,
        }
        for prompt, expected_months in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("min_sea_service", constraints["unapplied_constraints"])
                self.assertEqual(constraints["hard_constraints"]["sea_service"]["min_total_months"], expected_months)

    def test_sea_service_parser_does_not_swallow_age_range_numbers(self):
        constraints = self.analyzer._extract_job_constraints(
            "must be within the age of 30 and 50 years old and have minimum 5 years experience",
            rank=self.rank,
        )
        self.assertIn("age_range", constraints["applied_constraints"])
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["sea_service"]["min_total_months"], 60)

    def test_rank_and_vessel_type_query_preserves_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer on bulk carrier", rank=self.rank)
        self.assertIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["vessel_type"]["required"], ["bulk carrier"])

    def test_experience_ship_type_family_variants_map_to_same_type(self):
        prompts = {
            "tanker background": "tanker",
            "experience in bulk carrier": "bulk carrier",
            "sailed on lng carrier": "lng",
            "container vessel background": "container",
        }
        for prompt, expected_ship_type in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("experience_ship_type", constraints["applied_constraints"])
                self.assertEqual(constraints["hard_constraints"]["experience_ship_type"], expected_ship_type)

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

    def test_coc_family_variants_map_to_same_requirement(self):
        prompts = [
            "valid coc",
            "coc mandatory",
            "coc holder required",
            "must hold coc",
            "valid certificate of competency required",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("coc_document_gate", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"],
                    {"coc_required": True, "coc_valid_required": True},
                )

    def test_stcw_family_variants_map_to_same_requirement(self):
        prompts = [
            "valid stcw basic",
            "basic stcw required",
            "all basic stcw required",
            "must hold valid basic stcw",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("stcw_basic", constraints["applied_constraints"])
                self.assertEqual(constraints["hard_constraints"]["stcw_basic"], {"required": True})

    def test_company_continuity_two_contracts_prompt_is_supported(self):
        constraints = self.analyzer._extract_job_constraints("same company for 2 contracts", rank=self.rank)
        self.assertIn("company_continuity", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["company_continuity"]["min_same_company_contract_count"],
            2,
        )

    def test_company_continuity_direct_variants_map_to_expected_minimums(self):
        prompts = {
            "same company for 3 contracts": 3,
            "same employer for 3 contracts": 3,
            "has worked for a company for 3 contracts": 3,
            "worked with one employer for 3 contracts": 3,
        }
        for prompt, expected_minimum in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("company_continuity", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["company_continuity"]["min_same_company_contract_count"],
                    expected_minimum,
                )

    def test_company_continuity_threshold_variants_map_to_expected_minimums(self):
        prompts = {
            "more than 1 contract with same company": 2,
            "minimum 3 contracts with one employer": 3,
            "at least 2 contracts in same company": 2,
            "served minimum 2 contracts with same company": 2,
            "has worked for a company for more than 2 contracts": 3,
            "has worked in a company for more than 1 contract": 2,
            "worked under one employer for more than 2 contracts": 3,
        }
        for prompt, expected_minimum in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("company_continuity", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["company_continuity"]["min_same_company_contract_count"],
                    expected_minimum,
                )
                self.assertEqual(constraints["parsing_notes"], [])

    def test_llm_compound_note_ignores_age_ranges(self):
        note = self.analyzer._llm_compound_note_from_prompt(
            "should be within the ages of 30 and 50 years old\nComputed candidate age from DOB: 36 years old."
        )
        self.assertEqual(note, "")

    def test_llm_compound_note_detects_real_and_queries(self):
        note = self.analyzer._llm_compound_note_from_prompt(
            "2nd engineer and valid US visa\nAdditional deterministic context."
        )
        self.assertIn("ALL conditions must be satisfied", note)

    def test_llm_reasoning_prompt_omits_visa_section_when_visa_not_applied(self):
        prompt = self.analyzer._build_llm_reasoning_prompt(
            "2nd engineer with strong leadership",
            [{"metadata": {"raw_text": "Present Rank: 2nd Engineer"}}],
            [],
            applied_constraints=["rank_match"],
        )
        self.assertNotIn("VISA VALIDATION - VERY STRICT", prompt)
        self.assertNotIn("US visa field shows 'Other'", prompt)

    def test_llm_reasoning_prompt_includes_visa_section_when_visa_applied(self):
        prompt = self.analyzer._build_llm_reasoning_prompt(
            "has a valid Schengen visa",
            [{"metadata": {"raw_text": "Visa: Schengen visa Expiry: 04-May-2028"}}],
            [],
            applied_constraints=["us_visa"],
        )
        self.assertIn("VISA VALIDATION - VERY STRICT", prompt)
        self.assertIn("US visa field shows 'Other'", prompt)

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

    def test_semantic_only_ship_experience_prompt_proceeds(self):
        class _FakePrepper:
            last_error = "test fallback"

            def get_embeddings(self, *_args, **_kwargs):
                return None

        self.analyzer.prepper = _FakePrepper()
        self.analyzer._retrieve_candidates_keyword_fallback = lambda *_args, **_kwargs: {}

        events = list(self.analyzer.run_analysis_stream(self.rank, "has experience in duel cell ships"))

        self.assertFalse(any(event["type"] == "graceful_failure" for event in events))
        self.assertTrue(any(event["type"] == "complete" for event in events))

    def test_full_scan_candidates_use_expanded_llm_context_cap(self):
        filename = "2nd_Engineer_2001.pdf"
        pdf_path = self.rank_folder / filename
        pdf_path.write_bytes(b"%PDF-1.4")

        class _FakePDFProcessor:
            def extract_text(self, *_args, **_kwargs):
                return "A" * 35000

        self.analyzer.pdf_processor = _FakePDFProcessor()

        candidates = self.analyzer._enumerate_rank_candidates(self.rank_folder, self.rank)
        chunk = next(iter(candidates.values()))[0]

        self.assertEqual(len(chunk["metadata"]["raw_text"]), self.analyzer.LLM_CONTEXT_TEXT_CHAR_LIMIT)
        self.assertEqual(chunk["metadata"]["filename"], filename)
        self.assertEqual(chunk["metadata"]["source_path"], str(pdf_path))

    def test_keyword_fallback_candidates_use_expanded_llm_context_cap(self):
        filename = "2nd_Engineer_2002.pdf"
        pdf_path = self.rank_folder / filename
        pdf_path.write_bytes(b"%PDF-1.4")

        class _FakePDFProcessor:
            def extract_text(self, *_args, **_kwargs):
                return ("dual fuel ships " * 3000).strip()

        self.analyzer.pdf_processor = _FakePDFProcessor()

        candidates = self.analyzer._retrieve_candidates_keyword_fallback(self.rank, "dual fuel ships", top_k=5)
        chunk = next(iter(candidates.values()))[0]

        self.assertEqual(len(chunk["metadata"]["raw_text"]), self.analyzer.LLM_CONTEXT_TEXT_CHAR_LIMIT)
        self.assertEqual(chunk["metadata"]["filename"], filename)
        self.assertEqual(chunk["metadata"]["source_path"], str(pdf_path))

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

    def test_structured_only_pass_skips_llm_and_surfaces_match(self):
        filename = "2nd_Engineer_1003.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {"resume_id": Path(filename).stem, "rank": self.rank, "raw_text": "Date Of Birth 04-Jan-1971"},
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "role": {"applied_rank_normalized": None},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": None}},
            "personal": {"dob": None},
            "derived": {"age_years": 36},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._evaluate_hard_filters = lambda *args, **kwargs: {
            "decision": "PASS",
            "results": [{
                "decision": "PASS",
                "reason_code": "AGE_MATCH",
                "message": "Candidate age 36 meets requested age filter.",
                "actual_value": 36,
                "expected_value": {"min_age": None, "max_age": 49},
                "confidence": 0.9,
                "unknown_reason": None,
            }],
            "evaluation_date_used": "2026-04-06",
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should be skipped"))

        events = list(self.analyzer.run_analysis_stream(self.rank, "is below the age of 50"))
        match_event = next(event for event in events if event["type"] == "match_found")

        self.assertEqual(match_event["match"]["confidence"], 1.0)
        self.assertEqual(match_event["match"]["reason"], "Candidate age 36 meets requested age filter.")

    def test_mixed_query_pass_still_uses_llm(self):
        filename = "2nd_Engineer_1004.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")
        llm_called = {"value": False}

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
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
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

        def fake_llm(*_args, **_kwargs):
            llm_called["value"] = True
            return {"is_match": True, "reason": "ok", "confidence": 0.9}

        self.analyzer._reason_with_llm = fake_llm

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer with strong leadership under pressure"))

        self.assertTrue(llm_called["value"])
        self.assertTrue(any(event["type"] == "match_found" for event in events))

    def test_semantic_vector_path_uses_metadata_filename_and_source_path(self):
        filename = "2nd_Engineer_1005.pdf"
        pdf_path = self.rank_folder / filename
        pdf_path.write_bytes(b"%PDF-1.4")
        rank = self.rank

        class _FakePrepper:
            last_error = None

            def get_embeddings(self, *_args, **_kwargs):
                return [[0.1, 0.2, 0.3]]

        class _FakeVectorDB:
            last_error = None

            def query(self, *_args, **_kwargs):
                return [{
                    "score": 0.95,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": rank,
                        "filename": filename,
                        "source_path": str(pdf_path),
                        "raw_text": "Worked on dual fuel ships with strong leadership",
                    },
                }]

        self.analyzer.prepper = _FakePrepper()
        self.analyzer.vector_db = _FakeVectorDB()
        self.analyzer._build_candidate_facts = lambda resolved_filename, *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "candidate_id": resolved_filename,
            "role": {"applied_rank_normalized": None},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": None}},
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

        events = list(self.analyzer.run_analysis_stream(self.rank, "has experience in dual fuel ships"))
        match_event = next(event for event in events if event["type"] == "match_found")

        self.assertEqual(match_event["match"]["filename"], filename)

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
        self.assertEqual(unknown_event["match"]["review_path_type"], "version_mismatch_unknown")
        self.assertEqual(unknown_event["match"]["evidence_review_state"], "partial_evidence")
        self.assertEqual(
            unknown_event["match"]["evidence_review_reasons"],
            ["version_mismatch_partial_evaluation"],
        )
        self.assertIsNone(unknown_event["match"]["document_quality_hint"])

    def test_pass_match_includes_evidence_review_fields(self):
        filename = "2nd_Engineer_1002.pdf"
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
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
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

        events = list(self.analyzer.run_analysis_stream(self.rank, "2nd engineer"))
        match_event = next(event for event in events if event["type"] == "match_found")

        self.assertEqual(match_event["match"]["review_path_type"], "none")
        self.assertEqual(match_event["match"]["evidence_review_state"], "sufficient_evidence")
        self.assertEqual(match_event["match"]["evidence_review_reasons"], [])
        self.assertIsNone(match_event["match"]["document_quality_hint"])

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
