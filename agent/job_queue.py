import queue
import threading
import time
import uuid


class AgentJobQueue:
    def __init__(self, worker_fn):
        self.worker_fn = worker_fn
        self._q = queue.Queue()
        self._jobs = {}
        self._events = {}
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, payload):
        job_id = str(uuid.uuid4())
        now = time.time()
        record = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "ended_at": None,
            "payload": payload,
            "result": None,
            "error": "",
        }
        with self._cond:
            self._jobs[job_id] = record
            self._events[job_id] = []
            self._append_event(job_id, "queued", "Job queued", {"payload": payload})
        self._q.put(job_id)
        return job_id

    def shutdown(self):
        self._stop.set()
        self._q.put(None)
        self._thread.join(timeout=2)

    def get_job(self, job_id):
        with self._cond:
            return dict(self._jobs.get(job_id, {}))

    def get_events_since(self, job_id, last_seq=0):
        with self._cond:
            rows = self._events.get(job_id, [])
            return [e for e in rows if e["seq"] > last_seq]

    def wait_for_events(self, job_id, last_seq=0, timeout=15):
        with self._cond:
            self._cond.wait_for(
                lambda: any(e["seq"] > last_seq for e in self._events.get(job_id, [])) or self._stop.is_set(),
                timeout=timeout
            )
            rows = self._events.get(job_id, [])
            return [e for e in rows if e["seq"] > last_seq]

    def emit(self, job_id, event_type, message, data=None):
        with self._cond:
            self._append_event(job_id, event_type, message, data or {})

    def _append_event(self, job_id, event_type, message, data):
        rows = self._events.setdefault(job_id, [])
        seq = rows[-1]["seq"] + 1 if rows else 1
        rows.append({
            "seq": seq,
            "ts": time.time(),
            "type": event_type,
            "message": message,
            "data": data,
        })
        self._cond.notify_all()

    def _run(self):
        while not self._stop.is_set():
            job_id = self._q.get()
            if not job_id:
                continue
            with self._cond:
                job = self._jobs.get(job_id)
                if not job:
                    continue
                job["status"] = "running"
                job["started_at"] = time.time()
                self._append_event(job_id, "running", "Job started", {})
            try:
                result = self.worker_fn(job_id, job["payload"], self.emit)
                with self._cond:
                    job = self._jobs.get(job_id)
                    if not job:
                        continue
                    job["result"] = result
                    job["status"] = "success" if result.get("success") else "failed"
                    job["ended_at"] = time.time()
                    self._append_event(
                        job_id,
                        "complete",
                        result.get("message", "Job completed"),
                        {"result": result},
                    )
            except Exception as exc:
                with self._cond:
                    job = self._jobs.get(job_id)
                    if not job:
                        continue
                    job["status"] = "failed"
                    job["error"] = str(exc)
                    job["ended_at"] = time.time()
                    self._append_event(job_id, "error", f"Job failed: {exc}", {})

