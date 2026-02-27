import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _stub_external_modules():
    if "scraper_engine" not in sys.modules:
        scraper_module = types.ModuleType("scraper_engine")

        class DummyScraper:
            def __init__(self, *_args, **_kwargs):
                self.driver = None

            def quit(self):
                return None

            def get_session_health(self):
                return {"active": False, "valid": False, "reason": "No active session"}

        scraper_module.Scraper = DummyScraper
        sys.modules["scraper_engine"] = scraper_module


_stub_external_modules()
from agent.service import create_agent_app  # noqa: E402


class _FakeJSONResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FakeDownloadResponse:
    def __init__(self, body=b""):
        self._body = body
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024 * 1024):
        if self._body:
            yield self._body


class AgentUpdateEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = self.temp_dir.name
        self.agent_cfg = os.path.join(self.base, "agent.json")
        self.prev_agent_cfg = os.environ.get("NJORDHR_AGENT_CONFIG_PATH")
        self.prev_agent_ver = os.environ.get("NJORDHR_AGENT_VERSION")
        os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.agent_cfg
        os.environ["NJORDHR_AGENT_VERSION"] = "0.1.0"

        app = create_agent_app()
        self.client = app.test_client()

    def tearDown(self):
        if self.prev_agent_cfg is None:
            os.environ.pop("NJORDHR_AGENT_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.prev_agent_cfg
        if self.prev_agent_ver is None:
            os.environ.pop("NJORDHR_AGENT_VERSION", None)
        else:
            os.environ["NJORDHR_AGENT_VERSION"] = self.prev_agent_ver
        self.temp_dir.cleanup()

    @patch("agent.updater.requests.get")
    def test_updates_check_returns_update_available(self, mock_get):
        cfg = self.client.put("/settings", json={"api_base_url": "http://127.0.0.1:5050"})
        self.assertEqual(cfg.status_code, 200)
        manifest = {
            "success": True,
            "version": "2026.02.27.2220",
            "artifacts": [
                {
                    "name": "NjordHR-unsigned.pkg",
                    "platform": "macos",
                    "sha256": "abc123",
                    "url": "http://127.0.0.1:5050/releases/2026.02.27.2220/NjordHR-unsigned.pkg",
                }
            ],
        }
        mock_get.return_value = _FakeJSONResponse(manifest)
        resp = self.client.get("/updates/check")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["update_available"])
        self.assertEqual(body["target_version"], "2026.02.27.2220")
        self.assertIn("artifact", body)

    @patch("agent.updater.requests.get")
    def test_updates_download_and_verify_checksum(self, mock_get):
        content = b"sample-installer-bytes"
        sha = hashlib.sha256(content).hexdigest()
        mock_get.return_value = _FakeDownloadResponse(content)

        down = self.client.post("/updates/download", json={
            "artifact_url": "http://127.0.0.1:5050/releases/2026.02.27.2220/NjordHR-unsigned.pkg",
            "expected_sha256": sha,
        })
        self.assertEqual(down.status_code, 200)
        body = down.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["checksum_ok"])
        self.assertTrue(os.path.isfile(body["local_path"]))

        verify = self.client.post("/updates/verify", json={
            "local_path": body["local_path"],
            "expected_sha256": sha,
        })
        self.assertEqual(verify.status_code, 200)
        vbody = verify.get_json()
        self.assertTrue(vbody["success"])
        self.assertTrue(vbody["checksum_ok"])
        self.assertFalse(vbody["signature_verified"])


if __name__ == "__main__":
    unittest.main()
