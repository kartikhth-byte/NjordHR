import os
import tempfile
import time
import unittest

from agent.config_store import AgentConfigStore
from agent.filesystem import ensure_writable_folder
from agent.job_queue import AgentJobQueue


class AgentComponentsTests(unittest.TestCase):
    def test_config_store_defaults_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent.json")
            store = AgentConfigStore(path=path)
            cfg = store.get()
            self.assertTrue(cfg["device_id"])
            self.assertTrue(os.path.isabs(cfg["download_folder"]))

            updated = store.update({"cloud_sync_enabled": False, "download_folder": tmp})
            self.assertFalse(updated["cloud_sync_enabled"])
            self.assertEqual(updated["download_folder"], tmp)

    def test_ensure_writable_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg, path = ensure_writable_folder(tmp)
            self.assertTrue(ok, msg)
            self.assertEqual(path, tmp)

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


if __name__ == "__main__":
    unittest.main()

