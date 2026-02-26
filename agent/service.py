import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime

from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS

from app_settings import load_app_settings
from logger_config import setup_logger
from scraper_engine import Scraper

from .cloud_sync import CloudSyncClient
from .config_store import AgentConfigStore
from .filesystem import ensure_writable_folder
from .job_queue import AgentJobQueue


def create_agent_app():
    app = Flask(__name__)
    CORS(app)

    app_settings = load_app_settings()
    creds = app_settings.credentials
    parser = app_settings.config
    settings_store = AgentConfigStore()
    sync_client = CloudSyncClient(settings_store, os.path.join(settings_store.base_dir, "state"))
    sync_client.start()

    session_lock = threading.RLock()
    scraper_session = {"scraper": None}

    def _session_health():
        with session_lock:
            scraper = scraper_session["scraper"]
        if not scraper:
            return {"active": False, "valid": False, "reason": "No active session"}
        return scraper.get_session_health()

    def _rank_slug(rank):
        return str(rank or "").replace(" ", "_").replace("/", "-")

    def _build_scraper():
        cfg = settings_store.get()
        download_folder = cfg.get("download_folder", "")
        ok, msg, normalized = ensure_writable_folder(download_folder)
        if not ok:
            raise RuntimeError(msg)
        return Scraper(
            normalized,
            otp_window_seconds=int(parser.get("Advanced", "otp_window_seconds", fallback="120")),
            login_url=parser.get("Advanced", "seajob_login_url", fallback="http://seajob.net/seajob_login.php"),
            dashboard_url=parser.get("Advanced", "seajob_dashboard_url", fallback="http://seajob.net/company/dashboard.php"),
        )

    def _extract_saved_files(log_lines):
        rows = []
        for line in log_lines or []:
            m = re.search(r"Saved:\s+(.+\.pdf)$", str(line))
            if m:
                rows.append(m.group(1).strip())
        return rows

    def _run_download_job(job_id, payload, emit):
        rank = str(payload.get("rank", "")).strip()
        ship_type = str(payload.get("ship_type", "")).strip()
        force = bool(payload.get("force_redownload", False))
        if not rank or not ship_type:
            return {"success": False, "message": "rank and ship_type are required"}

        with session_lock:
            scraper = scraper_session["scraper"]
        if not scraper:
            return {"success": False, "message": "No active session. Start and verify OTP first."}

        health = scraper.get_session_health()
        if not health.get("valid"):
            return {"success": False, "message": f"Session invalid: {health.get('reason', 'unknown')}"}

        settings = settings_store.get()
        logs_dir = os.path.join(settings_store.base_dir, "logs")
        logger, log_path = setup_logger(str(uuid.uuid4()), logs_dir=logs_dir)

        # Stream live logs to SSE + cloud sync.
        import logging
        class _QueueLogHandler(logging.Handler):
            def emit(self, record):
                message = self.format(record)
                emit(job_id, "log", message, {"level": record.levelname})
                sync_client.push_job_log({
                    "job_id": job_id,
                    "level": record.levelname,
                    "line": message,
                    "device_id": settings.get("device_id", ""),
                })

        qh = _QueueLogHandler()
        qh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(qh)

        started_payload = {
            "job_id": job_id,
            "device_id": settings.get("device_id", ""),
            "rank": rank,
            "ship_type": ship_type,
            "status": "running",
            "started_at": datetime.utcnow().isoformat() + "Z",
        }
        sync_client.push_job_state(started_payload)

        try:
            result = scraper.download_resumes(rank, ship_type, force, logger)
        finally:
            logger.removeHandler(qh)
            qh.close()

        saved_files = _extract_saved_files(result.get("log", []))
        rank_folder = _rank_slug(rank)
        download_folder = settings.get("download_folder", "")
        upload_rows = []
        for filename in saved_files:
            abs_path = os.path.join(download_folder, rank_folder, filename)
            upload = sync_client.upload_resume(abs_path, {
                "job_id": job_id,
                "rank_applied_for": rank_folder,
                "device_id": settings.get("device_id", ""),
                "candidate_external_id": (
                    re.search(r"_(\d+)(?:_|\.)", filename).group(1)
                    if re.search(r"_(\d+)(?:_|\.)", filename)
                    else ""
                ),
            })
            upload_rows.append({"filename": filename, **upload})
            sync_client.push_candidate_event({
                "job_id": job_id,
                "filename": filename,
                "rank_applied_for": rank_folder,
                "event_type": "resume_downloaded",
                "resume_source": upload.get("resume_source", "local_only"),
                "resume_upload_status": upload.get("resume_upload_status", "skipped"),
                "resume_storage_path": upload.get("resume_storage_path", ""),
                "resume_checksum_sha256": upload.get("resume_checksum_sha256", ""),
                "device_id": settings.get("device_id", ""),
            })

        sync_client.push_job_state({
            "job_id": job_id,
            "device_id": settings.get("device_id", ""),
            "rank": rank,
            "ship_type": ship_type,
            "status": "success" if result.get("success") else "failed",
            "ended_at": datetime.utcnow().isoformat() + "Z",
            "message": result.get("message", ""),
            "saved_files": len(saved_files),
        })

        return {
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
            "log_file": log_path,
            "saved_files": saved_files,
            "uploads": upload_rows,
        }

    job_queue = AgentJobQueue(_run_download_job)

    @app.route("/health", methods=["GET"])
    def health():
        cfg = settings_store.get()
        ok, msg, _ = ensure_writable_folder(cfg.get("download_folder", ""))
        return jsonify({
            "success": True,
            "status": "ok",
            "agent_version": "0.1.0",
            "device_id": cfg.get("device_id", ""),
            "download_folder_ok": ok,
            "download_folder_message": msg,
            "session_health": _session_health(),
            "sync": sync_client.stats(),
        })

    @app.route("/settings", methods=["GET"])
    def get_settings():
        return jsonify({"success": True, "settings": settings_store.get()})

    @app.route("/settings", methods=["PUT"])
    def put_settings():
        payload = request.json or {}
        candidate = dict(payload)
        if "download_folder" in candidate:
            ok, msg, normalized = ensure_writable_folder(candidate.get("download_folder", ""))
            if not ok:
                return jsonify({"success": False, "message": msg}), 400
            candidate["download_folder"] = normalized
        updated = settings_store.update(candidate)
        return jsonify({"success": True, "settings": updated})

    @app.route("/settings/download-folder", methods=["PUT"])
    def put_download_folder():
        payload = request.json or {}
        folder = payload.get("download_folder", "")
        ok, msg, normalized = ensure_writable_folder(folder)
        if not ok:
            return jsonify({"success": False, "message": msg}), 400
        updated = settings_store.update({"download_folder": normalized})
        return jsonify({"success": True, "settings": updated})

    @app.route("/session/start", methods=["POST"])
    def session_start():
        payload = request.json or {}
        mobile = str(payload.get("mobile_number", "")).strip()
        if not mobile:
            return jsonify({"success": False, "message": "mobile_number is required"}), 400
        try:
            with session_lock:
                if scraper_session["scraper"]:
                    scraper_session["scraper"].quit()
                scraper = _build_scraper()
                scraper_session["scraper"] = scraper
            result = scraper.start_session(creds["Username"], creds["Password"], mobile)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/session/verify-otp", methods=["POST"])
    def session_verify_otp():
        payload = request.json or {}
        otp = str(payload.get("otp", "")).strip()
        if not otp:
            return jsonify({"success": False, "message": "otp is required"}), 400
        with session_lock:
            scraper = scraper_session["scraper"]
        if not scraper:
            return jsonify({"success": False, "message": "No active session"}), 400
        return jsonify(scraper.verify_otp(otp))

    @app.route("/session/disconnect", methods=["POST"])
    def session_disconnect():
        with session_lock:
            scraper = scraper_session["scraper"]
            scraper_session["scraper"] = None
        if scraper:
            scraper.quit()
        return jsonify({"success": True, "message": "Disconnected"})

    @app.route("/session/health", methods=["GET"])
    def session_health():
        return jsonify({"success": True, "health": _session_health()})

    @app.route("/jobs/download", methods=["POST"])
    def jobs_download():
        payload = request.json or {}
        rank = str(payload.get("rank", "")).strip()
        ship_type = str(payload.get("ship_type", "")).strip()
        if not rank or not ship_type:
            return jsonify({"success": False, "message": "rank and ship_type are required"}), 400
        job_id = job_queue.submit({
            "rank": rank,
            "ship_type": ship_type,
            "force_redownload": bool(payload.get("force_redownload", False)),
        })
        return jsonify({"success": True, "job_id": job_id, "status": "queued"})

    @app.route("/jobs/<job_id>", methods=["GET"])
    def jobs_get(job_id):
        job = job_queue.get_job(job_id)
        if not job:
            return jsonify({"success": False, "message": "Job not found"}), 404
        return jsonify({"success": True, "job": job})

    @app.route("/jobs/<job_id>/stream", methods=["GET"])
    def jobs_stream(job_id):
        def generate():
            last_seq = 0
            while True:
                events = job_queue.wait_for_events(job_id, last_seq, timeout=15)
                if not events:
                    yield ": keepalive\n\n"
                    continue
                for ev in events:
                    last_seq = ev["seq"]
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev["type"] in {"complete", "error"}:
                        return
        return Response(generate(), mimetype="text/event-stream")

    @app.route("/diagnostics", methods=["GET"])
    def diagnostics():
        return jsonify({
            "success": True,
            "session_health": _session_health(),
            "sync": sync_client.stats(),
            "settings_path": settings_store.path,
            "base_dir": settings_store.base_dir,
        })

    @app.route("/diagnostics/log-bundle", methods=["GET"])
    def diagnostics_log_bundle():
        base = settings_store.base_dir
        logs_dir = os.path.join(base, "logs")
        state_dir = os.path.join(base, "state")
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(settings_store.path):
                zf.write(settings_store.path, arcname="agent/agent.json")
            for path, arc_prefix in [(logs_dir, "agent/logs"), (state_dir, "agent/state")]:
                if not os.path.isdir(path):
                    continue
                for root, _, files in os.walk(path):
                    for name in files:
                        src = os.path.join(root, name)
                        rel = os.path.relpath(src, path)
                        zf.write(src, arcname=f"{arc_prefix}/{rel}")
        mem.seek(0)
        return send_file(
            mem,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"njordhr_agent_diagnostics_{stamp}.zip",
        )

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        sync_client.stop()
        job_queue.shutdown()
        with session_lock:
            scraper = scraper_session["scraper"]
            scraper_session["scraper"] = None
        if scraper:
            scraper.quit()
        shutdown_fn = request.environ.get("werkzeug.server.shutdown")
        if shutdown_fn:
            threading.Thread(target=lambda: (time.sleep(0.2), shutdown_fn()), daemon=True).start()
        else:
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
        return jsonify({"success": True, "message": "Agent shutting down"})

    return app
