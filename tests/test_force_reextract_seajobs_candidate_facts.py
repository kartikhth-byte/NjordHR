import tempfile
import unittest
from pathlib import Path

from candidate_facts.persistence import build_candidate_resume_facts_row
from candidate_facts.repository import CandidateFactsRepository
from scripts.force_reextract_seajobs_candidate_facts import (
    is_stale_generic_or_unknown_seajobs_row,
    scan_and_refresh,
)


SEAJOBS_TEXT = """
Candidate Profile
SEAMEN EXPERIENCE DETAILS
COMPANY NAME / SHIP TYPE
Vessel Name Rank Tonnage Sign In Sign Out
Example Tanker 2/E 49996 01-Jan-2024 01-Jul-2024
"""


def _candidate_facts(*, source_origin="manual_upload", detected_layout="unknown", parser_version="generic_pdf.v1", tonnage=None):
    return {
        "schema_version": "candidate_facts.v1",
        "source": {
            "resume_id": "resume-1",
            "candidate_id": "resume-1",
            "source_origin": source_origin,
            "detected_layout": detected_layout,
            "file_name": "resume-1.pdf",
            "content_hash": "hash-1",
        },
        "identity": {
            "candidate_name": {
                "value": "Jane Doe",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
            }
        },
        "experience": {
            "vessel_tonnage_values": list(tonnage or []),
        },
        "contracts": [],
        "evidence": [{"evidence_id": "ev-1", "source_kind": "raw_text_chunk", "source_id": "resume-1.pdf"}],
        "extraction": {
            "parser_version": parser_version,
            "status": "partial",
            "provenance": {
                "mode": "raw_text_fallback",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": None,
            },
            "warnings": [],
        },
    }


def _row(candidate_facts):
    return build_candidate_resume_facts_row(
        candidate_resume_id="resume-1",
        resume_blob_id="resume-1",
        candidate_facts=candidate_facts,
        parser_version=str((candidate_facts.get("extraction") or {}).get("parser_version") or ""),
        facts_revision="candidate_facts.v1",
        is_current_for_resume=True,
    )


class _FakeRegistry:
    def generate_resume_id(self, file_path):
        return Path(file_path).stem


class _FakePdfProcessor:
    def __init__(self, text_by_path):
        self.text_by_path = text_by_path

    def extract_text(self, path):
        return self.text_by_path[str(path)]


class _FakeAnalyzer:
    def __init__(self, text_by_path):
        self.registry = _FakeRegistry()
        self.pdf_processor = _FakePdfProcessor(text_by_path)
        self.reextract_calls = []

    def _rank_manifest_metadata(self, _folder):
        return {}

    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        self.reextract_calls.append((filename, rank, original_path))
        return {
            "candidate_id": filename,
            "identity": {"full_name": "Jane Doe"},
            "role": {"applied_rank_normalized": "2nd_engineer"},
            "personal": {"dob": None},
            "certifications": {"coc": {}, "endorsements": {}},
            "logistics": {},
            "experience": {
                "vessel_types": ["tanker"],
                "engine_types": [],
                "engine_details": [],
                "service_rows": [
                    {
                        "row_index": 1,
                        "rank_normalized": "2nd_engineer",
                        "vessel_type": "Oil Tanker",
                        "vessel_tonnage": [
                            {
                                "value": 49996,
                                "unit": "unspecified",
                                "source_label": "Tonnage",
                                "confidence": 0.70,
                                "evidence_text": "Oil Tanker 49996",
                            }
                        ],
                        "engine_details": [],
                    }
                ],
                "rank_duration_rows": [],
            },
            "application": {"applied_ship_types": []},
            "derived": {},
        }


class ForceReextractSeajobsCandidateFactsTests(unittest.TestCase):
    def test_stale_detector_selects_generic_row_for_seajobs_text(self):
        should_refresh, reason = is_stale_generic_or_unknown_seajobs_row(
            _row(_candidate_facts()),
            SEAJOBS_TEXT,
        )

        self.assertTrue(should_refresh)
        self.assertEqual(reason, "stale_generic_or_unknown_source")

    def test_stale_detector_skips_already_seajobs_row(self):
        should_refresh, reason = is_stale_generic_or_unknown_seajobs_row(
            _row(_candidate_facts(source_origin="seajobs_download", detected_layout="seajobs", parser_version="legacy_bridge.v1")),
            SEAJOBS_TEXT,
        )

        self.assertFalse(should_refresh)
        self.assertEqual(reason, "already_seajobs")

    def test_stale_detector_does_not_select_missing_row_by_default(self):
        should_refresh, reason = is_stale_generic_or_unknown_seajobs_row(None, SEAJOBS_TEXT)

        self.assertFalse(should_refresh)
        self.assertEqual(reason, "missing_current_row")

    def test_scan_dry_run_reports_without_persisting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rank_folder = root / "2nd_Engineer"
            rank_folder.mkdir()
            pdf_path = rank_folder / "resume-1.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            analyzer = _FakeAnalyzer({str(pdf_path): SEAJOBS_TEXT})
            repo = CandidateFactsRepository(rows=[_row(_candidate_facts())])

            report = scan_and_refresh(analyzer=analyzer, repo=repo, download_root=root, rank="2nd Engineer")

            self.assertEqual(report["mode"], "dry_run")
            self.assertEqual(report["selected_count"], 1)
            self.assertEqual(report["refreshed_count"], 0)
            self.assertEqual(len(analyzer.reextract_calls), 1)
            self.assertEqual(repo.rows[0]["facts_json"]["source"]["source_origin"], "manual_upload")
            self.assertEqual(report["rows"][0]["status"], "would_refresh")
            self.assertEqual(report["rows"][0]["tonnage"]["values"], [49996])

    def test_scan_apply_persists_refreshed_seajobs_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rank_folder = root / "2nd_Engineer"
            rank_folder.mkdir()
            pdf_path = rank_folder / "resume-1.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            analyzer = _FakeAnalyzer({str(pdf_path): SEAJOBS_TEXT})
            repo = CandidateFactsRepository(rows=[_row(_candidate_facts())])

            report = scan_and_refresh(analyzer=analyzer, repo=repo, download_root=root, rank="2nd Engineer", apply=True)

            self.assertEqual(report["mode"], "apply")
            self.assertEqual(report["selected_count"], 1)
            self.assertEqual(report["refreshed_count"], 1)
            current_rows = [row for row in repo.rows if row.get("is_current_for_resume")]
            self.assertEqual(len(current_rows), 1)
            self.assertEqual(current_rows[0]["facts_json"]["source"]["source_origin"], "seajobs_download")
            self.assertEqual(current_rows[0]["facts_json"]["experience"]["vessel_tonnage_values"], [49996])


if __name__ == "__main__":
    unittest.main()
