import sys
import tempfile
import types
import unittest
from pathlib import Path

from repositories.resume_identity import build_resume_fingerprint


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
