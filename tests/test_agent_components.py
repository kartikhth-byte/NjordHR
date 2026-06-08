import os
import tempfile
import time
import unittest
from unittest import mock

from agent.config_store import AgentConfigStore, _default_download_folder
from agent.filesystem import ensure_writable_folder
from agent.job_queue import AgentJobQueue
from agent.secret_store import SecretStore
from rank_folders import rank_folder_path, rank_folder_slug


class AgentComponentsTests(unittest.TestCase):
    def test_config_store_defaults_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent.json")
            expected_download_folder = os.path.join(tmp, "Library", "Application Support", "NjordHR", "Resumes")
            with mock.patch("agent.config_store._default_download_folder", return_value=expected_download_folder):
                store = AgentConfigStore(path=path)
            cfg = store.get()
            self.assertTrue(cfg["device_id"])
            self.assertTrue(os.path.isabs(cfg["download_folder"]))
            self.assertEqual(cfg["download_folder"], expected_download_folder)
            self.assertEqual(cfg["api_base_url"], "")
            self.assertEqual(cfg["device_token"], "")
            self.assertTrue(cfg["cloud_sync_enabled"])
            self.assertFalse(cfg["cloud_upload_resumes"])
            self.assertFalse(cfg["auto_start"])
            self.assertEqual(cfg["email_intake_monitored_folder"], "Inbox/NjordHR Resumes")
            self.assertEqual(cfg["email_intake_processed_folder"], "Inbox/NjordHR Processed")
            self.assertEqual(cfg["email_intake_failed_folder"], "Inbox/NjordHR Failed")
            self.assertEqual(cfg["email_intake_poll_interval_seconds"], 60)

            updated = store.update({
                "api_base_url": "https://api.example.supabase.co/",
                "device_token": "  device-token-123  ",
                "cloud_sync_enabled": False,
                "cloud_upload_resumes": True,
                "auto_start": True,
                "download_folder": tmp,
                "email_intake_mailbox": "recruitment@njordships.com",
                "email_intake_poll_interval_seconds": "90",
                "outlook_client_id": "test-client-id",
                "update_manifest_url": " https://updates.example.com/manifest.json ",
                "log_level": " debug ",
            })
            self.assertEqual(updated["api_base_url"], "https://api.example.supabase.co")
            self.assertEqual(updated["device_token"], "device-token-123")
            self.assertFalse(updated["cloud_sync_enabled"])
            self.assertTrue(updated["cloud_upload_resumes"])
            self.assertTrue(updated["auto_start"])
            self.assertEqual(updated["download_folder"], tmp)
            self.assertEqual(updated["email_intake_mailbox"], "recruitment@njordships.com")
            self.assertEqual(updated["email_intake_poll_interval_seconds"], 90)
            self.assertEqual(updated["outlook_client_id"], "test-client-id")
            self.assertEqual(updated["update_manifest_url"], "https://updates.example.com/manifest.json")
            self.assertEqual(updated["log_level"], "debug")

    def test_default_download_folder_tracks_platform_base_dir(self):
        with mock.patch("agent.config_store.platform.system", return_value="darwin"):
            self.assertEqual(
                _default_download_folder(home="/Users/tester"),
                "/Users/tester/Library/Application Support/NjordHR/Resumes",
            )
        with mock.patch("agent.config_store.platform.system", return_value="windows"), mock.patch.dict(
            os.environ,
            {"APPDATA": r"C:\\Users\\tester\\AppData\\Roaming"},
            clear=False,
        ):
            expected_windows = os.path.join(r"C:\\Users\\tester\\AppData\\Roaming", "NjordHR", "Resumes")
            self.assertEqual(
                _default_download_folder(home="/Users/tester"),
                expected_windows,
            )
        with mock.patch("agent.config_store.platform.system", return_value="linux"):
            self.assertEqual(
                _default_download_folder(home="/home/tester"),
                "/home/tester/.config/njordhr/Resumes",
            )

    def test_config_store_migrates_legacy_download_folder_into_app_managed_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "app")
            os.makedirs(app_dir, exist_ok=True)
            path = os.path.join(app_dir, "agent.json")
            legacy_downloads = os.path.join(tmp, "Downloads", "NjordHR")
            os.makedirs(legacy_downloads, exist_ok=True)
            legacy_file = os.path.join(legacy_downloads, "resume.pdf")
            with open(legacy_file, "wb") as fh:
                fh.write(b"%PDF-1.4 legacy")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"download_folder": "%s"}' % legacy_downloads.replace("\\", "\\\\"))

            with mock.patch("agent.config_store._legacy_download_folder", return_value=legacy_downloads), mock.patch(
                "agent.config_store._default_download_folder",
                return_value=os.path.join(tmp, "Library", "Application Support", "NjordHR", "Resumes"),
            ):
                store = AgentConfigStore(path=path)

            cfg = store.get()
            expected_download_folder = os.path.join(tmp, "Library", "Application Support", "NjordHR", "Resumes")
            self.assertEqual(cfg["download_folder"], expected_download_folder)
            self.assertTrue(os.path.exists(os.path.join(expected_download_folder, "resume.pdf")))
            self.assertFalse(os.path.exists(legacy_file))

    def test_config_store_migrates_legacy_temp12_download_folder_into_app_managed_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "app")
            os.makedirs(app_dir, exist_ok=True)
            path = os.path.join(app_dir, "agent.json")
            legacy_downloads = os.path.join(tmp, "temp12")
            os.makedirs(legacy_downloads, exist_ok=True)
            legacy_file = os.path.join(legacy_downloads, "resume.pdf")
            with open(legacy_file, "wb") as fh:
                fh.write(b"%PDF-1.4 legacy")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"download_folder": "%s"}' % legacy_downloads.replace("\\", "\\\\"))

            with mock.patch("agent.config_store._legacy_temp_download_folder", return_value=legacy_downloads), mock.patch(
                "agent.config_store._default_download_folder",
                return_value=os.path.join(tmp, "Library", "Application Support", "NjordHR", "Resumes"),
            ):
                store = AgentConfigStore(path=path)

            cfg = store.get()
            expected_download_folder = os.path.join(tmp, "Library", "Application Support", "NjordHR", "Resumes")
            self.assertEqual(cfg["download_folder"], expected_download_folder)
            self.assertTrue(os.path.exists(os.path.join(expected_download_folder, "resume.pdf")))
            self.assertFalse(os.path.exists(legacy_file))

    def test_ensure_writable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg, path = ensure_writable_folder(tmp)
            self.assertTrue(ok, msg)
            self.assertEqual(path, tmp)

    def test_ensure_writable_folder_rejects_empty_value(self):
        ok, msg, path = ensure_writable_folder("")
        self.assertFalse(ok)
        self.assertEqual(msg, "Folder path is empty")
        self.assertEqual(path, "")

    def test_ensure_writable_folder_rejects_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "not-a-folder.txt")
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write("hello")
            ok, msg, path = ensure_writable_folder(file_path)
            self.assertFalse(ok)
            self.assertIn("Path is not a directory", msg)
            self.assertEqual(path, os.path.abspath(file_path))

    def test_secret_store_uses_backend(self):
        class FakeBackend:
            def __init__(self):
                self.rows = {}

            def get_password(self, service, key):
                return self.rows.get((service, key))

            def set_password(self, service, key, value):
                self.rows[(service, key)] = value

            def delete_password(self, service, key):
                self.rows.pop((service, key), None)

        backend = FakeBackend()
        store = SecretStore(service_name="NjordHR.Test", backend=backend)
        self.assertTrue(store.available())
        store.set("cache", "hello")
        self.assertEqual(store.get("cache"), "hello")
        store.delete("cache")
        self.assertIsNone(store.get("cache"))

    def test_secret_store_available_requires_round_trip(self):
        class BrokenBackend:
            def set_password(self, *_args):
                raise RuntimeError("credential manager unavailable")

            def get_password(self, *_args):
                return None

            def delete_password(self, *_args):
                return None

        store = SecretStore(service_name="NjordHR.Test", backend=BrokenBackend())
        self.assertFalse(store.available())

    def test_secret_store_chunks_large_values_for_windows_credential_limits(self):
        class SizeLimitedBackend:
            def __init__(self):
                self.rows = {}

            def get_password(self, service, key):
                return self.rows.get((service, key))

            def set_password(self, service, key, value):
                if len(value.encode("utf-16-le")) > 2560:
                    raise RuntimeError("CredWrite bad data")
                self.rows[(service, key)] = value

            def delete_password(self, service, key):
                self.rows.pop((service, key), None)

        backend = SizeLimitedBackend()
        store = SecretStore(service_name="NjordHR.Test", backend=backend)
        value = "x" * 3200

        store.set("cache", value)

        self.assertEqual(store.get("cache"), value)
        self.assertIsNone(backend.get_password("NjordHR.Test", "cache"))
        self.assertIsNotNone(backend.get_password("NjordHR.Test", "cache.__chunks__"))
        store.delete("cache")
        self.assertIsNone(store.get("cache"))

    def test_job_queue_runs_worker(self):
        def worker(job_id, payload, emit):
            emit(job_id, "log", "hello", {})
            return {"success": True, "message": "ok", "payload": payload}

        q = AgentJobQueue(worker)
        try:
            job_id = q.submit({"rank": "Chief Officer"})
            deadline = time.time() + 5
            while time.time() < deadline:
                job = q.get_job(job_id)
                if job and job.get("status") in {"success", "failed"}:
                    break
                time.sleep(0.05)
            job = q.get_job(job_id)
            self.assertEqual(job.get("status"), "success")
            events = q.get_events_since(job_id, 0)
            self.assertTrue(any(e["type"] == "log" for e in events))
            self.assertTrue(any(e["type"] == "complete" for e in events))
        finally:
            q.shutdown()

    def test_rank_folder_helpers_match_seajobs_slugging(self):
        self.assertEqual(rank_folder_slug("Chief Officer"), "Chief_Officer")
        self.assertEqual(rank_folder_slug("NCV/NWKO"), "NCV-NWKO")
        self.assertEqual(
            str(rank_folder_path("/tmp/downloads", "Add 2nd Officer")),
            "/tmp/downloads/Add_2nd_Officer",
        )


if __name__ == "__main__":
    unittest.main()
