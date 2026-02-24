import hashlib
import json
import os
import threading
import time
from collections import deque

import requests


def _json_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _idempotency_key(kind, payload):
    blob = f"{kind}:{_json_dumps(payload)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class CloudSyncClient:
    """Retry-safe cloud sync queue for agent-originated events and uploads."""

    def __init__(self, config_store, state_dir):
        self.config_store = config_store
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)
        self.queue_path = os.path.join(self.state_dir, "pending_sync_queue.json")
        self._lock = threading.RLock()
        self._pending = deque()
        self._stop = threading.Event()
        self._thread = None
        self._load_pending()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def stats(self):
        with self._lock:
            return {"pending": len(self._pending), "queue_path": self.queue_path}

    def enqueue(self, kind, endpoint, payload, idem_key=None):
        item = {
            "kind": kind,
            "endpoint": endpoint,
            "payload": payload,
            "idempotency_key": idem_key or _idempotency_key(kind, payload),
            "attempts": 0,
            "next_retry_at": 0.0,
            "last_error": "",
            "created_at": time.time(),
        }
        with self._lock:
            self._pending.append(item)
            self._save_pending()
        return item["idempotency_key"]

    def push_job_state(self, payload):
        self.enqueue("job_state", "/api/agent/job-state", payload)

    def push_job_log(self, payload):
        self.enqueue("job_log", "/api/agent/job-log", payload)

    def push_candidate_event(self, payload):
        self.enqueue("candidate_event", "/api/events/candidate", payload)

    def upload_resume(self, file_path, metadata):
        cfg = self.config_store.get()
        if not cfg.get("cloud_sync_enabled"):
            return {
                "resume_source": "local_only",
                "resume_upload_status": "disabled",
                "resume_storage_path": "",
                "resume_checksum_sha256": "",
            }
        if not cfg.get("cloud_upload_resumes"):
            return {
                "resume_source": "local_only",
                "resume_upload_status": "skipped",
                "resume_storage_path": "",
                "resume_checksum_sha256": "",
            }
        if not os.path.isfile(file_path):
            return {
                "resume_source": "local_only",
                "resume_upload_status": "failed",
                "resume_storage_path": "",
                "resume_checksum_sha256": "",
            }

        checksum = _sha256_file(file_path)
        meta = dict(metadata or {})
        meta["resume_checksum_sha256"] = checksum
        meta["filename"] = os.path.basename(file_path)
        idem_key = _idempotency_key("resume_upload", meta)

        self.enqueue(
            "resume_upload",
            "/api/agent/resume-upload",
            {"metadata": meta, "file_path": file_path},
            idem_key=idem_key,
        )
        return {
            "resume_source": "cloud_sync_pending",
            "resume_upload_status": "pending",
            "resume_storage_path": "",
            "resume_checksum_sha256": checksum,
        }

    def _load_pending(self):
        with self._lock:
            if not os.path.exists(self.queue_path):
                self._pending = deque()
                return
            try:
                with open(self.queue_path, "r", encoding="utf-8") as fh:
                    rows = json.load(fh)
                self._pending = deque(rows if isinstance(rows, list) else [])
            except Exception:
                self._pending = deque()

    def _save_pending(self):
        with self._lock:
            tmp = f"{self.queue_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(list(self._pending), fh)
            os.replace(tmp, self.queue_path)

    def _headers(self):
        cfg = self.config_store.get()
        token = str(cfg.get("device_token", "")).strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Device-Token"] = token
        return headers

    def _base_url(self):
        cfg = self.config_store.get()
        return str(cfg.get("api_base_url", "")).rstrip("/")

    def _can_sync(self):
        cfg = self.config_store.get()
        return bool(cfg.get("cloud_sync_enabled") and self._base_url())

    def _post_item(self, item):
        base = self._base_url()
        if not base:
            return False, "api_base_url not configured"
        url = f"{base}{item['endpoint']}"
        timeout = 20
        payload = item["payload"]
        headers = self._headers()
        headers["X-Idempotency-Key"] = item["idempotency_key"]

        try:
            if item["kind"] == "resume_upload":
                meta = payload.get("metadata", {})
                file_path = payload.get("file_path", "")
                if not os.path.isfile(file_path):
                    return True, "file_missing_skip"
                with open(file_path, "rb") as fh:
                    files = {"file": (os.path.basename(file_path), fh, "application/pdf")}
                    data = {"metadata": _json_dumps(meta)}
                    local_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
                    resp = requests.post(url, headers=local_headers, files=files, data=data, timeout=timeout)
            else:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as exc:
            return False, f"request_error: {exc}"
        if resp.status_code >= 400:
            return False, f"{resp.status_code} {resp.text[:500]}"
        return True, ""

    def _run(self):
        while not self._stop.is_set():
            if not self._can_sync():
                time.sleep(2)
                continue
            item = None
            with self._lock:
                now = time.time()
                if self._pending and self._pending[0].get("next_retry_at", 0) <= now:
                    item = self._pending[0]
            if not item:
                time.sleep(0.5)
                continue

            ok, err = self._post_item(item)
            with self._lock:
                if not self._pending:
                    continue
                head = self._pending[0]
                if head.get("idempotency_key") != item.get("idempotency_key"):
                    continue
                if ok:
                    self._pending.popleft()
                else:
                    attempts = int(head.get("attempts", 0)) + 1
                    backoff = min(300, 2 ** min(attempts, 8))
                    head["attempts"] = attempts
                    head["last_error"] = err
                    head["next_retry_at"] = time.time() + backoff
                self._save_pending()
