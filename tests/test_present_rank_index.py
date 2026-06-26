import tempfile
import time
import unittest
from pathlib import Path

from candidate_facts.present_rank_index import PresentRankIndex


def _facts(*, file_name, present_rank="", applied_rank="chief_officer"):
    return {
        "schema_version": "candidate_facts.v1",
        "source": {
            "resume_id": file_name,
            "candidate_id": file_name,
            "source_origin": "manual_upload",
            "detected_layout": "manual",
            "file_name": file_name,
            "content_hash": f"hash-{file_name}",
        },
        "role": {
            "current_rank_normalized": present_rank,
            "applied_rank_normalized": applied_rank,
        },
        "rank": {"value": applied_rank},
        "extraction": {
            "parser_version": "generic_pdf.v1",
            "status": "partial",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "raw_text_fallback",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": "test",
            },
            "warnings": [],
        },
    }


def _row(*, row_id, file_name, present_rank="", applied_rank="chief_officer", facts_hash=None, current=True, updated_at="2026-06-26T00:00:00+00:00"):
    facts = _facts(file_name=file_name, present_rank=present_rank, applied_rank=applied_rank)
    return {
        "id": row_id,
        "candidate_id": f"candidate-{row_id}",
        "candidate_resume_id": f"resume-{row_id}",
        "resume_blob_id": f"blob-{row_id}",
        "candidate_facts_hash": facts_hash or f"facts-{row_id}",
        "facts_json": facts,
        "is_current_for_resume": current,
        "updated_at": updated_at,
    }


class PresentRankIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        (self.base / "chief_officer").mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_pdf(self, folder, name):
        path = self.base / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n")
        return path

    def test_index_builds_from_current_candidate_facts_rows(self):
        self._write_pdf("chief_officer", "a.pdf")
        index = PresentRankIndex()

        snapshot = index.rebuild([
            _row(row_id="1", file_name="a.pdf", present_rank="chief_officer"),
            _row(row_id="2", file_name="old.pdf", present_rank="master", current=False),
        ], base_folder=self.base)

        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["row_count"], 1)
        self.assertEqual(snapshot["indexed_count"], 1)
        self.assertEqual(snapshot["rank_counts"], {"chief_officer": 1})
        entries = index.lookup("chief_officer")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].candidate_resume_id, "resume-1")
        self.assertEqual(entries[0].file_name, "a.pdf")
        self.assertEqual(entries[0].resume_path, "chief_officer/a.pdf")

    def test_index_tracks_unindexed_missing_present_rank_rows(self):
        index = PresentRankIndex()

        snapshot = index.rebuild([
            _row(row_id="1", file_name="a.pdf", present_rank=""),
        ], base_folder=self.base)

        self.assertEqual(snapshot["row_count"], 1)
        self.assertEqual(snapshot["indexed_count"], 0)
        self.assertEqual(snapshot["unindexed_count"], 1)
        self.assertEqual(index.lookup("chief_officer"), [])

    def test_first_refresh_builds_empty_index_snapshot(self):
        index = PresentRankIndex()

        snapshot = index.refresh([], base_folder=self.base)

        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["row_count"], 0)
        self.assertEqual(snapshot["rank_counts"], {})
        self.assertTrue(snapshot["built_at"])

    def test_refresh_does_not_increment_version_when_signatures_unchanged(self):
        self._write_pdf("chief_officer", "a.pdf")
        rows = [_row(row_id="1", file_name="a.pdf", present_rank="chief_officer")]
        index = PresentRankIndex()
        index.rebuild(rows, base_folder=self.base)

        snapshot = index.refresh(rows, base_folder=self.base)

        self.assertEqual(snapshot["version"], 1)

    def test_refresh_increments_version_when_resume_mtime_changes(self):
        path = self._write_pdf("chief_officer", "a.pdf")
        rows = [_row(row_id="1", file_name="a.pdf", present_rank="chief_officer")]
        index = PresentRankIndex()
        index.rebuild(rows, base_folder=self.base)
        time.sleep(0.01)
        path.write_bytes(b"%PDF-1.4\nchanged\n")

        snapshot = index.refresh(rows, base_folder=self.base)

        self.assertEqual(snapshot["version"], 2)

    def test_refresh_increments_version_when_facts_hash_changes(self):
        self._write_pdf("chief_officer", "a.pdf")
        index = PresentRankIndex()
        index.rebuild([
            _row(row_id="1", file_name="a.pdf", present_rank="chief_officer", facts_hash="old"),
        ], base_folder=self.base)

        snapshot = index.refresh([
            _row(row_id="1", file_name="a.pdf", present_rank="chief_officer", facts_hash="new"),
        ], base_folder=self.base)

        self.assertEqual(snapshot["version"], 2)


if __name__ == "__main__":
    unittest.main()
