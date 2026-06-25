import sys
import tempfile
import time
import types
import unittest
import json
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

    def generate_resume_id(self, file_path):
        return Path(file_path).stem

    def needs_processing(self, *_args, **_kwargs):
        return False

    def upsert_file_record(self, *_args, **_kwargs):
        return None


class _FakeFeedbackStore:
    def get_recent_feedback(self, *_args, **_kwargs):
        return []


class _FakeConfig:
    def __init__(self, download_root):
        self.download_root = str(download_root)
        self.min_similarity_score = 0.0


class _IngestRegistry(_FakeRegistry):
    def needs_processing(self, *_args, **_kwargs):
        return True


class _FakePdfProcessor:
    def __init__(self, text):
        self.text = text

    def extract_text(self, *_args, **_kwargs):
        return self.text


class _FakePrepper:
    def __init__(self, embeddings=None):
        self.embeddings = embeddings if embeddings is not None else [[0.1, 0.2, 0.3]]
        self.last_error = ""

    def chunk_text(self, text, resume_id, rank, filename=None, source_path=None):
        return [
            {
                "text": text,
                "metadata": {
                    "resume_id": resume_id,
                    "rank": rank,
                    "filename": filename,
                    "source_path": source_path,
                    "raw_text": text,
                },
            }
        ]

    def get_embeddings(self, *_args, **_kwargs):
        return self.embeddings


class _FakeVectorDb:
    def __init__(self):
        self.upserts = []

    def namespace_vector_count(self, *_args, **_kwargs):
        return 0

    def upsert_chunks(self, chunks, embeddings, rank):
        self.upserts.append((chunks, embeddings, rank))
        return True


class _UnavailableVectorDb(_FakeVectorDb):
    def __init__(self):
        super().__init__()
        self.last_error = "Failed to inspect namespace stats: unauthorized"

    def namespace_vector_count(self, *_args, **_kwargs):
        return None


class _FailingVectorDb(_FakeVectorDb):
    def __init__(self):
        super().__init__()
        self.last_error = ""

    def upsert_chunks(self, chunks, embeddings, rank):
        self.upserts.append((chunks, embeddings, rank))
        self.last_error = "Upsert failed: unauthorized"
        return False


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

    def test_ingest_folder_captures_candidate_facts_for_review(self):
        resume_path = self.rank_folder / "Jane_Doe.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n")
        extracted_text = "Jane Doe\n" + ("2nd engineer resume " * 10)
        captured = []

        self.analyzer.registry = _IngestRegistry()
        self.analyzer.pdf_processor = _FakePdfProcessor(extracted_text)
        self.analyzer.prepper = _FakePrepper()
        self.analyzer.vector_db = _FakeVectorDb()
        delattr(self.analyzer, "_ingest_folder")

        events = list(
            self.analyzer._ingest_folder(
                str(self.rank_folder),
                self.rank,
                review_capture_callback=lambda candidate_facts, context: captured.append((candidate_facts, context)),
            )
        )

        self.assertEqual(events[-1]["type"], "indexing_complete")
        self.assertEqual(len(captured), 1)
        candidate_facts, context = captured[0]
        self.assertEqual(candidate_facts["schema_version"], "candidate_facts.v1")
        self.assertEqual(candidate_facts["source"]["file_name"], "Jane_Doe.pdf")
        self.assertEqual(candidate_facts["source"]["resume_id"], "Jane_Doe.pdf")
        self.assertEqual(candidate_facts["identity"]["candidate_name"]["value"], "Jane Doe")
        self.assertEqual(context["candidate_resume_id"], "Jane_Doe")
        self.assertEqual(context["resume_blob_id"], "Jane_Doe")
        self.assertEqual(context["filename"], "Jane_Doe.pdf")
        self.assertEqual(context["parser_version"], "generic_pdf.v1")
        self.assertEqual(context["facts_revision"], "candidate_facts.v1")

    def test_ingest_folder_does_not_force_reindex_when_vector_health_unknown(self):
        resume_path = self.rank_folder / "Jane_Doe.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n")

        self.analyzer.registry = _FakeRegistry()
        self.analyzer.pdf_processor = _FakePdfProcessor("Jane Doe\n" + ("2nd engineer resume " * 10))
        self.analyzer.prepper = _FakePrepper()
        self.analyzer.vector_db = _UnavailableVectorDb()
        delattr(self.analyzer, "_ingest_folder")

        events = list(self.analyzer._ingest_folder(str(self.rank_folder), self.rank))

        self.assertEqual(events, [{
            "type": "indexing_complete",
            "current": 0,
            "total": 0,
            "message": "Index is up to date; vector index health could not be verified.",
        }])
        self.assertEqual(self.analyzer.vector_db.upserts, [])

    def test_ingest_folder_does_not_mark_processed_when_vector_upsert_fails(self):
        class _RecordingRegistry(_IngestRegistry):
            def __init__(self):
                self.upserts = []

            def upsert_file_record(self, *args, **kwargs):
                self.upserts.append((args, kwargs))

        resume_path = self.rank_folder / "Jane_Doe.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n")
        registry = _RecordingRegistry()

        self.analyzer.registry = registry
        self.analyzer.pdf_processor = _FakePdfProcessor("Jane Doe\n" + ("2nd engineer resume " * 10))
        self.analyzer.prepper = _FakePrepper()
        self.analyzer.vector_db = _FailingVectorDb()
        delattr(self.analyzer, "_ingest_folder")

        events = list(self.analyzer._ingest_folder(str(self.rank_folder), self.rank))

        self.assertEqual(registry.upserts, [])
        self.assertEqual(len(self.analyzer.vector_db.upserts), 1)
        self.assertEqual(events[-1]["type"], "indexing_complete")
        self.assertTrue(any("vector upsert failed" in event.get("message", "") for event in events))

    def test_iter_pdf_files_skips_supporting_document_attachments(self):
        resume_path = self.rank_folder / "Anubhav_Resume.pdf"
        support_path = self.rank_folder / "All_Promotion_Letters_Appraisals.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n")
        support_path.write_bytes(b"%PDF-1.4\n")
        Path(str(support_path) + ".json").write_text(
            json.dumps({"attachment_name": "2. All Promotion Letters & Appraisals.pdf"}),
            encoding="utf-8",
        )
        delattr(self.analyzer, "_ingest_folder")

        self.assertEqual(self.analyzer._iter_pdf_files(self.rank_folder), [resume_path])

    def test_age_and_rank_query_populates_applied_constraints(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer between 30 and 50 years old", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range", "rank_match"])

    def test_structured_rank_scope_suppresses_prompt_rank_constraint(self):
        constraints = self.analyzer._extract_job_constraints(
            "chief engineer between 30 and 50 years old",
            rank=self.rank,
            suppress_prompt_rank=True,
        )
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        self.assertNotIn("rank", constraints["hard_constraints"])

    def test_below_the_age_of_prompt_populates_age_constraint(self):
        constraints = self.analyzer._extract_job_constraints("is below the age of 50", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        self.assertEqual(constraints["hard_constraints"]["age_years"], {"min_age": None, "max_age": 49})
        self.assertEqual(constraints["parsing_notes"], [])

    def test_bare_between_age_range_is_consumed_and_not_split(self):
        constraints = self.analyzer._extract_job_constraints("between 30 and 50", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        observability = self.analyzer._build_prompt_observability(
            "between 30 and 50",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(observability["clause_accounting"], {"applied": 1, "soft": 0, "unsupported": 0})

    def test_between_age_range_with_years_old_is_fully_consumed(self):
        constraints = self.analyzer._extract_job_constraints("between 30 and 50 years old", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], ["age_range"])
        observability = self.analyzer._build_prompt_observability(
            "between 30 and 50 years old",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(observability["clause_accounting"], {"applied": 1, "soft": 0, "unsupported": 0})

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

    def test_rank_family_separator_and_alias_variants_map_to_expected_rank(self):
        prompts = {
            "need a C/E": ["chief_engineer"],
            "looking for C/O": ["chief_officer"],
            "2/E available": ["2nd_engineer"],
            "3/O wanted": ["3rd_officer"],
            "skipper for coastal run": ["master"],
            "Capt needed urgently": ["master"],
            "second mate": ["2nd_officer"],
            "first mate": ["chief_officer"],
            "1st mate": ["chief_officer"],
            "2nd mate": ["2nd_officer"],
            "third mate": ["3rd_officer"],
            "3rd mate": ["3rd_officer"],
            "1/O": ["chief_officer"],
            "1/E": ["chief_engineer"],
            "Mstr": ["master"],
            "old man": ["master"],
            "electro-technical officer": ["electro_technical_officer"],
            "E.T.O.": ["electro_technical_officer"],
            "2nd-officer": ["2nd_officer"],
            "Chief.Officer": ["chief_officer"],
            "chief-eng": ["chief_engineer"],
            "engineer in charge": ["chief_engineer"],
            "C/E or 2/E": ["chief_engineer", "2nd_engineer"],
        }
        for prompt, expected_ranks in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], ["rank_match"])
                self.assertEqual(constraints["hard_constraints"]["rank"]["applied_rank_normalized"], expected_ranks)

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

    def test_recent_contract_vessel_experience_family_maps_to_structured_constraint(self):
        prompts = [
            "12 months experience on container in last 3 contracts",
            "12 months on container in recent 3 contracts",
            "minimum 12 months container experience in last 3 contracts",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["recent_contract_vessel_experience"],
                    {
                        "vessel_type": "container",
                        "min_months": 12,
                        "lookback_contracts": 3,
                        "requested_label": "12 months experience on container in last 3 contracts",
                        "display_value": prompt,
                    },
                )

    def test_recent_contract_vessel_experience_accepts_year_duration(self):
        constraints = self.analyzer._extract_job_constraints(
            "2 years tanker experience in last 3 contracts",
            rank=self.rank,
        )
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertNotIn("experience_ship_type", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"],
            {
                "vessel_type": "tanker",
                "min_months": 24,
                "lookback_contracts": 3,
                "requested_label": "24 months experience on tanker in last 3 contracts",
                "display_value": "2 years tanker experience in last 3 contracts",
            },
        )

    def test_recent_contract_vessel_experience_accepts_duration_without_contract_window(self):
        constraints = self.analyzer._extract_job_constraints(
            "Has minimum  12 months experience in oil tanker",
            rank=self.rank,
        )
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertNotIn("experience_ship_type", constraints["applied_constraints"])
        self.assertNotIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"],
            {
                "vessel_type": "tanker",
                "min_months": 12,
                "lookback_contracts": 1,
                "requested_label": "12 months experience on tanker",
                "display_value": "Has minimum 12 months experience in oil tanker",
            },
        )

    def test_recent_contract_vessel_experience_accepts_no_duration_window(self):
        constraints = self.analyzer._extract_job_constraints(
            "container vessel experience in recent 3 contracts",
            rank=self.rank,
        )
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertNotIn("experience_ship_type", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"],
            {
                "vessel_type": "container",
                "min_months": 0,
                "lookback_contracts": 3,
                "requested_label": "container experience in last 3 contracts",
                "display_value": "container vessel experience in recent 3 contracts",
            },
        )

    def test_rank_duration_experience_family_maps_to_structured_constraint(self):
        cases = {
            "at least 4 years as chief officer": ("chief_officer", 48),
            "minimum 24 months 2nd engineer experience": ("2nd_engineer", 24),
            "has worked 1 year as 3rd engineer": ("3rd_engineer", 12),
            "chief officer experience 18 months": ("chief_officer", 18),
        }
        for prompt, (expected_rank, expected_months) in cases.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("rank_duration_experience", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["rank_duration_experience"]["rank_normalized"],
                    expected_rank,
                )
                self.assertEqual(
                    constraints["hard_constraints"]["rank_duration_experience"]["min_months"],
                    expected_months,
                )
                self.assertNotIn("min_sea_service", constraints["unapplied_constraints"])

    def test_engine_experience_family_maps_to_structured_constraint(self):
        cases = {
            "with me engine experience": "man_b_w_me",
            "has MAN B&W experience": "man_b_w",
            "has B&W engine experience": "man_b_w",
            "WinGD X-DF experience": "wingd_x_df",
            "Wartsila 32DF experience": "wartsila_dual_fuel",
            "has Wartsila experience": "wartsila",
            "has RTFlex engine type experience": "wartsila_rt_flex",
            "has 12RTA96C engine experience": "wartsila_rta",
            "has 6S50MC-C engine experience": "man_b_w_mc",
            "has MaK engine experience": "mak",
            "has ME-LGIA engine experience": "man_b_w_me_lgia",
            "has Yanmar engine experience": "yanmar",
            "has Bergen engine experience": "bergen",
            "has electronic engine experience": "electronically_controlled_engine",
            "has electronically controlled main engine experience": "electronically_controlled_engine",
            "has mechanical engine experience": "mechanical_engine",
            "has X-DF-HP engine experience": "wingd_x_df_hp",
            "has ME-C-GI engine experience": "man_b_w_me_c_gi",
            "has Everllence B&W experience": "man_b_w",
            "methanol engine experience": "methanol_engine",
            "ammonia engine experience": "ammonia_engine",
        }
        for prompt, expected_engine_type in cases.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_experience", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["engine_experience"]["engine_type"],
                    expected_engine_type,
                )
                self.assertEqual(constraints["hard_constraints"]["engine_experience"]["min_months"], 0)
                self.assertEqual(constraints["hard_constraints"]["engine_experience"]["lookback_contracts"], 0)
                self.assertNotIn("experience_ship_type", constraints["applied_constraints"])

    def test_engine_experience_family_preserves_contract_window(self):
        constraints = self.analyzer._extract_job_constraints(
            "Mitsubishi UEC experience in the last 3 contracts",
            rank=self.rank,
        )
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["engine_experience"]["engine_type"],
            "mitsubishi_uec",
        )
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["lookback_contracts"], 3)
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["recent_contract_match_mode"], "any")
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["min_months"], 0)
        self.assertNotIn("min_sea_service", constraints["unapplied_constraints"])

    def test_engine_experience_recent_vessels_with_engine_requires_all_recent_rows(self):
        cases = [
            "recent 3 vessels with UEC engine",
            "last 3 contracts should be Mitsubishi UEC",
            "all recent 3 contracts on UEC engine",
            "last 2 vessels should be ME-GI",
        ]
        for prompt in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_experience", constraints["applied_constraints"])
                engine_constraint = constraints["hard_constraints"]["engine_experience"]
                self.assertEqual(engine_constraint["recent_contract_match_mode"], "all")

    def test_engine_experience_family_preserves_minimum_months(self):
        constraints = self.analyzer._extract_job_constraints(
            "has minimum 12 months experience in Mitsubishi UEC",
            rank=self.rank,
        )
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["engine_experience"]["engine_type"],
            "mitsubishi_uec",
        )
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["min_months"], 12)
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["lookback_contracts"], 0)
        self.assertNotIn("min_sea_service", constraints["unapplied_constraints"])

    def test_engine_experience_duration_does_not_consume_age_range(self):
        constraints = self.analyzer._extract_job_constraints(
            "has dual fuel experience and has valid passport and is between 30 years and 50 years old",
            rank=self.rank,
        )

        self.assertIn("age_range", constraints["applied_constraints"])
        self.assertIn("passport_validity", constraints["applied_constraints"])
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["engine_type"], "dual_fuel")
        self.assertEqual(constraints["hard_constraints"]["engine_experience"]["min_months"], 0)
        self.assertEqual(
            constraints["hard_constraints"]["engine_experience"]["display_value"],
            "has dual fuel experience",
        )

        observability = self.analyzer._build_prompt_observability(
            "has dual fuel experience and has valid passport and is between 30 years and 50 years old",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(observability["clause_accounting"], {"applied": 3, "soft": 0, "unsupported": 0})

        result = self.analyzer._evaluate_hard_filters(
            {
                "facts_version": AIResumeAnalyzer.FACTS_VERSION,
                "personal": {"dob": date(1986, 11, 1), "dob_parse_status": "PARSED"},
                "derived": {"age_years": 39},
                "logistics": {
                    "passport_expiry_date": "2033-09-05",
                    "passport_expiry_status": "PARSED",
                },
                "experience": {"engine_types": ["dual_fuel"]},
                "fact_meta": {
                    "derived.age_years": {"confidence": 0.9},
                    "logistics.passport_expiry_date": {"confidence": 0.9},
                    "experience.engine_types": {"confidence": 0.8},
                },
            },
            constraints,
        )

        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(
            [rule["reason_code"] for rule in result["results"]],
            ["AGE_IN_RANGE", "PASSPORT_VALID", "ENGINE_EXPERIENCE_MATCH"],
        )

    def test_dual_fuel_passport_age_prompt_emits_deterministic_match_without_llm(self):
        filename = "Chief-Engineer_Container_10060_2026-01-29_16-36-29.pdf"
        pdf_path = self.rank_folder / filename
        pdf_path.write_bytes(b"%PDF-1.4")
        resume_id = self.analyzer.registry.get_resume_id(pdf_path)
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            resume_id: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": resume_id,
                        "rank": self.rank,
                        "filename": filename,
                        "source_path": str(pdf_path),
                        "raw_text": "Dual Fuel (X-DF) ENGINE",
                    },
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "personal": {"dob": date(1986, 11, 1), "dob_parse_status": "PARSED"},
            "derived": {"age_years": 39},
            "logistics": {
                "passport_expiry_date": "2033-09-05",
                "passport_expiry_status": "PARSED",
            },
            "application": {"applied_ship_types": []},
            "experience": {"engine_types": ["dual_fuel"], "vessel_types": []},
            "fact_meta": {
                "derived.age_years": {"confidence": 0.9},
                "logistics.passport_expiry_date": {"confidence": 0.9},
                "experience.engine_types": {"confidence": 0.8},
            },
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("LLM should be skipped for fully consumed deterministic constraints")
        )

        prompt = "has dual fuel experience and has valid passport and is between 30 years and 50 years old"
        events = list(self.analyzer.run_analysis_stream(self.rank, prompt))

        match_event = next(event for event in events if event["type"] == "match_found")
        complete_event = next(event for event in events if event["type"] == "complete")
        candidate_audit = next(
            entry for entry in complete_event["hard_filter_audit"] if entry["filename"] == filename
        )

        self.assertEqual(match_event["match"]["filename"], filename)
        self.assertEqual(match_event["match"]["confidence"], 1.0)
        self.assertEqual(complete_event["residual_text"], "")
        self.assertFalse(candidate_audit["llm_reached"])
        self.assertEqual(candidate_audit["result_bucket"], "verified_match")

    def test_experience_display_values_scope_compound_prompt_clauses(self):
        cases = [
            (
                "Mitsubishi UEC experience and tanker experience in last 3 contracts and has valid passport",
                {
                    "engine_experience": "Mitsubishi UEC experience",
                    "recent_contract_vessel_experience": "tanker experience in last 3 contracts",
                },
            ),
            (
                "recent 3 vessels with UEC engine and container experience and has valid passport",
                {
                    "engine_vessel_experience": "recent 3 vessels with UEC engine and container experience",
                },
            ),
        ]

        for prompt, expected_display_values in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                for constraint_id, expected_display_value in expected_display_values.items():
                    self.assertEqual(
                        constraints["hard_constraints"][constraint_id]["display_value"],
                        expected_display_value,
                    )
                observability = self.analyzer._build_prompt_observability(
                    prompt,
                    constraints,
                    has_semantic_intent=False,
                )
                self.assertEqual(observability["residual_text"], "")

    def test_engine_experience_slot_parser_handles_recruiter_variants(self):
        cases = [
            ("Mitsubishi UEC in last 3 contracts", "mitsubishi_uec", 0, 3),
            ("last 3 contracts should be Mitsubishi UEC", "mitsubishi_uec", 0, 3),
            ("recent 3 vessels with UEC engine", "mitsubishi_uec", 0, 3),
            ("candidate should have sailed on UEC engines", "mitsubishi_uec", 0, 0),
            ("worked on ships fitted with Mitsubishi UEC", "mitsubishi_uec", 0, 0),
            ("must have UEC engine background", "mitsubishi_uec", 0, 0),
            ("Mitsubishi UEC only", "mitsubishi_uec", 0, 0),
            ("UEC in latest 4 ships", "mitsubishi_uec", 0, 4),
            ("has handled Mitsubishi UEC machinery", "mitsubishi_uec", 0, 0),
            ("12 months sailing on Mitsubishi UEC", "mitsubishi_uec", 12, 0),
            ("served 1 year with Mitsubishi UEC", "mitsubishi_uec", 12, 0),
            ("Mitsubishi UEC within recent 3 contracts", "mitsubishi_uec", 0, 3),
            ("last 2 vessels should be ME-GI", "man_b_w_me_gi", 0, 2),
            ("minimum 6 months on WinGD X-DF", "wingd_x_df", 6, 0),
            ("RT Flex in recent 5 vessels", "wartsila_rt_flex", 0, 5),
            ("rt-flex engine type experience", "wartsila_rt_flex", 0, 0),
            ("has experience in rtflex engine", "wartsila_rt_flex", 0, 0),
            ("has experience in ME-GI engine type", "man_b_w_me_gi", 0, 0),
            ("has 6S60ME-C10.5 engine experience", "man_b_w_me_c", 0, 0),
            ("has X52DF engine type experience", "wingd_x_df", 0, 0),
            ("electronically controlled engines in recent 4 vessels", "electronically_controlled_engine", 0, 4),
        ]
        for prompt, expected_engine_type, expected_months, expected_contracts in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_experience", constraints["applied_constraints"])
                engine_constraint = constraints["hard_constraints"]["engine_experience"]
                self.assertEqual(engine_constraint["engine_type"], expected_engine_type)
                self.assertEqual(engine_constraint["min_months"], expected_months)
                self.assertEqual(engine_constraint["lookback_contracts"], expected_contracts)

    def test_electric_propulsion_terms_do_not_match_electronic_main_engine(self):
        for prompt in (
            "has electric engine experience",
            "has electric propulsion experience",
            "has diesel-electric propulsion experience",
            "has electrons engine experience",
        ):
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertNotIn("engine_experience", constraints["applied_constraints"])

    def test_engine_vessel_experience_family_maps_to_same_row_constraint(self):
        cases = [
            ("Mitsubishi UEC on tanker in last 3 contracts", "mitsubishi_uec", "tanker", 0, 3),
            ("12 months ME-GI on oil tanker", "man_b_w_me_gi", "oil tanker", 12, 0),
            ("dual fuel product tanker experience in recent 4 contracts", "dual_fuel", "product tanker", 0, 4),
            ("Mitsubishi UEC tanker experience in last 3 contracts", "mitsubishi_uec", "tanker", 0, 3),
        ]
        for prompt, expected_engine_type, expected_vessel_type, expected_months, expected_contracts in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_vessel_experience", constraints["applied_constraints"])
                self.assertNotIn("engine_experience", constraints["applied_constraints"])
                self.assertNotIn("recent_contract_vessel_experience", constraints["applied_constraints"])
                combined = constraints["hard_constraints"]["engine_vessel_experience"]
                self.assertEqual(combined["engine_type"], expected_engine_type)
                self.assertEqual(combined["vessel_type"], expected_vessel_type)
                self.assertEqual(combined["min_months"], expected_months)
                self.assertEqual(combined["lookback_contracts"], expected_contracts)

    def test_engine_vessel_experience_preserves_all_recent_contract_mode(self):
        cases = [
            ("last 3 contracts on oil tanker with MAN B&W engine", "man_b_w", "oil tanker", 3),
            ("recent 3 vessels with UEC engine and container experience", "mitsubishi_uec", "container", 3),
        ]
        for prompt, expected_engine_type, expected_vessel_type, expected_contracts in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_vessel_experience", constraints["applied_constraints"])
                self.assertNotIn("engine_experience", constraints["applied_constraints"])
                self.assertNotIn("recent_contract_vessel_experience", constraints["applied_constraints"])
                combined = constraints["hard_constraints"]["engine_vessel_experience"]
                self.assertEqual(combined["engine_type"], expected_engine_type)
                self.assertEqual(combined["vessel_type"], expected_vessel_type)
                self.assertEqual(combined["lookback_contracts"], expected_contracts)
                self.assertEqual(combined["recent_contract_match_mode"], "all")

    def test_engine_and_vessel_experience_with_and_remains_separate_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "Mitsubishi UEC experience and tanker experience in last 3 contracts",
            rank=self.rank,
        )
        self.assertNotIn("engine_vessel_experience", constraints["applied_constraints"])
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])

    def test_rank_and_availability_query_preserves_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer available immediately", rank=self.rank)
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["status"], "immediately")

    def test_availability_date_query_is_applied_with_preserved_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer available from January 15", rank=self.rank)
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["value_type"], "date")
        self.assertIsNotNone(constraints["hard_constraints"]["availability"]["available_from_date"])

    def test_availability_relative_query_is_applied_with_preserved_value(self):
        constraints = self.analyzer._extract_job_constraints("pumpman joinable in 30 days", rank=self.rank)
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["value_type"], "relative_phrase")
        self.assertEqual(constraints["hard_constraints"]["availability"]["relative_days"], 30)

    def test_rank_and_endorsement_query_preserves_canonical_value(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer DPO required", rank=self.rank)
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["dp_operational"],
        )

    def test_new_applied_family_without_semantic_intent_is_structured_only(self):
        constraints = self.analyzer._extract_job_constraints("has tanker background", rank=self.rank)
        structured_only = self.analyzer._is_structured_only_prompt(
            "has tanker background",
            job_constraints=constraints,
            has_semantic_intent=False,
        )
        self.assertTrue(structured_only)

    def test_multi_endorsement_query_preserves_all_canonical_values(self):
        constraints = self.analyzer._extract_job_constraints("DPO and GMDSS required", rank=self.rank)
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["dp_operational", "gmdss"],
        )

    def test_stcw_endorsement_registry_slice_consumes_compound_prompt_cleanly(self):
        constraints = self.analyzer._extract_job_constraints("DPO and GMDSS", rank=self.rank)
        observability = self.analyzer._build_prompt_observability(
            "DPO and GMDSS",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(
            observability["clause_accounting"]["applied"],
            1,
        )

    def test_parse_prompt_exposes_span_backed_matches_for_compound_endorsements(self):
        constraints = self.analyzer._extract_job_constraints("DPO and GMDSS", rank=self.rank)
        parsed = self.analyzer.parse_prompt(
            "DPO and GMDSS",
            constraints,
            has_semantic_intent=False,
        )

        applied_matches = [match for match in parsed["value_matches"] if match["disposition"] == "applied"]
        self.assertEqual(len(applied_matches), 2)
        self.assertTrue(all(match["span"] is not None for match in applied_matches))
        self.assertEqual(parsed["residual_text"], "")

    def test_advanced_igf_cop_query_maps_to_endorsement(self):
        prompts = [
            "holding advanced igf cop",
            "advanced IGF certificate required",
            "must hold IGF advanced certificate of proficiency",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("stcw_endorsement", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"]["endorsements_required"],
                    ["igf_advanced_cop"],
                )

    def test_generic_igf_cop_query_is_ambiguous(self):
        constraints = self.analyzer._extract_job_constraints("holding igf cop", rank=self.rank)
        self.assertIn("IGF CoP", constraints["parsing_notes"])
        self.assertNotIn("stcw_endorsement", constraints["applied_constraints"])

    def test_tanker_igf_and_dce_prompt_variants_map_to_endorsements(self):
        cases = [
            ("holding basic igf cop", ["igf_basic_cop"]),
            ("advanced oil tanker cop required", ["tanker_oil_advanced_cop"]),
            ("basic oil tanker endorsement", ["tanker_oil_basic_cop"]),
            ("chemical tanker management", ["tanker_chemical_advanced_cop"]),
            ("gas tanker support", ["tanker_gas_basic_cop"]),
            ("oil tanker dc", ["tanker_oil"]),
            ("chemical dc", ["tanker_chemical"]),
            ("gas dc", ["tanker_gas"]),
            ("DPO and advanced gas tanker certificate required", ["tanker_gas_advanced_cop", "dp_operational"]),
        ]
        for prompt, expected_ids in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("stcw_endorsement", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"]["endorsements_required"],
                    expected_ids,
                )

    def test_common_course_prompt_variants_map_to_certifications(self):
        cases = [
            ("must have ECDIS", ["cert_ecdis"]),
            ("holding ARPA", ["cert_arpa"]),
            ("BRM required", ["cert_brm_btm"]),
            ("BTM certificate required", ["cert_brm_btm"]),
            ("ERM course required", ["cert_erm"]),
            ("holding PSCRB", ["cert_pscrb"]),
            ("AFF required", ["cert_aff"]),
            ("Medical First Aid required", ["cert_mfa"]),
            ("Medical Care certificate required", ["cert_medical_care"]),
            ("SSO required", ["cert_sso"]),
            ("ECDIS and PSCRB required", ["cert_ecdis", "cert_pscrb"]),
        ]
        for prompt, expected_ids in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("stcw_endorsement", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"]["endorsements_required"],
                    expected_ids,
                )

    def test_rank_required_certificate_prompt_uses_folder_rank_expectations(self):
        constraints = self.analyzer._extract_job_constraints("required certificates", rank="2nd_Officer")
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["gmdss", "cert_arpa", "cert_pscrb", "cert_mfa", "cert_sso"],
        )

    def test_rank_required_certificate_prompt_uses_prompt_rank_when_present(self):
        constraints = self.analyzer._extract_job_constraints(
            "chief engineer required certificates",
            rank="2nd_Officer",
        )
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["cert_erm", "cert_pscrb", "cert_mfa", "cert_aff"],
        )

    def test_rank_required_certificate_prompt_merges_specific_certificates(self):
        constraints = self.analyzer._extract_job_constraints(
            "2nd officer required certificates and PSCRB",
            rank="2nd_Officer",
        )
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["cert_pscrb", "gmdss", "cert_arpa", "cert_mfa", "cert_sso"],
        )

    def test_rank_required_certificate_prompt_merges_explicit_ecdis(self):
        constraints = self.analyzer._extract_job_constraints(
            "must have ECDIS and required certificates",
            rank="2nd_Officer",
        )
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["cert_ecdis", "gmdss", "cert_arpa", "cert_pscrb", "cert_mfa", "cert_sso"],
        )

    def test_rank_required_certificate_prompt_without_rank_is_ambiguous(self):
        constraints = self.analyzer._extract_job_constraints("required certificates", rank="")
        self.assertIn("rank certificates", constraints["parsing_notes"])
        self.assertNotIn("stcw_endorsement", constraints["applied_constraints"])

    def test_compound_igf_and_dual_fuel_prompt_keeps_both_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "holding advanced IGF CoP with dual fuel experience",
            rank="2nd_Engineer",
        )
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["engine_experience"]["engine_type"],
            "dual_fuel",
        )
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["igf_advanced_cop"],
        )

    def test_compound_certificate_and_recent_vessel_prompt_keeps_both_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "must have ECDIS and 12 months tanker experience in recent 3 contracts",
            rank="2nd_Officer",
        )
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertNotIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["vessel_type"],
            "tanker",
        )
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["min_months"],
            12,
        )
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["lookback_contracts"],
            3,
        )
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["cert_ecdis"],
        )

    def test_compound_rank_certificates_and_recent_vessel_prompt_keeps_both_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "chief officer with required certificates and container experience in last 3 contracts",
            rank="Chief_Officer",
        )
        self.assertIn("rank_match", constraints["applied_constraints"])
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertNotIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["vessel_type"],
            "container",
        )
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["gmdss", "cert_arpa", "cert_pscrb", "cert_mfa", "cert_sso", "cert_medical_care"],
        )

    def test_compound_engine_and_certificate_prompt_keeps_both_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "2nd engineer with MAN B&W experience and ERM",
            rank="2nd_Engineer",
        )
        self.assertIn("rank_match", constraints["applied_constraints"])
        self.assertIn("engine_experience", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["engine_experience"]["engine_type"],
            "man_b_w",
        )
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["cert_erm"],
        )

    def test_compound_dce_and_recent_vessel_prompt_keeps_both_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "oil tanker dc and 2 years tanker experience in last 3 contracts",
            rank="Chief_Officer",
        )
        self.assertIn("recent_contract_vessel_experience", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertNotIn("vessel_type", constraints["unapplied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["vessel_type"],
            "tanker",
        )
        self.assertEqual(
            constraints["hard_constraints"]["recent_contract_vessel_experience"]["min_months"],
            24,
        )
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["tanker_oil"],
        )

    def test_compound_required_certificates_and_dce_merge_endorsements(self):
        constraints = self.analyzer._extract_job_constraints(
            "required certificates and oil tanker dc",
            rank="Chief_Officer",
        )
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["tanker_oil", "gmdss", "cert_arpa", "cert_pscrb", "cert_mfa", "cert_sso", "cert_medical_care"],
        )

    def test_compound_valid_coc_and_required_certificates_keep_both_certification_rules(self):
        constraints = self.analyzer._extract_job_constraints(
            "required certificates and valid coc",
            rank="Chief_Officer",
        )
        self.assertIn("coc_document_gate", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertTrue(constraints["hard_constraints"]["certifications"]["coc_required"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
            ["gmdss", "cert_arpa", "cert_pscrb", "cert_mfa", "cert_sso", "cert_medical_care"],
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

    def test_coc_bare_shorthand_with_document_context_maps_to_document_gate(self):
        prompts = [
            "valid passport and COC",
            "passport and COC required",
            "need valid passport and COC",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("passport_validity", constraints["applied_constraints"])
                self.assertIn("coc_document_gate", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"],
                    {"coc_required": True, "coc_valid_required": True},
                )

    def test_coc_country_prompt_maps_to_document_and_country_rules(self):
        cases = {
            "has indian coc": "india",
            "has UK coc": "uk",
            "has Panama coc": "panama",
        }
        for prompt, expected_country in cases.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("coc_document_gate", constraints["applied_constraints"])
                self.assertIn("coc_country_match", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["certifications"],
                    {
                        "coc_required": True,
                        "coc_valid_required": True,
                        "display_value": prompt,
                    },
                )
                self.assertEqual(
                    constraints["hard_constraints"]["coc_country"],
                    {
                        "countries": [expected_country],
                        "operator": "contains_any",
                        "display_value": prompt,
                    },
                )

    def test_coc_country_prompt_normalizes_uk_aliases(self):
        cases = [
            "has British coc",
            "has United Kingdom coc",
            "has GB coc",
            "has Great Britain coc",
        ]
        for prompt in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("coc_country_match", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["coc_country"]["countries"],
                    ["uk"],
                )

    def test_coc_country_prompt_normalizes_additional_demonyms(self):
        cases = {
            "has Iranian coc": "iran",
            "has Mauritian coc": "mauritius",
            "has Maldivian coc": "maldives",
            "has Argentinian coc": "argentina",
            "has Colombian coc": "colombia",
            "has Bolivian coc": "bolivia",
            "has Peruvian coc": "peru",
            "has Venezuelan coc": "venezuela",
            "has Ecuadorian coc": "ecuador",
            "has Algerian coc": "algeria",
            "has Tunisian coc": "tunisia",
            "has Libyan coc": "libya",
            "has Moroccan coc": "morocco",
            "has Serbian coc": "serbia",
            "has Bulgarian coc": "bulgaria",
            "has Hungarian coc": "hungary",
            "has Belarusian coc": "belarus",
            "has Estonian coc": "estonia",
            "has Latvian coc": "latvia",
            "has Lithuanian coc": "lithuania",
            "has Thai coc": "thailand",
            "has Saudi coc": "saudi arabia",
            "has Bahraini coc": "bahrain",
            "has Omani coc": "oman",
            "has Qatari coc": "qatar",
            "has Emirati coc": "uae",
            "has Nigerian coc": "nigeria",
            "has Kenyan coc": "kenya",
            "has South African coc": "south africa",
        }
        for prompt, expected_country in cases.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("coc_country_match", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["coc_country"]["countries"],
                    [expected_country],
                )

    def test_coc_country_prompt_normalizes_demonym_with_grade_words(self):
        constraints = self.analyzer._extract_job_constraints("has Iranian master coc", rank=self.rank)
        self.assertIn("coc_country_match", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["coc_country"]["countries"],
            ["iran"],
        )

    def test_coc_country_prompt_normalizes_multiword_demonym_with_grade_words(self):
        constraints = self.analyzer._extract_job_constraints(
            "has South African chief officer coc",
            rank=self.rank,
        )
        self.assertIn("coc_country_match", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["coc_country"]["countries"],
            ["south africa"],
        )

    def test_coc_country_prompt_rejects_structural_phantom_countries(self):
        for prompt in (
            "Section II coc",
            "Section II of the coc rules",
            "chapter 3 coc",
            "annex VI coc",
            "has Pacific coc",
            "has Atlantic coc",
            "has Asia Pacific coc",
        ):
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertNotIn("coc_country_match", constraints["applied_constraints"])

    def test_coc_country_prompt_rejects_unknown_suffix_and_region_phantoms(self):
        for prompt in (
            "has European coc",
            "has Martian coc",
        ):
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertNotIn("coc_country_match", constraints["applied_constraints"])

    def test_coc_country_prompt_rejects_unknown_ian_phantom_countries(self):
        for prompt in (
            "Christian coc holder",
            "Olympian coc holder",
            "civilian coc",
        ):
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertNotIn("coc_country_match", constraints["applied_constraints"])

    def test_coc_grade_prompt_does_not_become_country_prompt(self):
        constraints = self.analyzer._extract_job_constraints("chief officer coc", rank=self.rank)
        self.assertIn("coc_grade_match", constraints["applied_constraints"])
        self.assertNotIn("coc_country_match", constraints["applied_constraints"])

    def test_coc_grade_family_variants_map_to_same_requirement(self):
        prompts = {
            "chief officer coc": "chief_officer",
            "coc chief officer": "chief_officer",
            "chief mate's coc": "chief_officer",
            "certificate of competency for second engineer": "2nd_engineer",
        }
        for prompt, expected_grade in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("coc_grade_match", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["coc_grade"]["required_grades"],
                    [expected_grade],
                )

    def test_coc_grade_prompt_does_not_force_matching_rank(self):
        constraints = self.analyzer._extract_job_constraints("chief mate coc", rank=self.rank)
        self.assertIn("coc_grade_match", constraints["applied_constraints"])
        self.assertNotIn("rank_match", constraints["applied_constraints"])
        self.assertEqual(
            constraints["hard_constraints"]["coc_grade"]["required_grades"],
            ["chief_officer"],
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

    def test_passport_validity_family_variants_map_to_same_requirement(self):
        prompts = [
            "valid passport",
            "passport required",
            "must have valid passport",
            "passport holder",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("passport_validity", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["passport_validity"]["requested_label"],
                    "valid passport",
                )
                self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])

    def test_passport_validity_window_variants_map_to_month_threshold(self):
        prompts = [
            "passport valid for 18 months",
            "18 months of passport validity",
            "passport should be valid for at least 18 months",
            "minimum 18 months passport validity",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("passport_validity", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["passport_validity"]["minimum_months_remaining"],
                    18,
                )

    def test_passport_current_and_up_to_date_variants_map_to_same_constraint(self):
        prompts = [
            "passport current",
            "passport up to date",
            "passport current and valid",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("passport_validity", constraints["applied_constraints"])
                self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])
                self.assertEqual(
                    constraints["hard_constraints"]["passport_validity"]["requested_label"],
                    "valid passport",
                )

    def test_passport_and_company_continuity_registry_slice_parses_supported_variants_with_empty_residual(self):
        cases = [
            {
                "prompt": "passport up to date",
                "expected_constraints": ["passport_validity"],
                "expected_hard_constraints": {
                    "passport_validity": {
                        "required": True,
                        "must_be_valid": True,
                        "requested_label": "valid passport",
                        "display_value": "passport up to date",
                    }
                },
                "expected_ledger_families": ["passport_validity"],
            },
            {
                "prompt": "passport valid for 18 months and same company for 2 contracts",
                "expected_constraints": ["passport_validity", "company_continuity"],
                "expected_hard_constraints": {
                    "passport_validity": {
                        "required": True,
                        "must_be_valid": True,
                        "minimum_months_remaining": 18,
                        "requested_label": "passport valid for at least 18 months",
                        "display_value": "passport valid for 18 months",
                    },
                    "company_continuity": {
                        "min_same_company_contract_count": 2,
                        "display_value": "same company for 2 contracts",
                        "operator": "gte",
                    },
                },
                "expected_ledger_families": ["passport_validity", "company_continuity"],
            },
        ]

        for case in cases:
            with self.subTest(prompt=case["prompt"]):
                constraints = self.analyzer._extract_job_constraints(case["prompt"], rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], case["expected_constraints"])
                self.assertEqual(constraints["hard_constraints"], case["expected_hard_constraints"])

                observability = self.analyzer._build_prompt_observability(
                    case["prompt"],
                    constraints,
                    has_semantic_intent=False,
                )

                self.assertEqual(observability["residual_text"], "")
                self.assertEqual(
                    observability["clause_accounting"],
                    {"applied": len(case["expected_constraints"]), "soft": 0, "unsupported": 0},
                )
                self.assertEqual(
                    [item["family"] for item in observability["clause_ledger"]],
                    case["expected_ledger_families"],
                )

    def test_availability_family_variants_map_to_immediate_joining(self):
        prompts = [
            "ready to join",
            "available now",
            "join now",
            "immediate join",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("availability", constraints["applied_constraints"])
                self.assertEqual(constraints["hard_constraints"]["availability"]["status"], "immediately")

    def test_availability_family_variants_map_to_notice_period_and_dates(self):
        constraints = self.analyzer._extract_job_constraints("notice period 30 days", rank=self.rank)
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["value_type"], "relative_phrase")
        self.assertEqual(constraints["hard_constraints"]["availability"]["relative_days"], 30)

        constraints = self.analyzer._extract_job_constraints("available next month", rank=self.rank)
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["value_type"], "date")
        self.assertTrue(constraints["hard_constraints"]["availability"]["available_from_date"].endswith("-28") or constraints["hard_constraints"]["availability"]["available_from_date"].endswith("-29") or constraints["hard_constraints"]["availability"]["available_from_date"].endswith("-30") or constraints["hard_constraints"]["availability"]["available_from_date"].endswith("-31"))

        from datetime import date as _date
        from calendar import monthrange as _monthrange

        today = _date.today()
        month = 1 if today.month == 12 else today.month + 1
        year = today.year + (1 if today.month == 12 else 0)
        expected = _date(year, month, _monthrange(year, month)[1]).isoformat()
        self.assertEqual(constraints["hard_constraints"]["availability"]["available_from_date"], expected)

        march_constraint = self.analyzer._extract_job_constraints("available by March", rank=self.rank)
        self.assertIn("availability", march_constraint["applied_constraints"])
        self.assertEqual(march_constraint["hard_constraints"]["availability"]["value_type"], "date")
        self.assertTrue(march_constraint["hard_constraints"]["availability"]["available_from_date"].endswith("-31"))

    def test_medical_shorthand_maps_to_medical_certificate_requirement(self):
        prompts = [
            "medical valid",
            "valid medical",
            "medical certificate required",
            "fit to sail",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("stcw_endorsement", constraints["applied_constraints"])
                self.assertIn(
                    "cert_medical_care",
                    constraints["hard_constraints"]["certifications"]["endorsements_required"],
                )

    def test_compound_ready_to_join_with_passport_and_coc_keeps_all_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "ready to join with valid passport and COC",
            rank=self.rank,
        )
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertIn("passport_validity", constraints["applied_constraints"])
        self.assertIn("coc_document_gate", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["status"], "immediately")
        self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])
        self.assertEqual(
            constraints["hard_constraints"]["certifications"],
            {"coc_required": True, "coc_valid_required": True},
        )

    def test_compound_passport_and_generic_visa_keeps_passport_only(self):
        constraints = self.analyzer._extract_job_constraints(
            "has valid passport and visa",
            rank=self.rank,
        )
        self.assertIn("passport_validity", constraints["applied_constraints"])
        self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])
        self.assertNotIn("us_visa", constraints["applied_constraints"])
        self.assertNotIn("us_visa", constraints["hard_constraints"])

    def test_compound_passport_tanker_experience_and_basic_certification_keeps_all_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "has valid passport and has relevant experience in tanker and has basic certification",
            rank=self.rank,
        )
        self.assertIn("passport_validity", constraints["applied_constraints"])
        self.assertIn("experience_ship_type", constraints["applied_constraints"])
        self.assertIn("stcw_basic", constraints["applied_constraints"])
        self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])
        self.assertEqual(constraints["hard_constraints"]["experience_ship_type"], "tanker")
        self.assertEqual(constraints["hard_constraints"]["stcw_basic"], {"required": True})

    def test_compound_rank_medical_and_passport_keeps_all_constraints(self):
        constraints = self.analyzer._extract_job_constraints(
            "2nd engineer with passport up to date, medical valid, and ready to join",
            rank=self.rank,
        )
        self.assertIn("rank_match", constraints["applied_constraints"])
        self.assertIn("passport_validity", constraints["applied_constraints"])
        self.assertIn("availability", constraints["applied_constraints"])
        self.assertIn("stcw_endorsement", constraints["applied_constraints"])
        self.assertTrue(constraints["hard_constraints"]["passport_validity"]["must_be_valid"])
        self.assertEqual(constraints["hard_constraints"]["availability"]["status"], "immediately")
        self.assertIn(
            "cert_medical_care",
            constraints["hard_constraints"]["certifications"]["endorsements_required"],
        )

    def test_rank_duration_current_rank_variants_map_to_fallback_rank(self):
        prompts = [
            "minimum 12 months in current rank",
            "12 months in rank",
            "current rank experience 18 months",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("rank_duration_experience", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["rank_duration_experience"]["rank_normalized"],
                    "2nd_engineer",
                )

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

    def test_recency_family_variants_map_to_expected_maximums(self):
        prompts = {
            "signed off in last 6 months": 6,
            "signed off within 3 months": 3,
            "last sign off within 12 months": 12,
            "last signed off in last 9 months": 9,
        }
        for prompt, expected_maximum in prompts.items():
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("recency", constraints["applied_constraints"])
                self.assertEqual(
                    constraints["hard_constraints"]["recency"]["max_months_since_sign_off"],
                    expected_maximum,
                )

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
        self.assertNotIn("stcw_endorsement", constraints["applied_constraints"])

    def test_ambiguous_dp_phrase_goes_to_parsing_notes(self):
        constraints = self.analyzer._extract_job_constraints("2nd engineer DP2", rank=self.rank)
        self.assertIn("DP2", constraints["parsing_notes"])
        self.assertNotIn("stcw_endorsement", constraints["unapplied_constraints"])
        self.assertNotIn("stcw_endorsement", constraints["applied_constraints"])

    def test_v1_candidate_with_new_family_triggers_sync_reextract(self):
        should_retry = self.analyzer._should_try_sync_reextract(
            {"facts_version": "1.1"},
            {"applied_constraints": ["availability"], "hard_constraints": {}},
        )
        self.assertTrue(should_retry)

    def test_unclassifiable_fragment_populates_parsing_notes(self):
        constraints = self.analyzer._extract_job_constraints("VLCC-ish but not exactly", rank=self.rank)
        self.assertIn("VLCC-ish but not exactly", constraints["parsing_notes"])

    def test_all_unsupported_query_leaves_applied_empty(self):
        constraints = self.analyzer._extract_job_constraints("7+ years sea service and bulk carrier", rank=self.rank)
        self.assertEqual(constraints["applied_constraints"], [])
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertIn("vessel_type", constraints["unapplied_constraints"])

    def test_recency_and_vessel_experience_registry_slice_parses_supported_variants_with_empty_residual(self):
        constraints = self.analyzer._extract_job_constraints(
            "signed off within 6 months and tanker background",
            rank=self.rank,
        )
        self.assertIn("recency", constraints["applied_constraints"])
        self.assertIn("experience_ship_type", constraints["applied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["recency"]["max_months_since_sign_off"], 6)
        self.assertEqual(constraints["hard_constraints"]["experience_ship_type"], "tanker")

        observability = self.analyzer._build_prompt_observability(
            "signed off within 6 months and tanker background",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(
            observability["clause_accounting"],
            {"applied": 2, "soft": 1, "unsupported": observability["clause_accounting"]["unsupported"]},
        )
        ledger_families = [item["family"] for item in observability["clause_ledger"]]
        self.assertIn("experience_ship_type", ledger_families)
        self.assertIn("recency", ledger_families)

    def test_min_sea_service_remains_unapplied_and_visible_in_observability(self):
        constraints = self.analyzer._extract_job_constraints("minimum 24 months sea service", rank=self.rank)
        self.assertNotIn("sea_service", constraints["applied_constraints"])
        self.assertIn("min_sea_service", constraints["unapplied_constraints"])
        self.assertEqual(constraints["hard_constraints"]["sea_service"]["min_total_months"], 24)

        observability = self.analyzer._build_prompt_observability(
            "minimum 24 months sea service",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        self.assertEqual(
            [item["family"] for item in observability["clause_ledger"] if item["disposition"] == "unsupported"],
            [],
        )
        self.assertEqual(
            [item["family"] for item in observability["clause_ledger"] if item["disposition"] == "soft"],
            ["min_sea_service"],
        )

    def test_parsing_notes_are_consumed_once_and_do_not_duplicate_residual(self):
        constraints = self.analyzer._extract_job_constraints("chief engineer with C1/D visa and DP experience", rank=self.rank)
        observability = self.analyzer._build_prompt_observability(
            "chief engineer with C1/D visa and DP experience",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "experience")
        self.assertEqual(
            [item["family"] for item in observability["clause_ledger"] if item["family"] == "parsing_note"],
            ["parsing_note"],
        )

    def test_multi_rank_prompt_records_all_rank_phrases(self):
        constraints = self.analyzer._extract_job_constraints("master and chief officer", rank=self.rank)
        observability = self.analyzer._build_prompt_observability(
            "master and chief officer",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "")
        rank_entries = [item for item in observability["clause_ledger"] if item["family"] == "rank_match"]
        self.assertEqual(len(rank_entries), 1)
        self.assertIn("master", rank_entries[0]["text"].lower())
        self.assertIn("chief officer", rank_entries[0]["text"].lower())

    def test_vessel_type_values_are_consumed_as_individual_phrases(self):
        constraints = self.analyzer._extract_job_constraints("tanker experience and offshore exposure", rank=self.rank)
        observability = self.analyzer._build_prompt_observability(
            "tanker experience and offshore exposure",
            constraints,
            has_semantic_intent=False,
        )
        self.assertEqual(observability["residual_text"], "exposure")
        families = [item["family"] for item in observability["clause_ledger"]]
        self.assertIn("experience_ship_type", families)
        self.assertIn("vessel_type", families)

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

        self.assertEqual(complete_event["applied_constraints"], ["rank_match", "availability"])
        self.assertEqual(complete_event["unapplied_constraints"], [])
        self.assertEqual(complete_event["parsing_notes"], [])
        self.assertEqual(complete_event["residual_text"], "")
        self.assertEqual(complete_event["clause_accounting"], {"applied": 2, "soft": 0, "unsupported": 0})
        self.assertEqual(
            [item["disposition"] for item in complete_event["clause_ledger"]],
            ["applied", "applied"],
        )

    def test_complete_event_reports_mixed_prompt_residual_text(self):
        filename = "2nd_Engineer_1002.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": "Present Rank: 2nd Engineer",
                    },
                }
            ]
        }
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

        events = list(
            self.analyzer.run_analysis_stream(
                self.rank,
                "2nd engineer with valid passport and has relevant experience in tanker and has basic certification",
            )
        )
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertIn("passport_validity", complete_event["applied_constraints"])
        self.assertIn("experience_ship_type", complete_event["applied_constraints"])
        self.assertIn("stcw_basic", complete_event["applied_constraints"])
        self.assertTrue(complete_event["residual_text"])
        self.assertTrue(any(item["disposition"] in {"soft", "unsupported"} for item in complete_event["clause_ledger"]))

    def test_run_analysis_result_includes_prompt_observability_fields(self):
        filename = "2nd_Engineer_1003.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": "Present Rank: 2nd Engineer",
                    },
                }
            ]
        }
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

        result = self.analyzer.run_analysis(self.rank, "2nd engineer available immediately")

        self.assertTrue(result["success"])
        self.assertEqual(result["residual_text"], "")
        self.assertEqual(result["clause_accounting"], {"applied": 2, "soft": 0, "unsupported": 0})
        self.assertEqual([item["disposition"] for item in result["clause_ledger"]], ["applied", "applied"])

    def test_prompt_observability_consumes_structured_only_age_and_rank_variants(self):
        filename = "2nd_Engineer_1004.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": "Present Rank: 2nd Engineer",
                    },
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "fact_meta": {"role.applied_rank_normalized": {"confidence": 1.0}},
            "personal": {"dob": None},
            "derived": {"age_years": 35},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": []},
        }
        self.analyzer._evaluate_hard_filters = lambda *args, **kwargs: {
            "decision": "PASS",
            "results": [],
            "evaluation_date_used": "2026-04-06",
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
        }
        llm_calls = []

        def fake_reason_with_llm(prompt, retrieved_chunks, past_feedback):
            llm_calls.append(prompt)
            return {"is_match": True, "reason": "ok", "confidence": 0.9}

        self.analyzer._reason_with_llm = fake_reason_with_llm

        cases = [
            {
                "prompt": "aged 30-50",
                "expected_constraints": ["age_range"],
                "expected_hard_constraints": {"age_years": {"min_age": 30, "max_age": 50}},
            },
            {
                "prompt": "under 40",
                "expected_constraints": ["age_range"],
                "expected_hard_constraints": {"age_years": {"min_age": None, "max_age": 39}},
            },
            {
                "prompt": "no older than fifty",
                "expected_constraints": ["age_range"],
                "expected_hard_constraints": {"age_years": {"min_age": None, "max_age": 50}},
            },
            {
                "prompt": "45 or younger",
                "expected_constraints": ["age_range"],
                "expected_hard_constraints": {"age_years": {"min_age": None, "max_age": 45}},
            },
            {
                "prompt": "chief officer",
                "expected_constraints": ["rank_match"],
                "expected_hard_constraints": {
                    "rank": {
                        "applied_rank_normalized": ["chief_officer"],
                        "operator": "contains_any",
                    }
                },
            },
            {
                "prompt": "2nd eng",
                "expected_constraints": ["rank_match"],
                "expected_hard_constraints": {
                    "rank": {
                        "applied_rank_normalized": ["2nd_engineer"],
                        "operator": "contains_any",
                    }
                },
            },
        ]

        for case in cases:
            with self.subTest(prompt=case["prompt"]):
                constraints = self.analyzer._extract_job_constraints(case["prompt"], rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], case["expected_constraints"])
                self.assertEqual(constraints["hard_constraints"], case["expected_hard_constraints"])

                observability = self.analyzer._build_prompt_observability(
                    case["prompt"],
                    constraints,
                    has_semantic_intent=False,
                )

                self.assertEqual(observability["residual_text"], "")
                self.assertEqual(
                    observability["clause_accounting"],
                    {"applied": len(case["expected_constraints"]), "soft": 0, "unsupported": 0},
                )
                self.assertEqual(
                    [item["disposition"] for item in observability["clause_ledger"]],
                    ["applied"] * len(case["expected_constraints"]),
                )

                events = list(self.analyzer.run_analysis_stream(self.rank, case["prompt"]))
                complete_event = next(event for event in events if event["type"] == "complete")
                self.assertEqual(complete_event["residual_text"], "")
                self.assertEqual(complete_event["clause_accounting"]["unsupported"], 0)

        self.assertEqual(len(llm_calls), 0)

    def test_age_and_visa_registry_slice_parses_supported_visa_variants_with_empty_residual(self):
        cases = [
            {
                "prompt": "has a valid US visa",
                "expected_constraints": ["us_visa"],
                "expected_hard_constraints": {
                    "us_visa": {
                        "required": True,
                        "must_be_valid": True,
                        "accepted_types": ["C1/D (USA)", "B1/B2 (USA)", "C1 (USA)", "D (USA)", "US Visa (USA)"],
                        "visa_group": "usa",
                        "requested_label": "valid US visa",
                    }
                },
            },
            {
                "prompt": "has a valid Schengen visa",
                "expected_constraints": ["us_visa"],
                "expected_hard_constraints": {
                    "us_visa": {
                        "required": True,
                        "must_be_valid": True,
                        "accepted_types": ["Schengen"],
                        "visa_group": "schengen",
                        "requested_label": "valid Schengen visa",
                    }
                },
            },
        ]

        for case in cases:
            with self.subTest(prompt=case["prompt"]):
                constraints = self.analyzer._extract_job_constraints(case["prompt"], rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], case["expected_constraints"])
                self.assertEqual(constraints["hard_constraints"], case["expected_hard_constraints"])

                observability = self.analyzer._build_prompt_observability(
                    case["prompt"],
                    constraints,
                    has_semantic_intent=False,
                )

                self.assertEqual(observability["residual_text"], "")
                self.assertEqual(
                    observability["clause_accounting"],
                    {"applied": 1, "soft": 0, "unsupported": 0},
                )
                self.assertEqual([item["family"] for item in observability["clause_ledger"]], ["us_visa"])

    def test_coc_grade_and_rank_duration_registry_slice_parses_supported_variants_with_empty_residual(self):
        cases = [
            {
                "prompt": "chief engineer coc",
                "expected_constraints": ["coc_grade_match"],
                "expected_hard_constraints": {
                    "coc_grade": {
                        "required_grades": ["chief_engineer"],
                        "operator": "contains_any",
                        "display_value": "chief engineer coc",
                    },
                },
                "expected_ledger_families": ["coc_grade_match"],
            },
            {
                "prompt": "minimum 12 months in current rank",
                "expected_constraints": ["rank_duration_experience"],
                "expected_hard_constraints": {
                    "rank_duration_experience": {
                        "rank_normalized": "2nd_engineer",
                        "min_months": 12,
                        "requested_label": "at least 12 months as 2nd engineer",
                        "display_value": "minimum 12 months in current rank",
                    }
                },
                "expected_ledger_families": ["rank_duration_experience"],
            },
        ]

        for case in cases:
            with self.subTest(prompt=case["prompt"]):
                constraints = self.analyzer._extract_job_constraints(case["prompt"], rank=self.rank)
                self.assertEqual(constraints["applied_constraints"], case["expected_constraints"])
                self.assertEqual(constraints["hard_constraints"], case["expected_hard_constraints"])

                observability = self.analyzer._build_prompt_observability(
                    case["prompt"],
                    constraints,
                    has_semantic_intent=False,
                )

                self.assertEqual(observability["residual_text"], "")
                self.assertEqual(
                    observability["clause_accounting"],
                    {"applied": len(case["expected_constraints"]), "soft": 0, "unsupported": 0},
                )
                self.assertEqual(
                    [item["family"] for item in observability["clause_ledger"]],
                    case["expected_ledger_families"],
                )

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

    def test_recruiter_like_unsupported_prompt_uses_semantic_fallback_with_warning(self):
        llm_called = {"value": False}

        self.analyzer.prepper = _FakePrepper(embeddings=[])
        self.analyzer._retrieve_candidates_keyword_fallback = lambda *_args, **_kwargs: {
            "piracy-route-candidate": [
                {
                    "id": "chunk-1",
                    "score": 0.99,
                    "metadata": {
                        "resume_id": "piracy-route-candidate",
                        "rank": self.rank,
                        "raw_text": "Experience in piracy-prone routes",
                        "source_path": str(self.rank_folder / "piracy_route_resume.pdf"),
                    },
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

        def fake_reason_with_llm(*_args, **_kwargs):
            llm_called["value"] = True
            return {"is_match": True, "reason": "semantic match", "confidence": 0.7}

        self.analyzer._reason_with_llm = fake_reason_with_llm

        events = list(self.analyzer.run_analysis_stream(self.rank, "has experiencee in piracy routes"))
        complete_event = next(event for event in events if event["type"] == "complete")

        self.assertFalse(any(event["type"] == "graceful_failure" for event in events))
        self.assertTrue(llm_called["value"])
        self.assertIn("semantic search instead", complete_event["search_warning"])
        self.assertEqual(complete_event["hard_filter_summary"]["matched"], 1)

    def test_route_oriented_prompt_is_recruiter_like_for_semantic_fallback(self):
        self.assertTrue(
            self.analyzer._is_recruiter_like_semantic_fallback_prompt(
                "has experiencee in piracy routes",
                {"applied_constraints": [], "hard_constraints": {}, "unapplied_constraints": []},
            )
        )
        self.assertFalse(
            self.analyzer._is_recruiter_like_semantic_fallback_prompt(
                "good candidate",
                {"applied_constraints": [], "hard_constraints": {}, "unapplied_constraints": []},
            )
        )

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

    def test_structured_only_recency_pass_skips_llm_and_surfaces_match(self):
        filename = "Chief-Engineer_Container_12309_2026-01-29_16-35-43.pdf"
        (self.rank_folder / filename).write_bytes(b"%PDF-1.4")

        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": "chunk-1",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": "Sign Out Date 20-Oct-2025",
                    },
                }
            ]
        }
        self.analyzer._build_candidate_facts = lambda *args, **kwargs: {
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
            "role": {"applied_rank_normalized": None},
            "fact_meta": {"experience.last_sign_off_date": {"confidence": 0.9, "status": "PARSED"}},
            "personal": {"dob": None},
            "derived": {"age_years": None},
            "application": {"applied_ship_types": []},
            "experience": {"vessel_types": [], "last_sign_off_date": "2025-10-20", "last_sign_off_months_ago": 6},
        }
        self.analyzer._evaluate_hard_filters = lambda *args, **kwargs: {
            "decision": "PASS",
            "results": [{
                "decision": "PASS",
                "reason_code": "RECENCY_MATCH",
                "message": "Candidate signed off within the requested 6 months.",
                "actual_value": {"last_sign_off_date": "2025-10-20", "months_ago": 6},
                "expected_value": {"max_months_since_sign_off": 6},
                "confidence": 0.9,
                "unknown_reason": None,
            }],
            "evaluation_date_used": "2026-05-11",
            "facts_version": AIResumeAnalyzer.FACTS_VERSION,
        }
        self.analyzer._reason_with_llm = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should be skipped"))

        events = list(self.analyzer.run_analysis_stream(self.rank, "signed off in last 6 months"))
        match_event = next(event for event in events if event["type"] == "match_found")

        self.assertEqual(match_event["match"]["confidence"], 1.0)
        self.assertEqual(match_event["match"]["reason"], "Candidate signed off within the requested 6 months.")

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
                        "raw_text": "Worked with strong leadership under pressure",
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

        events = list(self.analyzer.run_analysis_stream(self.rank, "strong leadership under pressure"))
        match_event = next(event for event in events if event["type"] == "match_found")

        self.assertEqual(match_event["match"]["filename"], filename)

    def test_scoped_semantic_search_evaluates_only_scope_without_broad_retrieval(self):
        selected_filename = "2nd_Engineer_Selected.pdf"
        outside_filename = "2nd_Engineer_Outside.pdf"
        selected_path = self.rank_folder / selected_filename
        outside_path = self.rank_folder / outside_filename
        selected_path.write_bytes(b"%PDF-1.4\nselected")
        outside_path.write_bytes(b"%PDF-1.4\noutside")

        class _ScopedRegistry(_FakeRegistry):
            def get_resume_identity_record(self, file_path):
                path = Path(file_path)
                if path == selected_path:
                    return {
                        "resume_id": "resume-selected",
                        "candidate_scope_id": "scope-selected",
                        "content_hash": "hash-selected",
                    }
                if path == outside_path:
                    return {
                        "resume_id": "resume-outside",
                        "candidate_scope_id": "scope-outside",
                        "content_hash": "hash-outside",
                    }
                return {}

        class _PdfProcessor:
            def extract_text(self, file_path):
                return f"Strong leadership under pressure: {Path(file_path).stem}"

        class _NoBroadVectorSearch:
            last_error = None

            def query(self, *_args, **_kwargs):
                raise AssertionError("scoped search must not call broad vector retrieval")

        self.analyzer.registry = _ScopedRegistry()
        self.analyzer.pdf_processor = _PdfProcessor()
        self.analyzer.vector_db = _NoBroadVectorSearch()
        self.analyzer._retrieve_for_subquery = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scoped search must not call compound retrieval")
        )
        self.analyzer._retrieve_candidates_keyword_fallback = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scoped search must not call keyword retrieval")
        )
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
        self.analyzer._reason_with_llm = lambda *args, **kwargs: {
            "is_match": True,
            "reason": "ok",
            "confidence": 0.9,
        }

        events = list(self.analyzer.run_analysis_stream(
            self.rank,
            "strong leadership under pressure",
            candidate_scope_ids=["scope-selected"],
            candidate_scope_memberships=[{
                "candidate_scope_id": "scope-selected",
                "content_hash_at_event": "hash-selected",
            }],
        ))

        match_events = [event for event in events if event["type"] == "match_found"]
        complete_event = next(event for event in events if event["type"] == "complete")
        self.assertEqual([event["match"]["filename"] for event in match_events], [selected_filename])
        self.assertEqual(complete_event["hard_filter_summary"]["scanned"], 1)
        self.assertEqual(complete_event["scope_summary"]["requested_count"], 1)
        self.assertEqual(complete_event["scope_summary"]["resolved_count"], 1)

    def test_empty_scoped_search_fails_before_ingestion_or_full_scan(self):
        self.analyzer._ingest_folder = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty scope must fail before ingestion")
        )
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty scope must not become a full scan")
        )

        events = list(self.analyzer.run_analysis_stream(
            self.rank,
            "strong leadership under pressure",
            candidate_scope_ids=[],
        ))

        failure_event = next(event for event in events if event["type"] == "graceful_failure")
        self.assertEqual(failure_event["error_code"], "REFINEMENT_SCOPE_EMPTY")
        self.assertEqual(failure_event["scope_summary"]["requested_count"], 0)

    def test_changed_content_scope_is_not_blocked_after_backend_ack_gate(self):
        self.analyzer._enumerate_scoped_rank_candidates = lambda *_args, **_kwargs: (
            {"resume-selected": [{"metadata": {"resume_id": "resume-selected"}}]},
            {
                "eligible_population_count": 1,
                "retrieved_count": None,
                "evaluated_count": 0,
                "requested_count": 1,
                "resolved_count": 1,
                "changed_content_count": 1,
                "stale_count": 0,
                "unresolvable_count": 0,
                "duplicate_count": 0,
            },
        )
        self.analyzer._normalize_rank_candidates = lambda *_args, **_kwargs: ({}, {}, 0, 0)

        events = list(self.analyzer.run_analysis_stream(
            self.rank,
            "strong leadership under pressure",
            candidate_scope_ids=["scope-selected"],
        ))

        self.assertFalse(any(
            event.get("error_code") == "REFINEMENT_CHANGED_CONTENT_ACK_REQUIRED"
            for event in events
        ))

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
        for idx, filename in enumerate(filenames):
            (self.rank_folder / filename).write_bytes(f"%PDF-1.4-{idx}".encode("utf-8"))

        self.analyzer.SYNC_REEXTRACT_PER_SEARCH_LIMIT = 1
        self.analyzer._enumerate_rank_candidates = lambda *_args, **_kwargs: {
            Path(filename).stem: [
                {
                    "id": f"chunk-{Path(filename).stem}",
                    "score": 1.0,
                    "metadata": {
                        "resume_id": Path(filename).stem,
                        "rank": self.rank,
                        "raw_text": f"Present Rank: 2nd Engineer ({Path(filename).stem})",
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
