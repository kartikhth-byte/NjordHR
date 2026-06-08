import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from agent.runtime import AgentRuntime
from rank_folders import rank_folder_slug


class _FakeScraper:
    def __init__(self, download_folder):
        self.download_folder = download_folder
        self.download_calls = []

    def get_session_health(self):
        return {"active": True, "valid": True, "reason": ""}

    def download_resumes(self, rank, ship_type, force_redownload, logger):
        self.download_calls.append((rank, ship_type, force_redownload))
        filename = "resume_123.pdf"
        rank_folder = rank_folder_slug(rank)
        folder = os.path.join(self.download_folder, rank_folder)
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, filename)
        with open(file_path, "wb") as fh:
            fh.write(b"%PDF-1.4 test")
        logger.info("Saved: %s", filename)
        return {"success": True, "message": "download complete", "log": [f"Saved: {filename}"]}


class _RaisingScraper(_FakeScraper):
    def download_resumes(self, rank, ship_type, force_redownload, logger):
        self.download_calls.append((rank, ship_type, force_redownload))
        raise RuntimeError("download pipeline failed")


class _FakeEmailIntake:
    def fetch_from_outlook(self, emit):
        emit("log", "Inbox checked")
        return {"success": True, "message": "email intake complete"}


class _FakeSyncClient:
    def __init__(self):
        self.job_states = []
        self.job_logs = []
        self.candidate_events = []
        self.resume_uploads = []

    def push_job_state(self, payload):
        self.job_states.append(payload)

    def push_job_log(self, payload):
        self.job_logs.append(payload)

    def push_candidate_event(self, payload):
        self.candidate_events.append(payload)

    def upload_resume(self, file_path, metadata):
        self.resume_uploads.append((file_path, metadata))
        return {
            "resume_source": "cloud_sync_pending",
            "resume_upload_status": "pending",
            "resume_storage_path": "",
            "resume_checksum_sha256": "abc123",
        }


class _FlakySyncClient(_FakeSyncClient):
    def push_job_state(self, payload):
        raise OSError("state queue unavailable")

    def push_job_log(self, payload):
        raise OSError("log queue unavailable")

    def push_candidate_event(self, payload):
        raise OSError("event queue unavailable")

    def upload_resume(self, file_path, metadata):
        raise OSError("upload queue unavailable")


class _FakeSettingsStore:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self._cfg = {
            "device_id": "device-123",
            "download_folder": os.path.join(base_dir, "Resumes"),
            "cloud_sync_enabled": True,
            "cloud_upload_resumes": True,
        }

    def get(self):
        return dict(self._cfg)


class AgentRuntimeTests(unittest.TestCase):
    def test_download_job_routes_through_shared_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_store = _FakeSettingsStore(tmp)
            scraper = _FakeScraper(settings_store.get()["download_folder"])
            sync_client = _FakeSyncClient()
            runtime = AgentRuntime(
                settings_store=settings_store,
                session_getter=lambda: scraper,
                session_health_getter=scraper.get_session_health,
                email_intake=_FakeEmailIntake(),
                sync_client=sync_client,
            )
            try:
                job_id = runtime.submit_download_job("Chief Officer", "Bulk Carrier", True)
                deadline = time.time() + 5
                while time.time() < deadline:
                    job = runtime.get_job(job_id)
                    if job and job.get("status") in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                job = runtime.get_job(job_id)
                self.assertEqual(job.get("status"), "success")
                self.assertTrue(job.get("result", {}).get("success"))
                self.assertEqual(job.get("progress", {}).get("stage"), "complete")
                self.assertEqual(scraper.download_calls, [("Chief Officer", "Bulk Carrier", True)])
                self.assertTrue(sync_client.job_states)
                self.assertEqual(sync_client.job_states[0]["status"], "running")
                self.assertEqual(sync_client.job_states[-1]["status"], "success")
                self.assertTrue(sync_client.job_logs)
                events = runtime.wait_for_events(job_id, 0)
                self.assertTrue(any(event.get("type") == "progress" for event in events))
                self.assertTrue(any((event.get("data") or {}).get("stage") == "preflight" for event in events))
                self.assertEqual(len(sync_client.resume_uploads), 1)
                upload_path, upload_meta = sync_client.resume_uploads[0]
                self.assertTrue(upload_path.endswith(os.path.join("Chief_Officer", "resume_123.pdf")))
                self.assertEqual(upload_meta["job_id"], job_id)
                self.assertTrue(sync_client.candidate_events)
            finally:
                runtime.shutdown()

    def test_email_intake_job_uses_same_runtime_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_store = _FakeSettingsStore(tmp)
            scraper = _FakeScraper(settings_store.get()["download_folder"])
            sync_client = _FakeSyncClient()
            runtime = AgentRuntime(
                settings_store=settings_store,
                session_getter=lambda: scraper,
                session_health_getter=scraper.get_session_health,
                email_intake=_FakeEmailIntake(),
                sync_client=sync_client,
            )
            try:
                job_id = runtime.submit_email_intake_job()
                deadline = time.time() + 5
                while time.time() < deadline:
                    job = runtime.get_job(job_id)
                    if job and job.get("status") in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                job = runtime.get_job(job_id)
                self.assertEqual(job.get("status"), "success")
                self.assertEqual(job.get("result", {}).get("message"), "email intake complete")
                events = runtime.wait_for_events(job_id, 0)
                self.assertTrue(any(event.get("type") == "log" for event in events))
                self.assertTrue(any(event.get("type") == "progress" for event in events))
            finally:
                runtime.shutdown()

    def test_download_job_pushes_terminal_failed_state_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_store = _FakeSettingsStore(tmp)
            scraper = _RaisingScraper(settings_store.get()["download_folder"])
            sync_client = _FakeSyncClient()
            runtime = AgentRuntime(
                settings_store=settings_store,
                session_getter=lambda: scraper,
                session_health_getter=scraper.get_session_health,
                email_intake=_FakeEmailIntake(),
                sync_client=sync_client,
            )
            try:
                job_id = runtime.submit_download_job("Chief Officer", "Bulk Carrier", False)
                deadline = time.time() + 5
                while time.time() < deadline:
                    job = runtime.get_job(job_id)
                    if job and job.get("status") == "failed":
                        break
                    time.sleep(0.05)

                job = runtime.get_job(job_id)
                self.assertEqual(job.get("status"), "failed")
                self.assertIn("download pipeline failed", job.get("error", ""))
                self.assertEqual(sync_client.job_states[0]["status"], "running")
                self.assertEqual(sync_client.job_states[-1]["status"], "failed")
                self.assertEqual(sync_client.job_states[-1]["message"], "download pipeline failed")
            finally:
                runtime.shutdown()

    def test_download_job_ignores_cloud_sync_side_effect_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_store = _FakeSettingsStore(tmp)
            scraper = _FakeScraper(settings_store.get()["download_folder"])
            sync_client = _FlakySyncClient()
            runtime = AgentRuntime(
                settings_store=settings_store,
                session_getter=lambda: scraper,
                session_health_getter=scraper.get_session_health,
                email_intake=_FakeEmailIntake(),
                sync_client=sync_client,
            )
            try:
                job_id = runtime.submit_download_job("Chief Officer", "Bulk Carrier", False)
                deadline = time.time() + 5
                while time.time() < deadline:
                    job = runtime.get_job(job_id)
                    if job and job.get("status") == "success":
                        break
                    time.sleep(0.05)

                job = runtime.get_job(job_id)
                self.assertEqual(job.get("status"), "success")
                self.assertTrue(job.get("result", {}).get("success"))
                self.assertEqual(scraper.download_calls, [("Chief Officer", "Bulk Carrier", False)])
            finally:
                runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
