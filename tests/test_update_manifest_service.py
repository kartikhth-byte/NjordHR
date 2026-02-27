import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _stub_external_modules():
    if "scraper_engine" not in sys.modules:
        scraper_module = types.ModuleType("scraper_engine")

        class DummyScraper:
            def __init__(self, *_args, **_kwargs):
                self.driver = None

            def quit(self):
                return None

        scraper_module.Scraper = DummyScraper
        sys.modules["scraper_engine"] = scraper_module

    if "ai_analyzer" not in sys.modules:
        analyzer_module = types.ModuleType("ai_analyzer")

        class DummyAnalyzer:
            def __init__(self, *_args, **_kwargs):
                pass

        analyzer_module.Analyzer = DummyAnalyzer
        sys.modules["ai_analyzer"] = analyzer_module


_stub_external_modules()
import backend_server  # noqa: E402


class UpdateManifestServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.release_root = Path(self.temp_dir.name) / "release"
        self.version = "2026.02.27.1107"
        self.version_dir = self.release_root / self.version
        self.version_dir.mkdir(parents=True, exist_ok=True)

        self.macos_pkg = "NjordHR-2026.02.27.1107-unsigned.pkg"
        self.win_exe = "NjordHR-2026.02.27.1107-setup.exe"
        (self.version_dir / self.macos_pkg).write_bytes(b"pkg-bytes")
        (self.version_dir / self.win_exe).write_bytes(b"exe-bytes")

        manifest = {
            "version": self.version,
            "created_at_utc": "2026-02-27T00:00:00Z",
            "artifact_count": 2,
            "artifacts": [
                {"name": self.macos_pkg, "size_bytes": 9, "sha256": "abc123", "signature": ""},
                {"name": self.win_exe, "size_bytes": 9, "sha256": "def456", "signature": ""},
            ],
        }
        (self.version_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        self.prev_release_dir = os.environ.get("NJORDHR_RELEASE_DIR")
        self.prev_update_base = os.environ.get("NJORDHR_UPDATE_BASE_URL")
        os.environ["NJORDHR_RELEASE_DIR"] = str(self.release_root)
        os.environ["NJORDHR_UPDATE_BASE_URL"] = "https://updates.example.com/releases"
        self.client = backend_server.app.test_client()

    def tearDown(self):
        if self.prev_release_dir is None:
            os.environ.pop("NJORDHR_RELEASE_DIR", None)
        else:
            os.environ["NJORDHR_RELEASE_DIR"] = self.prev_release_dir
        if self.prev_update_base is None:
            os.environ.pop("NJORDHR_UPDATE_BASE_URL", None)
        else:
            os.environ["NJORDHR_UPDATE_BASE_URL"] = self.prev_update_base
        self.temp_dir.cleanup()

    def test_updates_manifest_defaults_to_latest(self):
        resp = self.client.get("/updates/manifest")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["version"], self.version)
        self.assertEqual(body["artifact_count"], 2)
        urls = [a["url"] for a in body["artifacts"]]
        self.assertTrue(any(self.macos_pkg in u for u in urls))
        self.assertTrue(any(self.win_exe in u for u in urls))

    def test_updates_manifest_platform_filter(self):
        resp = self.client.get("/updates/manifest?platform=macos")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["artifact_count"], 1)
        self.assertEqual(body["artifacts"][0]["platform"], "macos")
        self.assertEqual(body["artifacts"][0]["name"], self.macos_pkg)

    def test_release_artifact_download(self):
        resp = self.client.get(f"/releases/{self.version}/{self.macos_pkg}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"pkg-bytes")


if __name__ == "__main__":
    unittest.main()
