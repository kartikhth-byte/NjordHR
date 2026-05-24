import io
import json
import os
import threading
import time
import zipfile
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS

from app_settings import load_app_settings
from scraper_engine import Scraper

from .cloud_sync import CloudSyncClient
from .config_store import AgentConfigStore
from .filesystem import ensure_writable_folder
from .email_intake import OutlookEmailIntakeManager
from .outlook_auth import OutlookAuthManager
from .runtime import AgentRuntime
from .updater import AgentUpdater


def create_agent_app():
    app = Flask(__name__)
    CORS(app)

    app_settings = load_app_settings()
    creds = app_settings.credentials
    parser = app_settings.config
    settings_store = AgentConfigStore()
    sync_client = CloudSyncClient(settings_store, os.path.join(settings_store.base_dir, "state"))
    sync_client.start()
    updater = AgentUpdater(settings_store, agent_version=os.getenv("NJORDHR_AGENT_VERSION", "0.1.0"))
    outlook_auth = OutlookAuthManager(settings_store)
    email_intake = OutlookEmailIntakeManager(settings_store, outlook_auth, parser)
    session_lock = threading.RLock()
    scraper_session = {"scraper": None}

    def _session_health():
        with session_lock:
            scraper = scraper_session["scraper"]
        if not scraper:
            return {"active": False, "valid": False, "reason": "No active session"}
        return scraper.get_session_health()

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

    def _diagnostics_payload():
        cfg = settings_store.get()
        download_folder_ok, download_folder_message, normalized_download_folder = ensure_writable_folder(
            cfg.get("download_folder", "")
        )
        sync_stats = sync_client.stats()
        update_state = {}
        try:
            update_state_path = os.path.join(settings_store.base_dir, "updates", "update_state.json")
            if os.path.isfile(update_state_path):
                with open(update_state_path, "r", encoding="utf-8") as fh:
                    update_state = json.load(fh)
        except Exception:
            update_state = {}
        return {
            "success": True,
            "status": "ok",
            "agent_version": updater.agent_version,
            "device_id": cfg.get("device_id", ""),
            "download_folder_ok": download_folder_ok,
            "download_folder_message": download_folder_message,
            "download_folder": normalized_download_folder,
            "session_health": _session_health(),
            "sync": sync_stats,
            "email_intake": email_intake.health_summary(),
            "settings_path": settings_store.path,
            "base_dir": settings_store.base_dir,
            "update_state": update_state,
        }
    runtime = AgentRuntime(
        settings_store=settings_store,
        session_getter=lambda: scraper_session["scraper"],
        session_health_getter=_session_health,
        email_intake=email_intake,
        sync_client=sync_client,
    )

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(_diagnostics_payload())

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
        if "email_intake_poll_interval_seconds" in candidate:
            try:
                candidate["email_intake_poll_interval_seconds"] = int(candidate["email_intake_poll_interval_seconds"])
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "email_intake_poll_interval_seconds must be an integer"}), 400
        updated = settings_store.update(candidate)
        sync_client.signal_reconnect()
        return jsonify({"success": True, "settings": updated})

    @app.route("/settings/download-folder", methods=["PUT"])
    def put_download_folder():
        payload = request.json or {}
        folder = payload.get("download_folder", "")
        ok, msg, normalized = ensure_writable_folder(folder)
        if not ok:
            return jsonify({"success": False, "message": msg}), 400
        updated = settings_store.update({"download_folder": normalized})
        sync_client.signal_reconnect()
        return jsonify({"success": True, "settings": updated})

    @app.route("/session/start", methods=["POST"])
    def session_start():
        payload = request.json or {}
        mobile = str(payload.get("mobile_number", "")).strip()
        if not mobile:
            return jsonify({"success": False, "message": "mobile_number is required"}), 400
        scraper = None
        try:
            with session_lock:
                if scraper_session["scraper"]:
                    scraper_session["scraper"].quit()
                scraper = _build_scraper()
                scraper_session["scraper"] = scraper
            result = scraper.start_session(creds["Username"], creds["Password"], mobile)
            if not isinstance(result, dict) or not result.get("success"):
                with session_lock:
                    if scraper_session.get("scraper") is scraper:
                        scraper_session["scraper"] = None
                try:
                    scraper.quit()
                except Exception:
                    pass
            return jsonify(result)
        except Exception as exc:
            if scraper is not None:
                with session_lock:
                    if scraper_session.get("scraper") is scraper:
                        scraper_session["scraper"] = None
                try:
                    scraper.quit()
                except Exception:
                    pass
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
        job_id = runtime.submit_download_job(rank, ship_type, bool(payload.get("force_redownload", False)))
        return jsonify({"success": True, "job_id": job_id, "status": "queued"})

    @app.route("/jobs/<job_id>", methods=["GET"])
    def jobs_get(job_id):
        job = runtime.get_job(job_id)
        if not job:
            return jsonify({"success": False, "message": "Job not found"}), 404
        return jsonify({"success": True, "job": job})

    @app.route("/jobs/<job_id>/stream", methods=["GET"])
    def jobs_stream(job_id):
        def generate():
            last_seq = 0
            while True:
                events = runtime.wait_for_events(job_id, last_seq, timeout=15)
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
        return jsonify(_diagnostics_payload())

    @app.route("/email-intake/auth/status", methods=["GET"])
    def email_intake_auth_status():
        return jsonify({"success": True, "auth": outlook_auth.status()})

    @app.route("/email-intake/auth/start", methods=["POST"])
    def email_intake_auth_start():
        payload = request.json or {}
        result = outlook_auth.start_auth_flow(open_browser=bool(payload.get("open_browser", False)))
        return jsonify(result), (200 if result.get("success") else 400)

    @app.route("/email-intake/auth/disconnect", methods=["POST"])
    def email_intake_auth_disconnect():
        return jsonify(outlook_auth.disconnect())

    @app.route("/email-intake/fetch", methods=["POST"])
    def email_intake_fetch():
        status = outlook_auth.status()
        mailbox = str(status.get("mailbox", "")).strip()
        connected_account = str(status.get("connected_account", "")).strip()
        if not mailbox:
            return jsonify({"success": False, "message": "email_intake_mailbox is not configured."}), 400
        if not status.get("connected"):
            return jsonify({"success": False, "message": "Outlook mailbox is not connected."}), 400
        if mailbox.lower() != connected_account.lower():
            return jsonify({
                "success": False,
                "message": (
                    f"Connected Outlook account {connected_account or '(none)'} does not match "
                    f"configured mailbox {mailbox}. Disconnect and connect the correct mailbox."
                ),
            }), 400
        job_id = runtime.submit_email_intake_job()
        return jsonify({"success": True, "job_id": job_id, "status": "queued"})

    @app.route("/email-intake/manual-review/summary", methods=["GET"])
    def email_intake_manual_review_summary():
        try:
            return jsonify({"success": True, "summary": email_intake.manual_review_summary()})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/email-intake/manual-review/items", methods=["GET"])
    def email_intake_manual_review_items():
        try:
            return jsonify({"success": True, "items": email_intake.list_manual_review_items()})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/email-intake/manual-review/item", methods=["GET"])
    def email_intake_manual_review_item():
        item_id = str(request.args.get("id", "")).strip()
        if not item_id:
            return jsonify({"success": False, "message": "id is required"}), 400
        try:
            return jsonify({"success": True, "item": email_intake.get_manual_review_item(item_id)})
        except RuntimeError as exc:
            return jsonify({"success": False, "message": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/email-intake/manual-review/move", methods=["POST"])
    def email_intake_manual_review_move():
        payload = request.json or {}
        item_id = str(payload.get("id", "")).strip()
        selected_role = str(payload.get("selected_role", "")).strip()
        if not item_id:
            return jsonify({"success": False, "message": "id is required"}), 400
        if not selected_role:
            return jsonify({"success": False, "message": "selected_role is required"}), 400
        try:
            return jsonify(email_intake.move_manual_review_item(item_id, selected_role))
        except RuntimeError as exc:
            message = str(exc)
            if "allowlist" in message or "selected_role is required" in message:
                return jsonify({"success": False, "message": message}), 400
            return jsonify({"success": False, "message": message}), 404
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/email-intake/manual-review/open", methods=["POST"])
    def email_intake_manual_review_open():
        payload = request.json or {}
        item_id = str(payload.get("id", "")).strip()
        if not item_id:
            return jsonify({"success": False, "message": "id is required"}), 400
        try:
            return jsonify(email_intake.open_manual_review_item(item_id))
        except RuntimeError as exc:
            return jsonify({"success": False, "message": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 500

    @app.route("/diagnostics/log-bundle", methods=["GET"])
    def diagnostics_log_bundle():
        base = settings_store.base_dir
        logs_dir = os.path.join(base, "logs")
        state_dir = os.path.join(base, "state")
        updates_dir = os.path.join(base, "updates")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        manifest = _diagnostics_payload()
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(settings_store.path):
                zf.write(settings_store.path, arcname="agent/agent.json")
            if os.path.exists(settings_store.path):
                try:
                    manifest["settings_path"] = "agent/agent.json"
                except Exception:
                    pass
            zf.writestr("agent/diagnostics.json", json.dumps(manifest, indent=2, sort_keys=True))
            for path, arc_prefix in [
                (logs_dir, "agent/logs"),
                (state_dir, "agent/state"),
                (updates_dir, "agent/updates"),
            ]:
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

    @app.route("/updates/check", methods=["GET"])
    def updates_check():
        timeout = int(request.args.get("timeout", "20"))
        return jsonify(updater.check(timeout=timeout))

    @app.route("/updates/download", methods=["POST"])
    def updates_download():
        payload = request.json or {}
        artifact_url = str(payload.get("artifact_url", "")).strip()
        expected_sha = str(payload.get("expected_sha256", "")).strip().lower()
        timeout = int(payload.get("timeout", 120))
        result = updater.download(artifact_url=artifact_url, expected_sha256=expected_sha, timeout=timeout)
        code = 200 if result.get("success") else 400
        return jsonify(result), code

    @app.route("/updates/verify", methods=["POST"])
    def updates_verify():
        payload = request.json or {}
        local_path = str(payload.get("local_path", "")).strip()
        expected_sha = str(payload.get("expected_sha256", "")).strip().lower()
        result = updater.verify(local_path=local_path, expected_sha256=expected_sha)
        code = 200 if result.get("success") else 400
        return jsonify(result), code

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        runtime.shutdown()
        sync_client.stop()
        outlook_auth.shutdown()
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
