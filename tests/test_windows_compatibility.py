import tempfile
import types
import sys
import unittest
from pathlib import Path
from unittest import mock
from selenium.common.exceptions import WebDriverException


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
from scraper_engine import Scraper, _should_run_chrome_headless


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

    def test_seajobs_chrome_runs_headed_by_default_on_windows(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("scraper_engine.sys.platform", "win32"):
                self.assertFalse(_should_run_chrome_headless())

    def test_seajobs_chrome_stays_headless_by_default_off_windows(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("scraper_engine.sys.platform", "darwin"):
                self.assertTrue(_should_run_chrome_headless())

    def test_seajobs_chrome_headless_env_override_wins_on_windows(self):
        with mock.patch.dict("os.environ", {"NJORDHR_SELENIUM_HEADLESS": "true"}, clear=True):
            with mock.patch("scraper_engine.sys.platform", "win32"):
                self.assertTrue(_should_run_chrome_headless())

    def test_seajobs_input_helper_dispatches_browser_events(self):
        class FakeElement:
            def __init__(self):
                self.clicked = False
                self.keys = []

            def click(self):
                self.clicked = True

            def send_keys(self, *args):
                self.keys.append(args)

        driver = mock.Mock()
        element = FakeElement()
        scraper = Scraper(download_folder="")
        scraper.driver = driver

        scraper._set_input_value(element, "9999999999")

        self.assertTrue(element.clicked)
        self.assertEqual(element.keys[-1], ("9999999999",))
        driver.execute_script.assert_called_once()
        self.assertIn("dispatchEvent", driver.execute_script.call_args.args[0])

    def test_seajobs_send_otp_click_falls_back_to_javascript(self):
        button = mock.Mock()
        button.click.side_effect = WebDriverException("not clickable")
        driver = mock.Mock()
        scraper = Scraper(download_folder="")
        scraper.driver = driver

        mode = scraper._click_send_otp(button)

        self.assertEqual(mode, "javascript")
        self.assertEqual(driver.execute_script.call_count, 2)


if __name__ == "__main__":
    unittest.main()
