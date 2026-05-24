import logging
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone

from logger_config import setup_logger
from rank_folders import rank_folder_slug

from .job_queue import AgentJobQueue


class AgentRuntime:
    def __init__(self, *, settings_store, session_getter, session_health_getter, email_intake, sync_client):
        self.settings_store = settings_store
        self.session_getter = session_getter
        self.session_health_getter = session_health_getter
        self.email_intake = email_intake
        self.sync_client = sync_client
        self._job_lock = threading.RLock()
        self.job_queue = AgentJobQueue(self._run_agent_job)

    def shutdown(self):
        self.job_queue.shutdown()

    def submit_download_job(self, rank, ship_type, force_redownload=False):
        return self.job_queue.submit({
            "job_type": "download",
            "rank": rank,
            "ship_type": ship_type,
            "force_redownload": bool(force_redownload),
        })

    def submit_email_intake_job(self):
        return self.job_queue.submit({"job_type": "email_intake_fetch"})

    def get_job(self, job_id):
        return self.job_queue.get_job(job_id)

    def wait_for_events(self, job_id, last_seq=0, timeout=15):
        return self.job_queue.wait_for_events(job_id, last_seq=last_seq, timeout=timeout)

    @staticmethod
    def _now_iso():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _emit_progress(emit, stage, message, percent=None, data=None):
        payload = {"stage": stage}
        if percent is not None:
            payload["percent"] = percent
        if data:
            payload.update(data)
        emit("progress", message, payload)

    @staticmethod
    def _safe_sync_call(action, fn, fallback=None):
        try:
            return fn()
        except Exception as exc:
            print(f"[agent-sync] {action} failed: {exc}", file=sys.stderr)
            return fallback

    def _run_agent_job(self, job_id, payload, emit):
        job_type = str((payload or {}).get("job_type", "download")).strip() or "download"

        def _emit(event_type, message, data=None):
            emit(job_id, event_type, message, data or {})

        if job_type == "download":
            return self._run_download_job(job_id, payload, _emit)
        if job_type == "email_intake_fetch":
            self._emit_progress(_emit, "email_intake", "Checking Outlook mailbox", 10)
            return self.email_intake.fetch_from_outlook(lambda event_type, message: _emit(event_type, message, {}))
        return {"success": False, "message": f"Unsupported job type: {job_type}"}

    @staticmethod
    def _extract_saved_files(log_lines):
        rows = []
        for line in log_lines or []:
            m = re.search(r"Saved:\s+(.+\.pdf)$", str(line))
            if m:
                rows.append(m.group(1).strip())
        return rows

    def _run_download_job(self, job_id, payload, emit):
        rank = str(payload.get("rank", "")).strip()
        ship_type = str(payload.get("ship_type", "")).strip()
        force = bool(payload.get("force_redownload", False))
        if not rank or not ship_type:
            return {"success": False, "message": "rank and ship_type are required"}

        with self._job_lock:
            scraper = self.session_getter()
        if not scraper:
            return {"success": False, "message": "No active session. Start and verify OTP first."}

        health = self.session_health_getter()
        if not health.get("valid"):
            return {"success": False, "message": f"Session invalid: {health.get('reason', 'unknown')}"}

        settings = self.settings_store.get()
        logs_dir = os.path.join(self.settings_store.base_dir, "logs")
        logger, log_path = setup_logger(str(uuid.uuid4()), logs_dir=logs_dir)

        self._emit_progress(
            emit,
            "preflight",
            "Validated session and download folder",
            10,
            {"rank": rank, "ship_type": ship_type, "force_redownload": force},
        )

        class _QueueLogHandler(logging.Handler):
            def emit(self, record):
                message = self.format(record)
                emit("log", message, {"level": record.levelname})
                self_outer._safe_sync_call(
                    "push_job_log",
                    lambda: self_outer.sync_client.push_job_log({
                        "job_id": job_id,
                        "level": record.levelname,
                        "line": message,
                        "device_id": settings.get("device_id", ""),
                    }),
                )

        self_outer = self
        qh = _QueueLogHandler()
        qh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(qh)

        started_payload = {
            "job_id": job_id,
            "device_id": settings.get("device_id", ""),
            "rank": rank,
            "ship_type": ship_type,
            "status": "running",
            "started_at": self._now_iso(),
        }
        self._safe_sync_call("push_job_state(started)", lambda: self.sync_client.push_job_state(started_payload))

        terminal_payload = None
        try:
            self._emit_progress(emit, "download", "Starting SeaJobs download", 25)
            result = scraper.download_resumes(rank, ship_type, force, logger)

            saved_files = self._extract_saved_files(result.get("log", []))
            rank_folder = rank_folder_slug(rank)
            download_folder = settings.get("download_folder", "")
            upload_rows = []
            if saved_files:
                self._emit_progress(
                    emit,
                    "uploading",
                    f"Preparing {len(saved_files)} downloaded resume(s) for upload",
                    65,
                    {"total_files": len(saved_files)},
                )
            for idx, filename in enumerate(saved_files, start=1):
                abs_path = os.path.join(download_folder, rank_folder, filename)
                self._emit_progress(
                    emit,
                    "uploading",
                    f"Uploading {filename}",
                    min(95, 65 + int(idx * 30 / max(len(saved_files), 1))),
                    {"filename": filename, "total_files": len(saved_files), "file_index": idx},
                )
                upload = self._safe_sync_call(
                    "upload_resume",
                    lambda: self.sync_client.upload_resume(abs_path, {
                        "job_id": job_id,
                        "rank_applied_for": rank_folder,
                        "device_id": settings.get("device_id", ""),
                        "candidate_external_id": (
                            re.search(r"_(\d+)(?:_|\.)", filename).group(1)
                            if re.search(r"_(\d+)(?:_|\.)", filename)
                            else ""
                        ),
                    }),
                    fallback={
                        "resume_source": "local_only",
                        "resume_upload_status": "skipped",
                        "resume_storage_path": "",
                        "resume_checksum_sha256": "",
                    },
                )
                upload_rows.append({"filename": filename, **upload})
                self._safe_sync_call(
                    "push_candidate_event",
                    lambda: self.sync_client.push_candidate_event({
                        "job_id": job_id,
                        "filename": filename,
                        "rank_applied_for": rank_folder,
                        "event_type": "resume_downloaded",
                        "resume_source": upload.get("resume_source", "local_only"),
                        "resume_upload_status": upload.get("resume_upload_status", "skipped"),
                        "resume_storage_path": upload.get("resume_storage_path", ""),
                        "resume_checksum_sha256": upload.get("resume_checksum_sha256", ""),
                        "device_id": settings.get("device_id", ""),
                    }),
                )
            if not saved_files:
                self._emit_progress(emit, "finalizing", "No resume files were extracted", 90)
            else:
                self._emit_progress(emit, "finalizing", "Download and upload steps finished", 95)

            terminal_payload = {
                "job_id": job_id,
                "device_id": settings.get("device_id", ""),
                "rank": rank,
                "ship_type": ship_type,
                "status": "success" if result.get("success") else "failed",
                "ended_at": self._now_iso(),
                "message": result.get("message", ""),
                "saved_files": len(saved_files),
            }

            return {
                "success": bool(result.get("success")),
                "message": result.get("message", ""),
                "log_file": log_path,
                "saved_files": saved_files,
                "uploads": upload_rows,
            }
        except Exception as exc:
            terminal_payload = {
                "job_id": job_id,
                "device_id": settings.get("device_id", ""),
                "rank": rank,
                "ship_type": ship_type,
                "status": "failed",
                "ended_at": self._now_iso(),
                "message": str(exc),
                "saved_files": 0,
            }
            raise
        finally:
            logger.removeHandler(qh)
            qh.close()
            if terminal_payload is not None:
                self._safe_sync_call("push_job_state(terminal)", lambda: self.sync_client.push_job_state(terminal_payload))
