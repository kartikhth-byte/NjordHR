import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile


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


class AgentDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.agent_cfg = os.path.join(self.temp_dir.name, "agent.json")
        self.prev_agent_cfg = os.environ.get("NJORDHR_AGENT_CONFIG_PATH")
        os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.agent_cfg
        self.app = create_agent_app()
        self.client = self.app.test_client()

        self.base_dir = os.path.dirname(self.agent_cfg)
        os.makedirs(os.path.join(self.base_dir, "logs"), exist_ok=True)
        os.makedirs(os.path.join(self.base_dir, "state"), exist_ok=True)
        os.makedirs(os.path.join(self.base_dir, "updates"), exist_ok=True)
        with open(os.path.join(self.base_dir, "logs", "agent.log"), "w", encoding="utf-8") as fh:
            fh.write("diagnostic log line\n")
        with open(os.path.join(self.base_dir, "state", "queue.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(self.base_dir, "updates", "update_state.json"), "w", encoding="utf-8") as fh:
            json.dump({"last_check": {"target_version": "9.9.9"}}, fh)

    def tearDown(self):
        if self.prev_agent_cfg is None:
            os.environ.pop("NJORDHR_AGENT_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.prev_agent_cfg
        self.temp_dir.cleanup()

    def test_diagnostics_reports_runtime_snapshot(self):
        resp = self.client.get("/diagnostics")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "ok")
        self.assertIn("agent_version", body)
        self.assertIn("session_health", body)
        self.assertIn("sync", body)
        self.assertIn("email_intake", body)
        self.assertIn("last_resume_upload", body["sync"])
        self.assertEqual(body["base_dir"], self.base_dir)
        self.assertEqual(body["update_state"]["last_check"]["target_version"], "9.9.9")

    def test_log_bundle_exports_manifest_logs_state_and_updates(self):
        resp = self.client.get("/diagnostics/log-bundle")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/zip")

        with zipfile.ZipFile(io.BytesIO(resp.data), "r") as zf:
            names = set(zf.namelist())
            self.assertIn("agent/diagnostics.json", names)
            self.assertIn("agent/agent.json", names)
            self.assertIn("agent/logs/agent.log", names)
            self.assertIn("agent/state/queue.json", names)
            self.assertIn("agent/updates/update_state.json", names)

            manifest = json.loads(zf.read("agent/diagnostics.json").decode("utf-8"))
            self.assertEqual(manifest["base_dir"], self.base_dir)
            self.assertEqual(manifest["update_state"]["last_check"]["target_version"], "9.9.9")
            self.assertIn("sync", manifest)
            self.assertIn("last_resume_upload", manifest["sync"])


if __name__ == "__main__":
    unittest.main()
