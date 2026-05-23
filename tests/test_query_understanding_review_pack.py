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


if __name__ == "__main__":
    unittest.main()
