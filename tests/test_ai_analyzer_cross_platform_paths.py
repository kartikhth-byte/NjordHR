import sys
import tempfile
import types
import unittest
from pathlib import Path

from repositories.resume_identity import (
    EMPTY_INPUT_PROVENANCE,
    LEGACY_PATH_FALLBACK_PROVENANCE,
    VERIFIED_CONTENT_HASH_PROVENANCE,
    build_resume_fingerprint,
    stable_resume_id,
    stable_resume_identity,
)


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


class _FakeConfig:
    def __init__(self, download_root):
        self.download_root = str(download_root)


class AIAnalyzerCrossPlatformPathTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.download_root = Path(self.temp_dir.name)
        self.rank_folder = self.download_root / "2nd_Engineer"
        self.rank_folder.mkdir(parents=True, exist_ok=True)
        self.analyzer.config = _FakeConfig(self.download_root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cross_platform_basename_handles_windows_paths(self):
        path = r"C:\Users\ar_ka\Downloads\NjordHR\Downloaded_Resumes\2nd_Engineer\foo.pdf"
        self.assertEqual(AIResumeAnalyzer._cross_platform_basename(path), "foo.pdf")

    def test_resolve_candidate_path_prefers_current_rank_folder_copy(self):
        resume_path = self.rank_folder / "foo.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n")
        metadata = {
            "filename": "foo.pdf",
            "source_path": r"C:\Users\ar_ka\Downloads\NjordHR\Downloaded_Resumes\2nd_Engineer\foo.pdf",
        }

        resolved = self.analyzer._resolve_candidate_path(metadata, self.rank_folder)
        self.assertEqual(resolved, resume_path)

    def test_resolve_candidate_path_skips_unmatched_stale_paths(self):
        metadata = {
            "filename": "missing.pdf",
            "source_path": r"C:\Users\ar_ka\Downloads\NjordHR\Downloaded_Resumes\2nd_Engineer\missing.pdf",
        }

        resolved = self.analyzer._resolve_candidate_path(metadata, self.rank_folder)
        self.assertIsNone(resolved)

    def test_resume_fingerprint_tracks_content_or_path(self):
        resume_path = self.rank_folder / "resume.pdf"
        resume_path.write_bytes(b"%PDF-1.4\nsame-content")

        fingerprint = build_resume_fingerprint(source_path=resume_path, raw_text="chunk text")
        self.assertTrue(fingerprint)

    def test_stable_resume_identity_marks_readable_files_as_authoritative_content(self):
        resume_path = self.rank_folder / "identity.pdf"
        resume_path.write_bytes(b"%PDF-1.4\nstable-content")

        identity = stable_resume_identity(resume_path)

        self.assertEqual(identity.alias_provenance, VERIFIED_CONTENT_HASH_PROVENANCE)
        self.assertEqual(identity.content_hash, identity.resume_id)
        self.assertTrue(identity.is_authoritative_content_alias)
        self.assertEqual(stable_resume_id(resume_path), identity.resume_id)

    def test_stable_resume_identity_marks_missing_files_as_legacy_path_fallback(self):
        missing_path = self.rank_folder / "missing.pdf"

        identity = stable_resume_identity(missing_path)

        self.assertEqual(identity.alias_provenance, LEGACY_PATH_FALLBACK_PROVENANCE)
        self.assertEqual(identity.content_hash, "")
        self.assertFalse(identity.is_authoritative_content_alias)
        self.assertEqual(stable_resume_id(missing_path), identity.resume_id)

    def test_stable_resume_identity_marks_empty_input_as_non_authoritative(self):
        identity = stable_resume_identity("")

        self.assertEqual(identity.alias_provenance, EMPTY_INPUT_PROVENANCE)
        self.assertEqual(identity.content_hash, "")
        self.assertFalse(identity.is_authoritative_content_alias)

    def test_candidate_dedupe_signature_collapses_same_content_across_paths(self):
        chunks_a = [{
            "metadata": {
                "raw_text": "Candidate has valid passport and 10 years experience.",
                "resume_id": "windows-id",
            }
        }]
        chunks_b = [{
            "metadata": {
                "raw_text": "Candidate has valid passport and 10 years experience.",
                "resume_id": "mac-id",
            }
        }]

        sig_a = self.analyzer._candidate_dedupe_signature("windows-id", chunks_a)
        sig_b = self.analyzer._candidate_dedupe_signature("mac-id", chunks_b)

        self.assertEqual(sig_a, sig_b)

    def test_candidate_display_filename_prefers_current_rank_folder_copy(self):
        resume_path = self.rank_folder / "current_name.pdf"
        resume_path.write_bytes(b"%PDF-1.4\nidentical-content")
        resume_id = build_resume_fingerprint(source_path=resume_path)
        chunks = [{
            "metadata": {
                "resume_id": resume_id,
                "raw_text": "Candidate has valid passport and 10 years experience.",
            }
        }]
        current_rank_index = self.analyzer._current_rank_file_index(self.rank_folder)

        filename = self.analyzer._candidate_display_filename(
            resume_id,
            chunks,
            current_rank_index=current_rank_index,
        )

        self.assertEqual(filename, "current_name.pdf")

    def test_normalize_rank_candidates_drops_unresolved_stale_entries(self):
        current_path = self.rank_folder / "current.pdf"
        current_path.write_bytes(b"%PDF-1.4\ncurrent")

        current_candidates = {
            "current-id": [{
                "metadata": {
                    "resume_id": "current-id",
                    "filename": "current.pdf",
                    "source_path": str(current_path),
                    "raw_text": "Current candidate text.",
                }
            }],
            "stale-id": [{
                "metadata": {
                    "resume_id": "stale-id",
                    "filename": "stale.pdf",
                    "source_path": r"C:\\Users\\ar_ka\\Downloads\\NjordHR\\Downloaded_Resumes\\2nd_Engineer\\stale.pdf",
                    "raw_text": "Stale candidate text.",
                }
            }],
        }

        current_rank_index = self.analyzer._current_rank_file_index(self.rank_folder)
        resolved_candidates, candidate_paths, stale_candidates, duplicate_candidates = self.analyzer._normalize_rank_candidates(
            current_candidates,
            self.rank_folder,
            current_rank_index=current_rank_index,
        )

        self.assertEqual(list(resolved_candidates.keys()), ["current-id"])
        self.assertEqual(candidate_paths["current-id"], current_path)
        self.assertEqual(stale_candidates, 1)
        self.assertEqual(duplicate_candidates, 0)
        self.assertNotIn("stale-id", resolved_candidates)

    def test_scoped_enumeration_extracts_only_requested_candidates_and_reports_missing(self):
        selected_path = self.rank_folder / "selected.pdf"
        outside_path = self.rank_folder / "outside.pdf"
        selected_path.write_bytes(b"%PDF-1.4\nselected")
        outside_path.write_bytes(b"%PDF-1.4\noutside")
        extracted_paths = []

        class _Registry:
            def get_resume_identity_record(self, file_path):
                path = Path(file_path)
                if path == selected_path:
                    return {
                        "resume_id": "resume-selected",
                        "candidate_scope_id": "scope-selected",
                        "content_hash": "hash-selected",
                    }
                if path == outside_path:
                    return {
                        "resume_id": "resume-outside",
                        "candidate_scope_id": "scope-outside",
                        "content_hash": "hash-outside",
                    }
                return {}

            def generate_resume_id(self, file_path):
                return Path(file_path).stem

        class _PdfProcessor:
            def extract_text(self, file_path):
                extracted_paths.append(Path(file_path))
                return f"Resume text for {Path(file_path).stem}"

        self.analyzer.registry = _Registry()
        self.analyzer.pdf_processor = _PdfProcessor()

        candidates, summary = self.analyzer._enumerate_scoped_rank_candidates(
            self.rank_folder,
            "2nd Engineer",
            ["scope-selected", "scope-missing"],
        )

        self.assertEqual(list(candidates.keys()), ["resume-selected"])
        self.assertEqual(extracted_paths, [selected_path])
        self.assertEqual(summary["requested_count"], 2)
        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["unresolvable_count"], 1)

    def test_scoped_enumeration_marks_changed_content_without_losing_identity(self):
        selected_path = self.rank_folder / "selected.pdf"
        selected_path.write_bytes(b"%PDF-1.4\nupdated")

        class _Registry:
            def get_resume_identity_record(self, _file_path):
                return {
                    "resume_id": "resume-selected",
                    "candidate_scope_id": "scope-selected",
                    "content_hash": "new-hash",
                }

            def generate_resume_id(self, _file_path):
                return "resume-selected"

        class _PdfProcessor:
            def extract_text(self, _file_path):
                return "Updated resume text"

        self.analyzer.registry = _Registry()
        self.analyzer.pdf_processor = _PdfProcessor()

        candidates, summary = self.analyzer._enumerate_scoped_rank_candidates(
            self.rank_folder,
            "2nd Engineer",
            ["scope-selected"],
            candidate_scope_memberships=[{
                "candidate_scope_id": "scope-selected",
                "content_hash_at_event": "old-hash",
            }],
        )

        metadata = candidates["resume-selected"][0]["metadata"]
        self.assertEqual(summary["changed_content_count"], 1)
        self.assertIn("EARLIER_CONDITIONS_NOT_RECERTIFIED", metadata["lineage_warning_codes"])

    def test_legacy_scope_identity_record_gets_current_content_hash_for_ack_detection(self):
        selected_path = self.rank_folder / "legacy.pdf"
        selected_path.write_bytes(b"%PDF-1.4\nlegacy-current-content")

        class _Registry:
            def get_resume_identity_record(self, _file_path):
                return {
                    "resume_id": "legacy-resume-id",
                    "candidate_scope_id": "scope-legacy",
                    "content_hash": "",
                }

        self.analyzer.registry = _Registry()

        record = self.analyzer._candidate_scope_identity_record(selected_path)

        self.assertEqual(record["candidate_scope_id"], "scope-legacy")
        self.assertTrue(record["content_hash"])

    def test_scope_snapshot_requires_ack_when_parent_hash_is_missing(self):
        selected_path = self.rank_folder / "selected.pdf"
        selected_path.write_bytes(b"%PDF-1.4\nlegacy-upgraded-content")

        class _Registry:
            def get_resume_identity_record(self, _file_path):
                return {
                    "resume_id": "legacy-resume-id",
                    "candidate_scope_id": "scope-selected",
                    "content_hash": "",
                }

        self.analyzer.registry = _Registry()

        summary = self.analyzer.resolve_candidate_scope_snapshot(
            self.rank_folder,
            ["scope-selected"],
            candidate_scope_memberships=[{
                "candidate_scope_id": "scope-selected",
                "content_hash_at_event": "",
            }],
        )

        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["changed_content_count"], 1)
        self.assertEqual(summary["changed_members"][0]["parent_content_hash"], "")
        self.assertTrue(summary["changed_members"][0]["current_content_hash"])

    def test_scoped_enumeration_marks_uncertified_when_parent_hash_is_missing(self):
        selected_path = self.rank_folder / "selected.pdf"
        selected_path.write_bytes(b"%PDF-1.4\nlegacy-upgraded-content")

        class _Registry:
            def get_resume_identity_record(self, _file_path):
                return {
                    "resume_id": "legacy-resume-id",
                    "candidate_scope_id": "scope-selected",
                    "content_hash": "",
                }

            def generate_resume_id(self, _file_path):
                return "legacy-resume-id"

        class _PdfProcessor:
            def extract_text(self, _file_path):
                return "Updated resume text"

        self.analyzer.registry = _Registry()
        self.analyzer.pdf_processor = _PdfProcessor()

        candidates, summary = self.analyzer._enumerate_scoped_rank_candidates(
            self.rank_folder,
            "2nd Engineer",
            ["scope-selected"],
            candidate_scope_memberships=[{
                "candidate_scope_id": "scope-selected",
                "content_hash_at_event": "",
            }],
        )

        metadata = candidates["legacy-resume-id"][0]["metadata"]
        self.assertEqual(summary["changed_content_count"], 1)
        self.assertTrue(metadata["content_hash"])
        self.assertIn("EARLIER_CONDITIONS_NOT_RECERTIFIED", metadata["lineage_warning_codes"])
