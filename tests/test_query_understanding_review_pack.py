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

from scripts.query_understanding_review_pack import _build_candidate_facts_replay  # noqa: E402
from query_understanding.shadow_audit import build_shadow_audit_rows


class QueryUnderstandingReviewPackTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)

    def test_shadow_audit_rows_are_buildable_for_bootstrap_prompts(self):
        prompts = [
            {"prompt_id": "age_range:1", "prompt": "2nd engineer between 30 and 45 years old"},
            {"prompt_id": "us_visa:1", "prompt": "2nd engineer with valid us visa"},
        ]
        rows = build_shadow_audit_rows(self.analyzer, prompts, rank="2nd Engineer")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["shadow_mode"], "disabled")
        self.assertIn("age_range", {record["family"] for record in rows[0]["legacy_comparison_records"]})
        self.assertIn("us_visa", {record["family"] for record in rows[1]["legacy_comparison_records"]})

    def test_review_pack_candidate_facts_replay_uses_repository_adapter(self):
        candidate_facts_replay = _build_candidate_facts_replay()
        self.assertIn("candidate_facts", candidate_facts_replay)
        self.assertIn("persist", candidate_facts_replay)
        self.assertIn("replay", candidate_facts_replay)
        self.assertIn("audit", candidate_facts_replay)
        self.assertIn("review_capture", candidate_facts_replay)
        self.assertIn("review_cache_dir", candidate_facts_replay)
        self.assertEqual(candidate_facts_replay["candidate_facts"]["validation"]["status"], "valid")
        self.assertEqual(candidate_facts_replay["persist"]["committed"], True)
        self.assertEqual(candidate_facts_replay["replay"]["status"], "resolved")
        self.assertEqual(candidate_facts_replay["audit"]["selection_status"], "resolved")
        self.assertEqual(candidate_facts_replay["review_capture"]["review_item"]["review_status"], "pending_review")
        self.assertEqual(
            candidate_facts_replay["audit"]["pinned_facts_identity"]["candidate_resume_id"],
            "candidate-resume-1",
        )


if __name__ == "__main__":
    unittest.main()
