import sys
import types
import unittest
import tempfile


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

from candidate_facts.repository import CandidateFactsRepository  # noqa: E402


class _FakeAnalyzer:
    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        return {
            "candidate_id": filename,
            "identity": {"full_name": "Jane Doe"},
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "personal": {"dob": "1988-02-03"},
            "certifications": {
                "coc": {"grade": "chief_officer", "expiry_date": "2028-01-01", "status": "VALID"},
                "stcw_basic_all_valid": True,
                "endorsements": {"tanker_gas": "advanced"},
            },
            "logistics": {
                "passport_expiry_date": "2029-01-01",
                "passport_valid": True,
                "us_visa_status": "VALID",
                "us_visa_expiry_date": "2028-06-01",
            },
            "experience": {
                "service_rows": [
                    {
                        "rank_normalized": "2nd_engineer",
                        "vessel_name": "MV Aurora",
                        "months_total": 60,
                    }
                ],
                "rank_duration_rows": [
                    {
                        "rank_normalized": "2nd_engineer",
                        "months_total": 60,
                    }
                ],
            },
            "application": {"applied_ship_types": ["tanker"]},
            "derived": {
                "age_years": 37,
                "current_rank_months_total": 60,
                "same_company_contract_count_max": 2,
                "has_contract_gap_over_6_months": False,
            },
        }


class CandidateFactsRepositoryTests(unittest.TestCase):
    def test_repository_builds_persists_and_replays_candidate_facts(self):
        repo = CandidateFactsRepository()
        result = repo.build_persist_replay_audit(
            _FakeAnalyzer(),
            "resume-1",
            "2nd Engineer",
            [],
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
            folder_metadata={},
            source_origin="seajobs_download",
            detected_layout="seajobs",
        )
        self.assertEqual(result["candidate_facts"]["validation"]["status"], "valid")
        self.assertTrue(result["persist"]["committed"])
        self.assertEqual(result["replay"]["status"], "resolved")
        self.assertEqual(result["audit"]["selection_status"], "resolved")
        self.assertEqual(result["audit"]["pinned_facts_identity"]["candidate_resume_id"], "candidate-resume-1")
        self.assertEqual(len(repo.rows), 1)

    def test_repository_falls_back_to_generic_partial_candidate_facts(self):
        repo = CandidateFactsRepository()
        result = repo.build_persist_replay_audit(
            _FakeAnalyzer(),
            "resume-2",
            "2nd Engineer",
            [],
            candidate_resume_id="candidate-resume-2",
            resume_blob_id="blob-2",
            parser_version="generic_pdf.v1",
            facts_revision="rev-1",
            original_path="resume.pdf",
            text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
            folder_metadata={},
            source_origin="manual_upload",
            detected_layout="unknown",
        )
        self.assertEqual(result["candidate_facts"]["validation"]["status"], "degraded")
        self.assertEqual(result["candidate_facts"]["extraction"]["status"], "partial")
        self.assertEqual(result["audit"]["selection_status"], "resolved")
        self.assertEqual(result["audit"]["pinned_facts_identity"]["candidate_resume_id"], "candidate-resume-2")

    def test_repository_can_capture_review_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = CandidateFactsRepository(validation_cache_dir=tmpdir)
            capture = repo.capture_candidate_facts_for_review(
                _FakeAnalyzer(),
                "resume-3",
                "2nd Engineer",
                [],
                candidate_resume_id="candidate-resume-3",
                resume_blob_id="blob-3",
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                original_path="resume.pdf",
                text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
                folder_metadata={},
                source_origin="manual_upload",
                detected_layout="unknown",
            )
            self.assertEqual(capture["review_item"]["review_status"], "pending_review")
            self.assertEqual(len(repo.list_candidate_facts_review_items()), 1)
            approved = repo.approve_candidate_facts_review_item(capture["review_item"]["id"], reviewed_by="reviewer")
            self.assertEqual(approved["review_status"], "approved")
            promoted = repo.promote_candidate_facts_review_item(capture["review_item"]["id"])
            self.assertTrue(promoted["persist"]["committed"])
            self.assertEqual(promoted["review_item"]["persistence_status"], "persisted")
            self.assertEqual(len(repo.rows), 1)

    def test_repository_can_auto_capture_review_during_replay_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = CandidateFactsRepository(validation_cache_dir=tmpdir)
            replay = repo.build_persist_replay_audit(
                _FakeAnalyzer(),
                "resume-4",
                "2nd Engineer",
                [],
                candidate_resume_id="candidate-resume-4",
                resume_blob_id="blob-4",
                parser_version="legacy_bridge.v1",
                facts_revision="rev-1",
                original_path="resume.pdf",
                text_cache={"resume.pdf": "Jane Doe 2nd engineer resume"},
                folder_metadata={},
                source_origin="seajobs_download",
                detected_layout="seajobs",
                review_capture_callback=lambda candidate_facts, context: repo.capture_normalized_candidate_facts_for_review(
                    candidate_resume_id=str(context.get("candidate_resume_id") or ""),
                    resume_blob_id=str(context.get("resume_blob_id") or ""),
                    candidate_facts=candidate_facts,
                    parser_version=str(context.get("parser_version") or ""),
                    facts_revision=str(context.get("facts_revision") or ""),
                ),
            )
            self.assertEqual(replay["review_capture"]["review_item"]["review_status"], "pending_review")
            self.assertEqual(len(repo.list_candidate_facts_review_items()), 1)
            self.assertFalse(replay["review_capture_error"])


if __name__ == "__main__":
    unittest.main()
