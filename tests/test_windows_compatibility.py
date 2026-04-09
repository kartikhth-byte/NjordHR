import tempfile
import types
import sys
import unittest
from pathlib import Path


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
from ai_analyzer import AIResumeAnalyzer
from csv_manager import CSVManager


class WindowsCompatibilityTests(unittest.TestCase):
    def test_iter_pdf_files_includes_uppercase_suffix(self):
        analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "alpha.PDF").write_bytes(b"%PDF-1.4")
            (root / "beta.pdf").write_bytes(b"%PDF-1.4")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")

            results = analyzer._iter_pdf_files(root)

            self.assertEqual([path.name for path in results], ["alpha.PDF", "beta.pdf"])

    def test_csv_manager_writes_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CSVManager(base_folder=tmpdir)
            manager.log_ai_search_audit(
                search_session_id="session-1",
                candidate_id="1001",
                filename="resume.pdf",
                facts_version="2.0",
                rank_applied_for="Chief Officer",
            )

            content = Path(manager.ai_search_audit_csv).read_bytes()

            self.assertIn(b"\n", content)
            self.assertNotIn(b"\r\n", content)


if __name__ == "__main__":
    unittest.main()
