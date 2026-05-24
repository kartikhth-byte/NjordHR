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

from candidate_facts.audit import build_candidate_resume_facts_audit_metadata  # noqa: E402
from query_understanding.shadow_audit import build_shadow_audit_entry  # noqa: E402


class CandidateFactsAuditTests(unittest.TestCase):
    def test_build_candidate_resume_facts_audit_metadata_records_pinned_tuple(self):
        row = {
            "id": "candidate_resume_facts:1234",
            "candidate_id": "candidate-1",
            "candidate_resume_id": "candidate-resume-1",
            "resume_blob_id": "blob-1",
            "schema_version": "candidate_facts.v1",
            "parser_version": "legacy_bridge.v1",
            "facts_revision": "rev-2",
        }
        metadata = build_candidate_resume_facts_audit_metadata(
            row,
            resolution={"status": "resolved", "reason": "identity"},
        )
        self.assertEqual(metadata["candidate_resume_facts_id"], "candidate_resume_facts:1234")
        self.assertEqual(metadata["pinned_facts_identity"], {
            "candidate_resume_id": "candidate-resume-1",
            "schema_version": "candidate_facts.v1",
            "parser_version": "legacy_bridge.v1",
            "facts_revision": "rev-2",
        })
        self.assertEqual(metadata["selection_status"], "resolved")
        self.assertEqual(metadata["selection_reason"], "identity")
        self.assertEqual(metadata["replay_lookup_kind"], "identity_tuple")

    def test_build_candidate_resume_facts_audit_metadata_reports_id_lookup(self):
        row = {
            "id": "candidate_resume_facts:1234",
            "candidate_id": "candidate-1",
            "candidate_resume_id": "candidate-resume-1",
            "resume_blob_id": "blob-1",
            "schema_version": "candidate_facts.v1",
            "parser_version": "legacy_bridge.v1",
            "facts_revision": "rev-2",
        }
        metadata = build_candidate_resume_facts_audit_metadata(
            row,
            resolution={"status": "resolved", "reason": "id"},
        )
        self.assertEqual(metadata["replay_lookup_kind"], "candidate_resume_facts_id")

    def test_build_candidate_resume_facts_audit_metadata_handles_missing_row(self):
        metadata = build_candidate_resume_facts_audit_metadata(None)
        self.assertEqual(metadata["selection_status"], "unavailable")
        self.assertIsNone(metadata["candidate_resume_facts_id"])
        self.assertEqual(metadata["replay_lookup_kind"], "identity_tuple")

    def test_shadow_audit_entry_includes_candidate_facts_audit_placeholder(self):
        class _Analyzer:
            def _extract_job_constraints(self, user_prompt, rank=None):
                return {
                    "hard_constraints": {
                        "rank": {
                            "applied_rank_normalized": ["2nd_engineer"],
                            "display_value": rank or "2nd Engineer",
                        },
                        "age_years": {"min_age": 30, "max_age": 45},
                    },
                    "semantic_query": user_prompt,
                    "parsing_notes": [],
                }

        entry = build_shadow_audit_entry(
            _Analyzer(),
            "2nd engineer between 30 and 45 years old",
            prompt_id="prompt-1",
        )
        self.assertIn("candidate_facts_audit", entry)
        self.assertEqual(entry["candidate_facts_audit"]["selection_status"], "unavailable")
        self.assertIsNone(entry["candidate_facts_audit"]["candidate_resume_facts_id"])


if __name__ == "__main__":
    unittest.main()
