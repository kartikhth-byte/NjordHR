import unittest

from candidate_facts.persistence import (
    build_candidate_resume_facts_row,
    build_candidate_resume_facts_row_id,
    persist_candidate_resume_facts,
    resolve_candidate_resume_facts_for_replay,
    select_candidate_resume_facts_row_by_identity,
)


def _candidate_facts_payload():
    return {
        "schema_version": "candidate_facts.v1",
        "source": {
            "resume_id": "candidate-resume-1",
            "candidate_id": "candidate-1",
            "source_origin": "seajobs_download",
            "detected_layout": "seajobs",
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
            "dob": {
                "value": "1988-02-03",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "seajobs",
                    "parser_version": "legacy_bridge.v1",
                    "method": "fallback",
                },
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
                "source_kind": "pdf_page",
                "source_id": "resume-1/page-1",
            }
        ],
        "extraction": {
            "parser_version": "legacy_bridge.v1",
            "status": "complete",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "persisted",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": None,
            },
            "warnings": [],
        },
    }


class CandidateFactsPersistenceTests(unittest.TestCase):
    def test_build_candidate_resume_facts_row_id_is_deterministic(self):
        row_id = build_candidate_resume_facts_row_id(
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        )
        self.assertEqual(row_id, build_candidate_resume_facts_row_id(
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        ))

    def test_build_candidate_resume_facts_row_contains_normalized_payload(self):
        row = build_candidate_resume_facts_row(
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            candidate_facts=_candidate_facts_payload(),
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        )
        self.assertEqual(row["schema_version"], "candidate_facts.v1")
        self.assertEqual(row["candidate_resume_id"], "candidate-resume-1")
        self.assertEqual(row["resume_blob_id"], "blob-1")
        self.assertEqual(row["extraction_status"], "complete")
        self.assertIn("validation", row["facts_json"])

    def test_persist_candidate_resume_facts_replaces_current_row_and_preserves_history(self):
        old_row = build_candidate_resume_facts_row(
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            candidate_facts=_candidate_facts_payload(),
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
            row_id="row-1",
            is_current_for_resume=True,
        )
        new_payload = _candidate_facts_payload()
        new_payload["rank"]["value"] = "chief_engineer"
        result = persist_candidate_resume_facts(
            [old_row],
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            candidate_facts=new_payload,
            parser_version="legacy_bridge.v1",
            facts_revision="rev-2",
        )
        rows = result["rows"]
        current_rows = [row for row in rows if row.get("is_current_for_resume")]
        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0]["facts_revision"], "rev-2")
        self.assertIsNotNone(select_candidate_resume_facts_row_by_identity(
            rows,
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        ))

    def test_persist_candidate_resume_facts_keeps_invalid_rows_non_current(self):
        payload = _candidate_facts_payload()
        del payload["source"]
        result = persist_candidate_resume_facts(
            [],
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            candidate_facts=payload,
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        )
        self.assertEqual(result["current_row"], None)
        self.assertFalse(result["committed"])
        self.assertEqual(result["row"]["facts_json"]["validation"]["status"], "invalid")

    def test_resolve_candidate_resume_facts_for_replay_prefers_exact_row_id(self):
        rows = [
            {
                "id": "row-old",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "parser_version": "legacy_bridge.v1",
                "facts_revision": "rev-1",
            },
            {
                "id": "row-new",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "parser_version": "legacy_bridge.v1",
                "facts_revision": "rev-2",
            },
        ]
        result = resolve_candidate_resume_facts_for_replay(
            rows,
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-2",
            candidate_resume_facts_id="row-old",
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["reason"], "id")
        self.assertEqual(result["row"]["id"], "row-old")

    def test_resolve_candidate_resume_facts_for_replay_falls_back_to_identity_tuple(self):
        rows = [
            {
                "id": "row-1",
                "candidate_resume_id": "candidate-resume-1",
                "schema_version": "candidate_facts.v1",
                "parser_version": "legacy_bridge.v1",
                "facts_revision": "rev-1",
            }
        ]
        result = resolve_candidate_resume_facts_for_replay(
            rows,
            candidate_resume_id="candidate-resume-1",
            schema_version="candidate_facts.v1",
            parser_version="legacy_bridge.v1",
            facts_revision="rev-1",
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["reason"], "identity")
        self.assertEqual(result["row"]["id"], "row-1")


if __name__ == "__main__":
    unittest.main()
