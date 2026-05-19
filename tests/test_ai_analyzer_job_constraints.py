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

    def test_engine_experience_family_maps_to_structured_constraint(self):
        cases = {
            "with me engine experience": "man_b_w_me",
            "has MAN B&W experience": "man_b_w_me",
            "WinGD X-DF experience": "wingd_x_df",
            "Wartsila 32DF experience": "wartsila_dual_fuel",
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
        ]
        for prompt, expected_engine_type, expected_months, expected_contracts in cases:
            with self.subTest(prompt=prompt):
                constraints = self.analyzer._extract_job_constraints(prompt, rank=self.rank)
                self.assertIn("engine_experience", constraints["applied_constraints"])
                engine_constraint = constraints["hard_constraints"]["engine_experience"]
                self.assertEqual(engine_constraint["engine_type"], expected_engine_type)
                self.assertEqual(engine_constraint["min_months"], expected_months)
                self.assertEqual(engine_constraint["lookback_contracts"], expected_contracts)

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
            ("last 3 contracts on oil tanker with MAN B&W engine", "man_b_w_me", "oil tanker", 3),
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
