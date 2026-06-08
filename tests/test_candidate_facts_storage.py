import unittest

from candidate_facts.storage import (
    build_candidate_resume_facts_identity,
    build_transient_facts_id,
    ensure_single_current_candidate_resume_facts_row,
    select_current_candidate_resume_facts_rows,
)


class CandidateFactsStorageTests(unittest.TestCase):
    def test_transient_facts_id_is_deterministic(self):
        params = dict(
            candidate_resume_id="candidate-resume-1",
            resume_blob_content_hash="hash-123",
            extractor_version="extractor.v1",
            raw_text_version="raw.v1",
            chunk_index_version="chunks.v1",
            fallback_mode="raw_text_fallback",
            query_session_id="session-1",
        )
        self.assertEqual(build_transient_facts_id(**params), build_transient_facts_id(**params))

    def test_candidate_resume_facts_identity_is_stable(self):
        identity = build_candidate_resume_facts_identity(
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="parser.v1",
            facts_revision="rev-2",
        )
        self.assertEqual(identity, "candidate-resume-1::candidate_facts.v1::parser.v1::rev-2")

    def test_select_current_candidate_resume_facts_rows_prefers_current_row(self):
        rows = [
            {
                "id": "facts-1",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "facts_revision": "rev-1",
                "is_current_for_resume": False,
                "created_at": "2026-05-01T10:00:00+00:00",
            },
            {
                "id": "facts-2",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "facts_revision": "rev-2",
                "is_current_for_resume": True,
                "updated_at": "2026-05-02T10:00:00+00:00",
            },
        ]
        ordered = select_current_candidate_resume_facts_rows(
            rows,
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
        )
        self.assertEqual(ordered[0]["id"], "facts-2")

    def test_ensure_single_current_candidate_resume_facts_row_marks_only_best_row_current(self):
        rows = [
            {
                "id": "facts-1",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "facts_revision": "rev-1",
                "is_current_for_resume": True,
            },
            {
                "id": "facts-2",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "facts_revision": "rev-2",
                "is_current_for_resume": False,
                "updated_at": "2026-05-03T10:00:00+00:00",
            },
        ]
        updated = ensure_single_current_candidate_resume_facts_row(
            rows,
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
        )
        current_rows = [row for row in updated if row.get("is_current_for_resume")]
        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0]["id"], "facts-2")


if __name__ == "__main__":
    unittest.main()
