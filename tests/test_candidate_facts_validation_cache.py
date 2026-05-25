import tempfile
import unittest
from pathlib import Path

from candidate_facts.validation_cache import (
    CandidateFactsValidationCache,
    build_candidate_facts_content_hash,
    build_candidate_facts_review_id,
    candidate_facts_validation_cache_base_dir,
)


def _candidate_facts_payload():
    return {
        "schema_version": "candidate_facts.v1",
        "source": {
            "resume_id": "candidate-resume-1",
            "candidate_id": "candidate-1",
            "source_origin": "manual_upload",
            "detected_layout": "unknown",
            "file_name": "resume.pdf",
            "content_hash": "abc123",
        },
        "identity": {
            "candidate_name": {
                "value": "Jane Doe",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
            },
        },
        "rank": {
            "value": "2nd_engineer",
            "presence": "observed_true",
            "confidence": "high",
            "evidence_ids": ["ev-1"],
        },
        "documents": [],
        "certificates": [],
        "endorsements": [],
        "courses": [],
        "contracts": [],
        "rank_experience": [],
        "engine_experience": [],
        "vessel_experience": [],
        "application": {"applied_ship_types": []},
        "derived": {},
        "evidence": [
            {
                "evidence_id": "ev-1",
                "source_kind": "raw_text_chunk",
                "source_id": "resume-1/chunk-1",
            }
        ],
        "extraction": {
            "parser_version": "generic_pdf.v1",
            "status": "partial",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "semantic_chunk",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": "generic_fallback",
            },
            "warnings": ["generic_candidate_facts_fallback"],
        },
    }


class CandidateFactsValidationCacheTests(unittest.TestCase):
    def test_cache_dir_is_os_portable(self):
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="darwin").endswith("Library/Application Support/NjordHR/candidate_facts"))
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="linux").endswith(".config/njordhr/candidate_facts"))
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="windows").endswith("NjordHR/candidate_facts"))

    def test_capture_approve_and_promote_review_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            self.assertEqual(record["review_status"], "pending_review")
            self.assertEqual(len(cache.list_review_items(review_status="pending_review")), 1)

            approved = cache.approve_review_item(record["id"], reviewed_by="reviewer", review_notes="looks good")
            self.assertEqual(approved["review_status"], "approved")

            rows = []
            result = cache.promote_review_item_to_persisted(rows, record["id"])
            self.assertTrue(result["persist"]["committed"])
            self.assertEqual(result["review_item"]["persistence_status"], "persisted")
            self.assertTrue(Path(cache.path).exists())
            self.assertEqual(cache.get_review_item(record["id"])["persistence_row_id"], result["persist"]["row"]["id"])
            self.assertEqual(result["persist"]["row"]["candidate_facts_hash"], record["candidate_facts_hash"])

    def test_reject_review_item_keeps_item_out_of_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            rejected = cache.reject_review_item(record["id"], reviewed_by="reviewer", review_notes="bad evidence")
            self.assertEqual(rejected["review_status"], "rejected")
            with self.assertRaises(ValueError):
                cache.promote_review_item_to_persisted([], record["id"])

    def test_review_id_is_deterministic(self):
        candidate_facts_hash = build_candidate_facts_content_hash(_candidate_facts_payload())
        review_id = build_candidate_facts_review_id(
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            schema_version="candidate_facts.v1",
            parser_version="generic_pdf.v1",
            facts_revision="rev-1",
            candidate_facts_hash=candidate_facts_hash,
        )
        self.assertEqual(
            review_id,
            build_candidate_facts_review_id(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                schema_version="candidate_facts.v1",
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                candidate_facts_hash=candidate_facts_hash,
            ),
        )

    def test_recapture_with_changed_content_creates_new_review_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            first = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            second_payload = _candidate_facts_payload()
            second_payload["identity"]["candidate_name"]["value"] = "Jane Smith"
            second = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=second_payload,
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(cache.list_review_items()), 2)


if __name__ == "__main__":
    unittest.main()
