import sys
import types
import unittest


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

from query_understanding.legacy_parser_adapter import LegacyParserAdapter


class LegacyParserAdapterTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.adapter = LegacyParserAdapter(self.analyzer)

    def _family_ids(self, plan):
        return [item["id"] for item in plan["applied_constraints"]], [item["id"] for item in plan["unapplied_constraints"]]

    def test_adapter_preserves_current_parser_semantics_for_known_prompts(self):
        cases = [
            (
                "2nd engineer between 30 and 50 years old",
                "2nd Engineer",
                ["age_range", "rank_match"],
                [],
            ),
            (
                "minimum 5 years sea service",
                "2nd Engineer",
                [],
                ["min_sea_service"],
            ),
            (
                "2 years tanker experience in last 3 contracts",
                "2nd Engineer",
                ["recent_contract_vessel_experience"],
                [],
            ),
        ]

        for prompt, rank, expected_applied, expected_unapplied in cases:
            with self.subTest(prompt=prompt):
                legacy = self.analyzer._extract_job_constraints(prompt, rank=rank)
                adapted = self.adapter.adapt(prompt, rank=rank)
                applied_ids, unapplied_ids = self._family_ids(adapted)
                self.assertEqual(applied_ids, expected_applied)
                self.assertEqual(unapplied_ids, expected_unapplied)
                self.assertEqual(applied_ids, legacy["applied_constraints"] if expected_applied else [])
                self.assertEqual(unapplied_ids, legacy["unapplied_constraints"] if expected_unapplied else [])

    def test_adapter_carries_legacy_payloads_into_query_plan_shape(self):
        adapted = self.adapter.adapt("2 years tanker experience in last 3 contracts", rank="2nd Engineer")
        constraint = adapted["applied_constraints"][0]["constraint"]
        self.assertEqual(constraint["type"], "recent_contract_vessel_experience")
        self.assertEqual(constraint["ship_family"], "tanker")
        self.assertEqual(constraint["recent_contract_count"], 3)

    def test_adapter_preserves_logical_groups(self):
        adapted = self.adapter.adapt(
            "has lng vessel or dual fuel vessel or win gd vessel expereince",
            rank="2nd Engineer",
        )

        self.assertEqual(adapted["validation"]["status"], "valid")
        self.assertEqual(adapted["applied_constraints"], [])
        groups = adapted["logical_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["type"], "any_of")
        self.assertEqual(groups[0]["mode"], "required")
        self.assertEqual(groups[0]["confidence"], "high")
        self.assertEqual(
            [(child["id"], child["constraint"]["type"]) for child in groups[0]["children"]],
            [
                ("experience_ship_type", "experience_ship_type"),
                ("engine_experience", "engine_experience"),
                ("engine_experience", "engine_experience"),
            ],
        )
        self.assertEqual(groups[0]["children"][0]["constraint"]["ship_family"], "lng")
        self.assertEqual(groups[0]["children"][1]["constraint"]["engine_family"], "dual_fuel")
        self.assertEqual(groups[0]["children"][2]["constraint"]["engine_family"], "wingd_x_engines")

    def test_adapter_canonicalizes_ship_family_aliases_from_configured_parser(self):
        legacy = {
            "rank": "",
            "hard_constraints": {
                "recent_contract_vessel_experience": {
                    "vessel_type": "container vessel",
                    "min_months": 12,
                    "lookback_contracts": 3,
                    "display_value": "container vessel experience for 12 months over last 3 contracts",
                }
            },
            "applied_constraints": ["recent_contract_vessel_experience"],
            "unapplied_constraints": [],
            "parsing_notes": [],
        }

        adapted = self.adapter.from_legacy_constraints(
            legacy,
            user_prompt="container vessel experience for 12 months over last 3 contracts",
        )

        self.assertEqual(adapted["validation"]["status"], "valid")
        constraint = adapted["applied_constraints"][0]["constraint"]
        self.assertEqual(constraint["ship_family"], "container")

    def test_adapter_preserves_semantic_tail_for_rank_mixed_prompt(self):
        adapted = self.adapter.adapt("2nd engineer with strong leadership", rank="2nd Engineer")
        self.assertIn("strong leadership", adapted["semantic_query"])
        self.assertNotEqual(adapted["semantic_query"], "")

    def test_adapter_preserves_semantic_tail_for_age_mixed_prompt(self):
        adapted = self.adapter.adapt("between 30 and 50 years old with strong leadership", rank="2nd Engineer")
        self.assertIn("strong leadership", adapted["semantic_query"])
        self.assertNotEqual(adapted["semantic_query"], "")

    def test_adapter_removes_experience_residue_for_experience_family(self):
        adapted = self.adapter.adapt("2 years tanker experience in last 3 contracts with strong leadership", rank="2nd Engineer")
        semantic_query = adapted["semantic_query"].lower()
        self.assertIn("strong leadership", semantic_query)
        self.assertNotIn("experience", semantic_query)
        self.assertNotIn("contracts", semantic_query)

    def test_adapter_preserves_semantic_connectors_around_experience_family(self):
        adapted = self.adapter.adapt(
            "2nd engineer with tanker experience and interest in leadership",
            rank="2nd Engineer",
        )
        semantic_query = adapted["semantic_query"].lower()
        self.assertIn("interest in leadership", semantic_query)
        self.assertNotEqual(semantic_query, "interest leadership")

    def test_adapter_cleans_connector_scaffolding_for_passport_and_visa_prompts(self):
        passport_adapted = self.adapter.adapt("2nd engineer with valid passport and strong leadership", rank="2nd Engineer")
        visa_adapted = self.adapter.adapt("2nd engineer with valid us visa and strong leadership", rank="2nd Engineer")
        self.assertIn("strong leadership", passport_adapted["semantic_query"].lower())
        self.assertIn("strong leadership", visa_adapted["semantic_query"].lower())
        self.assertNotIn("with and", passport_adapted["semantic_query"].lower())
        self.assertNotIn("with and", visa_adapted["semantic_query"].lower())


if __name__ == "__main__":
    unittest.main()
