import hashlib
import json
import os
import tempfile
import unittest
import time
import threading
from unittest.mock import patch

from agent.cloud_sync import CloudSyncClient


class _FakeConfigStore:
    def __init__(self, base_dir):
        self._cfg = {
            "api_base_url": "https://cloud.example.test",
            "device_token": "device-token-123",
            "cloud_sync_enabled": True,
            "cloud_upload_resumes": True,
        }
        self.base_dir = base_dir

    def get(self):
        return dict(self._cfg)


class CloudSyncClientTests(unittest.TestCase):
    def test_push_methods_enqueue_expected_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))

            try:
                client.push_job_state({"job_id": "job-1", "status": "running"})
                client.push_job_log({"job_id": "job-1", "line": "hello"})
                client.push_candidate_event({"job_id": "job-1", "event_type": "resume_downloaded"})

                self.assertEqual(len(client._pending), 3)
                endpoints = [item["endpoint"] for item in client._pending]
                self.assertEqual(endpoints, [
                    "/api/agent/job-state",
                    "/api/agent/job-log",
                    "/api/events/candidate",
                ])
                self.assertTrue(all(item["idempotency_key"] for item in client._pending))

                with patch("agent.cloud_sync.requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.text = "ok"
                    ok, err = client._post_item(client._pending[0])

                self.assertTrue(ok)
                self.assertEqual(err, "")
                mock_post.assert_called_once()
                args, kwargs = mock_post.call_args
                self.assertEqual(args[0], "https://cloud.example.test/api/agent/job-state")
                self.assertEqual(kwargs["json"], {"job_id": "job-1", "status": "running"})
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer device-token-123")
                self.assertEqual(kwargs["headers"]["X-Device-Token"], "device-token-123")
                self.assertIn("X-Idempotency-Key", kwargs["headers"])
            finally:
                client.stop()

    def test_upload_resume_enqueues_multipart_upload_with_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            try:
                result = client.upload_resume(pdf_path, {
                    "job_id": "job-2",
                    "candidate_external_id": "1001",
                    "rank_applied_for": "Chief_Officer",
                })

                expected_checksum = hashlib.sha256(b"%PDF-1.4 resume bytes").hexdigest()
                self.assertEqual(result["resume_source"], "cloud_sync_pending")
                self.assertEqual(result["resume_upload_status"], "pending")
                self.assertEqual(result["resume_checksum_sha256"], expected_checksum)
                self.assertEqual(len(client._pending), 1)
                item = client._pending[0]
                self.assertEqual(item["kind"], "resume_upload")
                self.assertEqual(item["endpoint"], "/api/agent/resume-upload")
                self.assertEqual(item["payload"]["file_path"], pdf_path)
                self.assertEqual(item["payload"]["metadata"]["resume_checksum_sha256"], expected_checksum)
                self.assertEqual(item["payload"]["metadata"]["filename"], "resume.pdf")

                with patch("agent.cloud_sync.requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.text = "ok"
                    ok, err = client._post_item(item)

                self.assertTrue(ok)
                self.assertEqual(err, "")
                mock_post.assert_called_once()
                args, kwargs = mock_post.call_args
                self.assertEqual(args[0], "https://cloud.example.test/api/agent/resume-upload")
                self.assertNotIn("Content-Type", kwargs["headers"])
                self.assertEqual(kwargs["data"]["metadata"], json.dumps({
                    "job_id": "job-2",
                    "candidate_external_id": "1001",
                    "rank_applied_for": "Chief_Officer",
                    "resume_checksum_sha256": expected_checksum,
                    "filename": "resume.pdf",
                }, sort_keys=True, separators=(",", ":")))
                file_tuple = kwargs["files"]["file"]
                self.assertEqual(file_tuple[0], "resume.pdf")
                self.assertEqual(file_tuple[2], "application/pdf")
            finally:
                client.stop()

    def test_resume_upload_result_is_retained_in_stats_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            try:
                client.upload_resume(pdf_path, {
                    "job_id": "job-2",
                    "candidate_external_id": "1001",
                    "rank_applied_for": "Chief_Officer",
                })
                item = client._pending[0]

                class SuccessResponse:
                    status_code = 200
                    text = json.dumps({
                        "success": True,
                        "upload_status": "uploaded",
                        "resume_source": "cloud_synced",
                        "resume_upload_status": "uploaded",
                        "resume_storage_path": "storage://resumes/Chief_Officer/1001/resume.pdf",
                        "resume_checksum_sha256": hashlib.sha256(b"%PDF-1.4 resume bytes").hexdigest(),
                    })

                    def json(self):
                        return json.loads(self.text)

                with patch("agent.cloud_sync.requests.post", return_value=SuccessResponse()):
                    ok, err = client._post_item(item)

                self.assertTrue(ok)
                self.assertEqual(err, "")
                stats = client.stats()
                self.assertEqual(stats["last_resume_upload"]["upload_status"], "uploaded")
                self.assertEqual(stats["last_resume_upload"]["resume_source"], "cloud_synced")
                self.assertEqual(stats["last_resume_upload"]["resume_upload_status"], "uploaded")
                self.assertTrue(stats["last_resume_upload"]["resume_storage_path"].startswith("storage://resumes/Chief_Officer/1001/"))
                self.assertEqual(stats["last_resume_upload"]["resume_checksum_sha256"], hashlib.sha256(b"%PDF-1.4 resume bytes").hexdigest())
            finally:
                client.stop()

    def test_resume_upload_duplicate_result_is_retained_in_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            try:
                client.upload_resume(pdf_path, {
                    "job_id": "job-2",
                    "candidate_external_id": "1001",
                    "rank_applied_for": "Chief_Officer",
                })
                item = client._pending[0]

                class DuplicateResponse:
                    status_code = 409
                    text = '{"success":true,"duplicate":true,"upload_status":"duplicate"}'

                    def json(self):
                        return {"success": True, "duplicate": True, "upload_status": "duplicate"}

                with patch("agent.cloud_sync.requests.post", return_value=DuplicateResponse()):
                    ok, err = client._post_item(item)

                self.assertTrue(ok)
                self.assertEqual(err, "duplicate_accepted")
                stats = client.stats()
                self.assertEqual(stats["last_resume_upload"]["upload_status"], "duplicate")
                self.assertTrue(stats["last_resume_upload"]["duplicate"])
            finally:
                client.stop()

    def test_stats_reports_pending_queue_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            try:
                client.push_job_state({"job_id": "job-3"})
                stats = client.stats()
                self.assertEqual(stats["pending"], 1)
                self.assertTrue(stats["queue_path"].endswith("pending_sync_queue.json"))
                self.assertFalse(stats["reconnect_wakeup_pending"])
                self.assertIsNone(stats["last_resume_upload"])
                self.assertTrue(stats["sync_ready"])
                self.assertFalse(stats["offline_mode"])
            finally:
                client.stop()

    def test_upload_resume_is_deferred_while_offline_then_replays_on_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            store._cfg["api_base_url"] = ""
            store._cfg["cloud_sync_enabled"] = False
            state_dir = os.path.join(tmp, "state")
            client = CloudSyncClient(store, state_dir)
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            try:
                result = client.upload_resume(pdf_path, {
                    "job_id": "job-offline",
                    "candidate_external_id": "3003",
                    "rank_applied_for": "Chief_Officer",
                })
                self.assertEqual(result["resume_source"], "cloud_sync_pending")
                self.assertEqual(result["resume_upload_status"], "pending")
                self.assertEqual(client.stats()["pending"], 1)
                self.assertTrue(client.stats()["offline_mode"])

                class SuccessResponse:
                    status_code = 200
                    text = json.dumps({
                        "success": True,
                        "upload_status": "uploaded",
                        "resume_source": "cloud_synced",
                        "resume_upload_status": "uploaded",
                        "resume_storage_path": "storage://resumes/Chief_Officer/3003/resume.pdf",
                        "resume_checksum_sha256": hashlib.sha256(b"%PDF-1.4 resume bytes").hexdigest(),
                    })

                    def json(self):
                        return json.loads(self.text)

                store._cfg["api_base_url"] = "https://cloud.example.test"
                store._cfg["cloud_sync_enabled"] = True
                with patch("agent.cloud_sync.requests.post", return_value=SuccessResponse()) as mock_post:
                    client.start()
                    client.signal_reconnect()
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        if client.stats()["pending"] == 0:
                            break
                        time.sleep(0.05)

                self.assertEqual(client.stats()["pending"], 0)
                self.assertTrue(mock_post.called)
                stats = client.stats()
                self.assertFalse(stats["offline_mode"])
                self.assertEqual(stats["last_resume_upload"]["upload_status"], "uploaded")
                self.assertEqual(stats["last_resume_upload"]["resume_source"], "cloud_synced")
            finally:
                client.stop()

    def test_pending_resume_upload_survives_restart_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            state_dir = os.path.join(tmp, "state")
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            client1 = CloudSyncClient(store, state_dir)
            try:
                client1.upload_resume(pdf_path, {
                    "job_id": "job-5",
                    "candidate_external_id": "2002",
                    "rank_applied_for": "Chief_Officer",
                })
                queue_file = os.path.join(state_dir, "pending_sync_queue.json")
                self.assertTrue(os.path.isfile(queue_file))
                with open(queue_file, "r", encoding="utf-8") as fh:
                    persisted = json.load(fh)
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["kind"], "resume_upload")
            finally:
                client1.stop()

            client2 = CloudSyncClient(store, state_dir)

            class SuccessResponse:
                status_code = 200
                text = json.dumps({
                    "success": True,
                    "upload_status": "uploaded",
                    "resume_source": "cloud_synced",
                    "resume_upload_status": "uploaded",
                    "resume_storage_path": "storage://resumes/Chief_Officer/2002/resume.pdf",
                    "resume_checksum_sha256": hashlib.sha256(b"%PDF-1.4 resume bytes").hexdigest(),
                })

                def json(self):
                    return json.loads(self.text)

            try:
                self.assertEqual(client2.stats()["pending"], 1)
                with patch("agent.cloud_sync.requests.post", return_value=SuccessResponse()) as mock_post:
                    client2.start()
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        if client2.stats()["pending"] == 0:
                            break
                        time.sleep(0.05)

                self.assertEqual(client2.stats()["pending"], 0)
                self.assertTrue(mock_post.called)
                with open(queue_file, "r", encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh), [])
                stats = client2.stats()
                self.assertEqual(stats["last_resume_upload"]["upload_status"], "uploaded")
                self.assertEqual(stats["last_resume_upload"]["resume_source"], "cloud_synced")
            finally:
                client2.stop()

    def test_missing_resume_file_keeps_retryable_queue_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            state_dir = os.path.join(tmp, "state")
            pdf_path = os.path.join(tmp, "resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4 resume bytes")

            client = CloudSyncClient(store, state_dir)
            try:
                client.upload_resume(pdf_path, {
                    "job_id": "job-missing",
                    "candidate_external_id": "9001",
                    "rank_applied_for": "Chief_Officer",
                })
                os.remove(pdf_path)
                client.start()
                deadline = time.time() + 5
                while time.time() < deadline:
                    stats = client.stats()
                    if stats["pending"] == 1 and client._pending[0].get("last_error"):
                        break
                    time.sleep(0.05)

                self.assertEqual(client.stats()["pending"], 1)
                self.assertIn("file_missing", client._pending[0]["last_error"])
            finally:
                client.stop()

    def test_duplicate_response_is_treated_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            try:
                client.push_job_state({"job_id": "job-4", "status": "running"})
                item = client._pending[0]

                class DuplicateResponse:
                    status_code = 409
                    text = '{"success":true,"duplicate":true}'

                    def json(self):
                        return {"success": True, "duplicate": True}

                with patch("agent.cloud_sync.requests.post", return_value=DuplicateResponse()) as mock_post:
                    ok, err = client._post_item(item)

                self.assertTrue(ok)
                self.assertEqual(err, "duplicate_accepted")
                mock_post.assert_called_once()
            finally:
                client.stop()

    def test_signal_reconnect_wakes_wait_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _FakeConfigStore(tmp)
            client = CloudSyncClient(store, os.path.join(tmp, "state"))
            try:
                start = time.time()
                result = {"done": False}

                def waiter():
                    client._wait_for_wake(5)
                    result["done"] = True

                thread = threading.Thread(target=waiter)
                thread.start()
                time.sleep(0.1)
                client.signal_reconnect()
                thread.join(timeout=2)

                self.assertTrue(result["done"])
                self.assertLess(time.time() - start, 2)
                self.assertFalse(client.stats()["reconnect_wakeup_pending"])
            finally:
                client.stop()


if __name__ == "__main__":
    unittest.main()
