import sys
import tempfile
import types
import unittest
import configparser
from datetime import date
from pathlib import Path
from unittest.mock import patch


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
from ai_analyzer import AIResumeAnalyzer, SupabaseFileRegistry  # noqa: E402


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
                        "source_path": str(self.rank_folder / spec["filename"]),
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
                "travel": {
                    "us_visa_status": "PARSED",
                    "visa_records": [{
                        "status": "PARSED",
                        "visa_type": "US Visa (USA)",
                        "visa_group": "usa",
                        "expiry_date": date(2027, 5, 4),
                        "expiry_status": "PARSED",
                    }],
                    "visa_types": ["US Visa (USA)"],
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
        audit_by_filename = {entry["filename"]: entry for entry in complete_event["hard_filter_audit"]}
        self.assertEqual(audit_by_filename["2nd_Engineer_120969.pdf"]["hard_filter_decision"], "FAIL")
        self.assertEqual(audit_by_filename["2nd_Engineer_unknown.pdf"]["hard_filter_decision"], "UNKNOWN")
        self.assertEqual(audit_by_filename["2nd_Engineer_315781.pdf"]["result_bucket"], "verified_match")
        self.assertTrue(audit_by_filename["2nd_Engineer_315781.pdf"]["llm_reached"])

        self.assertEqual(len(llm_calls), 2)
        llm_prompts = [call["prompt"] for call in llm_calls]
        self.assertTrue(all("deterministic age gate" in prompt for prompt in llm_prompts))
        self.assertTrue(all("2nd_Engineer_120969.pdf" not in prompt for prompt in llm_prompts))
        self.assertTrue(all("2nd_Engineer_unknown.pdf" not in prompt for prompt in llm_prompts))

    def test_visa_only_prompt_does_not_inject_age_gate_language(self):
        resume_specs = [
            {"filename": "2nd_Engineer_349740.pdf", "dob": date(1989, 11, 4), "age": 36, "status": "PARSED"},
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
                        "source_path": str(self.rank_folder / spec["filename"]),
                        "raw_text": "US Visa valid until 04-May-2027",
                    },
                }
            ]
            for spec in resume_specs
        }
        self.analyzer.prepper = types.SimpleNamespace(
            get_embeddings=lambda prompts: [[0.1] for _ in prompts],
            last_error=None,
        )
        self.analyzer.vector_db = types.SimpleNamespace(
            query=lambda *_args, **_kwargs: [
                {
                    "score": 0.99,
                    "metadata": {
                        "resume_id": Path(resume_specs[0]["filename"]).stem,
                        "rank": self.rank,
                        "source_path": str(self.rank_folder / resume_specs[0]["filename"]),
                        "raw_text": "US Visa valid until 04-May-2027",
                    },
                }
            ],
            last_error=None,
        )

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
                "travel": {
                    "us_visa_status": "PARSED",
                    "visa_records": [{
                        "status": "PARSED",
                        "visa_type": "US Visa (USA)",
                        "visa_group": "usa",
                        "expiry_date": date(2027, 5, 4),
                        "expiry_status": "PARSED",
                    }],
                    "visa_types": ["US Visa (USA)"],
                },
            }

        llm_calls = []

        def fake_reason_with_llm(prompt, retrieved_chunks, past_feedback):
            llm_calls.append(prompt)
            return {
                "is_match": True,
                "reason": "Candidate has a valid US visa.",
                "confidence": 0.95,
            }

        self.analyzer._build_candidate_facts = fake_build_candidate_facts
        self.analyzer._reason_with_llm = fake_reason_with_llm

        events = list(
            self.analyzer.run_analysis_stream(
                self.rank,
                "has a valid US visa",
            )
        )

        complete_event = next(event for event in events if event["type"] == "complete")
        self.assertEqual(len(complete_event["verified_matches"]), 1)
        self.assertEqual(len(llm_calls), 1)
        self.assertNotIn("Computed candidate age from DOB", llm_calls[0])
        self.assertNotIn("deterministic age gate", llm_calls[0])

    def test_age_rule_marks_implausibly_low_or_high_age_as_unknown(self):
        low_result = self.analyzer._evaluate_age_rule(
            {
                "personal": {"dob": date(2020, 1, 1), "dob_parse_status": "PARSED"},
                "derived": {"age_years": 6},
                "fact_meta": {"derived.age_years": {"confidence": 0.99}},
            },
            {"min_age": 30, "max_age": 50},
        )
        high_result = self.analyzer._evaluate_age_rule(
            {
                "personal": {"dob": date(1900, 1, 1), "dob_parse_status": "PARSED"},
                "derived": {"age_years": 126},
                "fact_meta": {"derived.age_years": {"confidence": 0.99}},
            },
            {"min_age": 30, "max_age": 50},
        )
        self.assertEqual(low_result["decision"], "UNKNOWN")
        self.assertEqual(low_result["reason_code"], "INVALID_AGE_TOO_LOW")
        self.assertEqual(high_result["decision"], "UNKNOWN")
        self.assertEqual(high_result["reason_code"], "INVALID_AGE_TOO_HIGH")

    def test_age_rule_marks_explicit_age_conflict_as_unknown(self):
        result = self.analyzer._evaluate_age_rule(
            {
                "personal": {
                    "dob": date(1989, 11, 4),
                    "dob_parse_status": "PARSED",
                    "stated_age": 31,
                    "stated_age_status": "PARSED",
                },
                "derived": {"age_years": 36},
                "fact_meta": {"derived.age_years": {"confidence": 0.99}},
            },
            {"min_age": 30, "max_age": 50},
        )
        self.assertEqual(result["decision"], "UNKNOWN")
        self.assertEqual(result["reason_code"], "DATA_CONFLICT")
        self.assertIn("explicitly stated age", result["message"])

    def test_hard_filter_output_includes_evaluation_metadata(self):
        result = self.analyzer._evaluate_hard_filters(
            {
                "personal": {"dob": date(1989, 11, 4), "dob_parse_status": "PARSED"},
                "derived": {"age_years": 36},
                "fact_meta": {"derived.age_years": {"confidence": 0.99}},
            },
            {
                "applied_constraints": ["age_range"],
                "hard_constraints": {"age_years": {"min_age": 30, "max_age": 50}},
            },
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("evaluation_date_used", result)
        self.assertEqual(result["facts_version"], AIResumeAnalyzer.FACTS_VERSION)
        self.assertEqual(result["results"][0]["actual_value"], 36)
        self.assertEqual(result["results"][0]["expected_value"], {"min_age": 30, "max_age": 50})

    # ------------------------------------------------------------------
    # _extract_stated_age_fact_from_text — disqualifying prefix guard
    # ------------------------------------------------------------------

    def test_stated_age_extraction_ignores_vessel_age(self):
        """Vessel age, engine age, etc. must not be treated as candidate age."""
        disqualifying_cases = [
            "vessel age: 15 years",
            "ship age 8",
            "engine age: 12",
            "document age: 3 years",
            "course age: 2",
            "sea age 5",
            "charter age: 7",
        ]
        for text in disqualifying_cases:
            with self.subTest(text=text):
                result = self.analyzer._extract_stated_age_fact_from_text(text)
                self.assertEqual(result["status"], "MISSING", msg=f"Should not extract from: {text!r}")
                self.assertIsNone(result["age"])

    def test_stated_age_extraction_accepts_plain_candidate_age(self):
        """Explicit candidate age labels without disqualifying prefixes must be extracted."""
        valid_cases = [
            ("Age: 36", 36),
            ("age 29", 29),
            ("Aged 42", 42),
            ("Candidate Age: 31", 31),
        ]
        for text, expected_age in valid_cases:
            with self.subTest(text=text):
                result = self.analyzer._extract_stated_age_fact_from_text(text)
                self.assertEqual(result["status"], "PARSED", msg=f"Should extract from: {text!r}")
                self.assertEqual(result["age"], expected_age)

    # ------------------------------------------------------------------
    # _check_age_conflict — out-of-range stated age is ignored
    # ------------------------------------------------------------------

    def test_conflict_detection_ignores_out_of_range_stated_age(self):
        """Stated ages outside 14-80 are almost certainly extraction artefacts."""
        out_of_range_cases = [0, 5, 13, 81, 150, 200]
        for stated_age in out_of_range_cases:
            with self.subTest(stated_age=stated_age):
                result = self.analyzer._check_age_conflict(
                    36,
                    {"age": stated_age, "status": "PARSED"},
                )
                self.assertIsNone(result, msg=f"Should ignore stated_age={stated_age}")

    def test_conflict_detection_triggers_on_plausible_disagreement(self):
        """A stated age within 14-80 that disagrees with DOB by more than 2 years triggers DATA_CONFLICT."""
        result = self.analyzer._check_age_conflict(
            36,
            {"age": 25, "status": "PARSED"},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["reason_code"], "DATA_CONFLICT")

    def test_conflict_detection_passes_when_ages_agree(self):
        """A stated age within 2 years of DOB-derived age must not conflict."""
        result = self.analyzer._check_age_conflict(
            36,
            {"age": 37, "status": "PARSED"},
        )
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # _evaluate_rule — new operators
    # ------------------------------------------------------------------

    def test_evaluate_rule_gte(self):
        self.assertTrue(self.analyzer._evaluate_rule("gte", 10, 5))
        self.assertTrue(self.analyzer._evaluate_rule("gte", 5, 5))
        self.assertFalse(self.analyzer._evaluate_rule("gte", 4, 5))

    def test_evaluate_rule_lte(self):
        self.assertTrue(self.analyzer._evaluate_rule("lte", 4, 5))
        self.assertTrue(self.analyzer._evaluate_rule("lte", 5, 5))
        self.assertFalse(self.analyzer._evaluate_rule("lte", 6, 5))

    def test_evaluate_rule_contains_all(self):
        self.assertTrue(self.analyzer._evaluate_rule("contains_all", ["tanker", "bulk"], ["tanker"]))
        self.assertTrue(self.analyzer._evaluate_rule("contains_all", ["tanker", "bulk"], ["tanker", "bulk"]))
        self.assertFalse(self.analyzer._evaluate_rule("contains_all", ["tanker"], ["tanker", "bulk"]))
        self.assertFalse(self.analyzer._evaluate_rule("contains_all", [], ["tanker"]))

    def test_evaluate_rule_raises_on_none_actual_value(self):
        with self.assertRaises(ValueError):
            self.analyzer._evaluate_rule("gte", None, 5)

    def test_evaluate_rule_raises_on_unsupported_operator(self):
        with self.assertRaises(ValueError):
            self.analyzer._evaluate_rule("fuzzy_match", "tanker", "tanker")


class SupabaseFileRegistryTests(unittest.TestCase):
    @patch("ai_analyzer.requests.request")
    def test_upsert_file_record_uses_merge_duplicates_prefer_header(self, request_mock):
        class _Resp:
            status_code = 201
            text = ""

            def json(self):
                return []

        request_mock.return_value = _Resp()

        with patch.dict(
            "os.environ",
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SECRET_KEY": "secret",
            },
            clear=False,
        ):
            registry = SupabaseFileRegistry()
            registry.upsert_file_record("/tmp/2nd_Engineer_288.pdf", 123.0, "resume-1")

        kwargs = request_mock.call_args.kwargs
        self.assertEqual(kwargs["params"], {"on_conflict": "file_key"})
        self.assertEqual(
            kwargs["headers"]["Prefer"],
            "resolution=merge-duplicates,return=minimal",
        )


if __name__ == "__main__":
    unittest.main()
