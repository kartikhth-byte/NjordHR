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

            def get_session_health(self):
                return {"active": False, "valid": False, "reason": "No active session"}

        scraper_module.Scraper = DummyScraper
        sys.modules["scraper_engine"] = scraper_module


_stub_external_modules()
from agent.service import create_agent_app  # noqa: E402


class AgentEmailIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.agent_cfg = os.path.join(self.temp_dir.name, "agent.json")
        self.prev_agent_cfg = os.environ.get("NJORDHR_AGENT_CONFIG_PATH")
        os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.agent_cfg
        app = create_agent_app()
        self.client = app.test_client()

    def tearDown(self):
        if self.prev_agent_cfg is None:
            os.environ.pop("NJORDHR_AGENT_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_AGENT_CONFIG_PATH"] = self.prev_agent_cfg
        self.temp_dir.cleanup()

    def test_health_reports_email_intake_summary(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("email_intake", body)
        self.assertEqual(body["email_intake"]["redirect_uri"], "http://localhost:53682/auth/outlook/callback")
        self.assertFalse(body["email_intake"]["configured"])
        self.assertIn("converter_available", body["email_intake"])
        self.assertIn("converter_message", body["email_intake"])

    def test_email_intake_auth_status_reflects_settings(self):
        save = self.client.put("/settings", json={
            "outlook_client_id": "client-123",
            "email_intake_mailbox": "recruitment@njordships.com",
            "email_intake_enabled": True,
            "email_intake_poll_interval_seconds": 75,
        })
        self.assertEqual(save.status_code, 200)

        resp = self.client.get("/email-intake/auth/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        auth = body["auth"]
        self.assertTrue(auth["configured"])
        self.assertEqual(auth["mailbox"], "recruitment@njordships.com")
        self.assertTrue(auth["enabled"])
        self.assertEqual(auth["poll_interval_seconds"], 75)

    def test_rejects_invalid_email_intake_poll_interval(self):
        resp = self.client.put("/settings", json={"email_intake_poll_interval_seconds": "abc"})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("email_intake_poll_interval_seconds", body["message"])

    def test_email_intake_fetch_requires_connected_matching_mailbox(self):
        self.client.put("/settings", json={"email_intake_mailbox": "recruitment@njordships.com"})
        resp = self.client.post("/email-intake/fetch", json={})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("not connected", body["message"].lower())

    def test_manual_review_summary_and_items_endpoints_reflect_filesystem_queue(self):
        download_folder = os.path.join(self.temp_dir.name, "downloads")
        self.client.put("/settings", json={"download_folder": download_folder})
        manual_dir = Path(download_folder) / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)

        newest_pdf = manual_dir / "EMAIL_20260427_093105_Neeraj_CV.pdf"
        newest_pdf.write_bytes(b"%PDF-1.4 newest")
        Path(f"{newest_pdf}.json").write_text(
            '{"mail_sender":"test@example.com","mail_subject":"Regarding Placemnet","received_at":"2026-04-27T09:31:05Z","rank_reason":"Weak role evidence"}',
            encoding="utf-8",
        )

        older_pdf = manual_dir / "EMAIL_20260401_080822_Copy-SANTANU.pdf"
        older_pdf.write_bytes(b"%PDF-1.4 older")
        Path(f"{older_pdf}.json").write_text(
            '{"mail_sender":"older@example.com","mail_subject":"Fwd: Apply for messman","received_at":"2026-04-01T08:08:22Z","rank_reason":"Ambiguous role"}',
            encoding="utf-8",
        )

        summary_resp = self.client.get("/email-intake/manual-review/summary")
        self.assertEqual(summary_resp.status_code, 200)
        summary = summary_resp.get_json()["summary"]
        self.assertEqual(summary["pending_count"], 2)
        self.assertEqual(summary["latest_item_name"], newest_pdf.name)

        list_resp = self.client.get("/email-intake/manual-review/items")
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.get_json()["items"]
        self.assertEqual(items[0]["pdf_filename"], newest_pdf.name)
        self.assertEqual(items[1]["pdf_filename"], older_pdf.name)

        detail_resp = self.client.get(f"/email-intake/manual-review/item?id={newest_pdf.name}")
        self.assertEqual(detail_resp.status_code, 200)
        item = detail_resp.get_json()["item"]
        self.assertEqual(item["mail_subject"], "Regarding Placemnet")
        self.assertEqual(item["review_reason"], "Weak role evidence")

    def test_manual_review_item_endpoint_requires_id(self):
        resp = self.client.get("/email-intake/manual-review/item")
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("id is required", body["message"])

    def test_manual_review_move_endpoint_moves_item_and_updates_summary(self):
        download_folder = os.path.join(self.temp_dir.name, "downloads")
        self.client.put("/settings", json={"download_folder": download_folder})
        manual_dir = Path(download_folder) / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 item")
        Path(f"{pdf_path}.json").write_text(
            '{"mail_subject":"Document from Tauhid","attachment_name":"Md Tauhid-1.pdf"}',
            encoding="utf-8",
        )

        resp = self.client.post("/email-intake/manual-review/move", json={
            "id": pdf_path.name,
            "selected_role": "Chief Officer",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["selected_role"], "Chief Officer")
        self.assertEqual(body["pending_count"], 0)
        self.assertTrue(Path(body["destination_pdf_path"]).exists())
        self.assertTrue(Path(body["destination_sidecar_path"]).exists())

    def test_manual_review_move_endpoint_rejects_invalid_role(self):
        download_folder = os.path.join(self.temp_dir.name, "downloads")
        self.client.put("/settings", json={"download_folder": download_folder})
        manual_dir = Path(download_folder) / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 item")

        resp = self.client.post("/email-intake/manual-review/move", json={
            "id": pdf_path.name,
            "selected_role": "Not A Configured Role",
        })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("allowlist", body["message"])

    def test_manual_review_open_endpoint_opens_pdf(self):
        download_folder = os.path.join(self.temp_dir.name, "downloads")
        self.client.put("/settings", json={"download_folder": download_folder})
        manual_dir = Path(download_folder) / "_EmailInbox_ManualReview"
        manual_dir.mkdir(parents=True, exist_ok=True)

        pdf_path = manual_dir / "EMAIL_20260427_050735_item.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 item")

        with unittest.mock.patch(
            "agent.email_intake.OutlookEmailIntakeManager._open_path_in_system_viewer"
        ) as open_mock:
            resp = self.client.post("/email-intake/manual-review/open", json={"id": pdf_path.name})

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["success"])
        open_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
