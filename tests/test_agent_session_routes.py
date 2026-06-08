import configparser
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _FakeScraper:
    last_instance = None

    def __init__(self, download_folder, otp_window_seconds, login_url, dashboard_url):
        self.download_folder = download_folder
        self.otp_window_seconds = otp_window_seconds
        self.login_url = login_url
        self.dashboard_url = dashboard_url
        self.started_with = None
        self.verified_otps = []
        self.quit_called = False
        _FakeScraper.last_instance = self

    def start_session(self, username, password, mobile_number):
        self.started_with = (username, password, mobile_number)
        return {"success": True, "message": "session started"}

    def verify_otp(self, otp):
        self.verified_otps.append(otp)
        return {"success": True, "message": "otp verified"}

    def quit(self):
        self.quit_called = True

    def get_session_health(self):
        return {"active": True, "valid": True, "reason": ""}


class _RaisingScraper(_FakeScraper):
    def start_session(self, username, password, mobile_number):
        self.started_with = (username, password, mobile_number)
        raise RuntimeError("login bootstrap failed")


class _FakeCloudSyncClient:
    def __init__(self, *_args, **_kwargs):
        self.started = False
        self.signal_reconnect_called = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def signal_reconnect(self):
        self.signal_reconnect_called = True

    def stats(self):
        return {"pending": 0, "queue_path": ""}

    def push_job_state(self, *_args, **_kwargs):
        return None

    def push_job_log(self, *_args, **_kwargs):
        return None

    def push_candidate_event(self, *_args, **_kwargs):
        return None

    def upload_resume(self, *_args, **_kwargs):
        return {
            "resume_source": "local_only",
            "resume_upload_status": "skipped",
            "resume_storage_path": "",
            "resume_checksum_sha256": "",
        }


class _FakeAgentRuntime:
    last_instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.submissions = []
        self.shutdown_called = False
        _FakeAgentRuntime.last_instance = self

    def submit_download_job(self, rank, ship_type, force_redownload=False):
        self.submissions.append({
            "job_type": "download",
            "rank": rank,
            "ship_type": ship_type,
            "force_redownload": bool(force_redownload),
        })
        return "job-123"

    def submit_email_intake_job(self):
        self.submissions.append({"job_type": "email_intake_fetch"})
        return "job-456"

    def get_job(self, job_id):
        return {"job_id": job_id, "status": "queued", "payload": {}}

    def wait_for_events(self, *_args, **_kwargs):
        return []

    def shutdown(self):
        self.shutdown_called = True
        return None


class AgentSessionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prev_agent_cfg = os.environ.get("NJORDHR_AGENT_CONFIG_PATH")
        self.prev_agent_token = os.environ.get("NJORDHR_AGENT_SYNC_TOKEN")
        self.prev_seajob_username = os.environ.get("SEAJOB_USERNAME")
        self.prev_seajob_password = os.environ.get("SEAJOB_PASSWORD")
        os.environ["NJORDHR_AGENT_CONFIG_PATH"] = os.path.join(self.temp_dir.name, "agent.json")
        os.environ["NJORDHR_AGENT_SYNC_TOKEN"] = "agent-preview-token"
        os.environ.pop("SEAJOB_USERNAME", None)
        os.environ.pop("SEAJOB_PASSWORD", None)

        parser = configparser.ConfigParser()
        parser["Advanced"] = {
            "otp_window_seconds": "120",
            "seajob_login_url": "http://seajob.net/seajob_login.php",
            "seajob_dashboard_url": "http://seajob.net/company/dashboard.php",
        }
        parser["Ranks"] = {"rank_options": "Chief Officer\n2nd Engineer"}
        parser["ShipTypes"] = {"ship_type_options": "Bulk Carrier\nOil Tanker"}

        patchers = [
            patch(
                "agent.service.load_app_settings",
                return_value=SimpleNamespace(
                    credentials={"Username": "demo-user", "Password": "demo-pass"},
                    config=parser,
                ),
            ),
            patch("agent.service.Scraper", _FakeScraper),
            patch("agent.service.AgentRuntime", _FakeAgentRuntime),
            patch("agent.service.CloudSyncClient", _FakeCloudSyncClient),
        ]
        self._patchers = patchers
        for patcher in patchers:
            patcher.start()

        from agent.service import create_agent_app

        self.app = create_agent_app()
        self.client = self.app.test_client()
        self.client.put("/settings", json={"download_folder": self.temp_dir.name})

    def tearDown(self):
        for patcher in reversed(getattr(self, "_patchers", [])):
            patcher.stop()
        if self.prev_agent_cfg is None:
            os.environ.pop("NJORDHR_AGENT_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.prev_agent_cfg
        if self.prev_agent_token is None:
            os.environ.pop("NJORDHR_AGENT_SYNC_TOKEN", None)
        else:
            os.environ["NJORDHR_AGENT_SYNC_TOKEN"] = self.prev_agent_token
        if self.prev_seajob_username is None:
            os.environ.pop("SEAJOB_USERNAME", None)
        else:
            os.environ["SEAJOB_USERNAME"] = self.prev_seajob_username
        if self.prev_seajob_password is None:
            os.environ.pop("SEAJOB_PASSWORD", None)
        else:
            os.environ["SEAJOB_PASSWORD"] = self.prev_seajob_password
        self.temp_dir.cleanup()

    def test_session_start_verify_disconnect_are_exposed_on_agent_api(self):
        start_resp = self.client.post("/session/start", json={"mobile_number": "9999999999"})
        self.assertEqual(start_resp.status_code, 200)
        start_body = start_resp.get_json()
        self.assertTrue(start_body["success"])

        verify_resp = self.client.post("/session/verify-otp", json={"otp": "123456"})
        self.assertEqual(verify_resp.status_code, 200)
        verify_body = verify_resp.get_json()
        self.assertTrue(verify_body["success"])
        self.assertEqual(verify_body["ranks"], ["Chief Officer", "2nd Engineer"])
        self.assertEqual(verify_body["ship_types"], ["Bulk Carrier", "Oil Tanker"])

        disconnect_resp = self.client.post("/session/disconnect")
        self.assertEqual(disconnect_resp.status_code, 200)
        disconnect_body = disconnect_resp.get_json()
        self.assertTrue(disconnect_body["success"])
        self.assertTrue(_FakeScraper.last_instance.quit_called)

    def test_session_start_uses_cloud_hydrated_seajobs_credentials(self):
        import agent.service as agent_service

        blank_parser = configparser.ConfigParser()
        blank_parser["Credentials"] = {"Username": "", "Password": ""}
        blank_parser["Settings"] = {}
        blank_parser["Advanced"] = {
            "otp_window_seconds": "120",
            "seajob_login_url": "http://seajob.net/seajob_login.php",
            "seajob_dashboard_url": "http://seajob.net/company/dashboard.php",
        }

        with patch.dict(os.environ, {"SEAJOB_USERNAME": "cloud-user", "SEAJOB_PASSWORD": "cloud-pass"}), \
                patch("agent.service.load_app_settings", return_value=SimpleNamespace(
                    credentials=blank_parser["Credentials"],
                    config=blank_parser,
                )), \
                patch("agent.service.Scraper", _FakeScraper), \
                patch("agent.service.AgentRuntime", _FakeAgentRuntime), \
                patch("agent.service.CloudSyncClient", _FakeCloudSyncClient):
            app = agent_service.create_agent_app()
            client = app.test_client()
            client.put("/settings", json={"download_folder": self.temp_dir.name})
            resp = client.post("/session/start", json={"mobile_number": "9999999999"})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(_FakeScraper.last_instance.started_with, ("cloud-user", "cloud-pass", "9999999999"))

    def test_session_start_rejects_missing_seajobs_credentials(self):
        blank_parser = configparser.ConfigParser()
        blank_parser["Credentials"] = {"Username": "", "Password": ""}
        blank_parser["Settings"] = {}
        blank_parser["Advanced"] = {
            "otp_window_seconds": "120",
            "seajob_login_url": "http://seajob.net/seajob_login.php",
            "seajob_dashboard_url": "http://seajob.net/company/dashboard.php",
        }

        with patch("agent.service.load_app_settings", return_value=SimpleNamespace(
                credentials=blank_parser["Credentials"],
                config=blank_parser,
        )), \
                patch("agent.service.Scraper", _FakeScraper), \
                patch("agent.service.AgentRuntime", _FakeAgentRuntime), \
                patch("agent.service.CloudSyncClient", _FakeCloudSyncClient):
            from agent.service import create_agent_app

            app = create_agent_app()
            client = app.test_client()
            resp = client.post("/session/start", json={"mobile_number": "9999999999"})

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["success"])
        self.assertIn("SeaJobs username/password are missing", resp.get_json()["message"])

    def test_agent_preview_downloaded_resume_serves_local_file(self):
        rank_dir = os.path.join(self.temp_dir.name, "Chief_Officer")
        os.makedirs(rank_dir, exist_ok=True)
        file_path = os.path.join(rank_dir, "Chief-Officer_Bulk-Carrier_1001.pdf")
        with open(file_path, "wb") as fh:
            fh.write(b"%PDF-1.4 preview content")

        resp = self.client.get("/preview_downloaded_resume/Chief_Officer/Chief-Officer_Bulk-Carrier_1001.pdf")
        self.assertEqual(resp.status_code, 403)

        resp = self.client.get(
            "/preview_downloaded_resume/Chief_Officer/Chief-Officer_Bulk-Carrier_1001.pdf",
            headers={"X-Device-Token": "agent-preview-token"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"preview content", resp.data)

    def test_agent_preview_downloaded_resume_rejects_symlink_escape(self):
        rank_dir = os.path.join(self.temp_dir.name, "Chief_Officer")
        os.makedirs(rank_dir, exist_ok=True)
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside_file = os.path.join(outside_tmp, "escape.pdf")
            with open(outside_file, "wb") as fh:
                fh.write(b"%PDF-1.4 escaped")
            link_path = os.path.join(rank_dir, "escape.pdf")
            try:
                os.symlink(outside_file, link_path)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is not available")
                raise

            resp = self.client.get(
                "/preview_downloaded_resume/Chief_Officer/escape.pdf",
                headers={"X-Device-Token": "agent-preview-token"},
            )
            self.assertEqual(resp.status_code, 403)

    def test_session_start_cleans_up_scraper_when_bootstrap_fails(self):
        import agent.service as agent_service

        with patch("agent.service.Scraper", _RaisingScraper):
            resp = self.client.post("/session/start", json={"mobile_number": "9999999999"})

        self.assertEqual(resp.status_code, 500)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("login bootstrap failed", body["message"])
        self.assertTrue(_RaisingScraper.last_instance.quit_called)
        health_resp = self.client.get("/session/health")
        self.assertEqual(health_resp.status_code, 200)
        self.assertFalse(health_resp.get_json()["health"]["active"])

    def test_download_job_route_queues_agent_work(self):
        resp = self.client.post(
            "/jobs/download",
            json={"rank": "Chief Officer", "ship_type": "Bulk Carrier", "force_redownload": True},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["job_id"], "job-123")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(_FakeAgentRuntime.last_instance.submissions, [{
            "job_type": "download",
            "rank": "Chief Officer",
            "ship_type": "Bulk Carrier",
            "force_redownload": True,
        }])


if __name__ == "__main__":
    unittest.main()
