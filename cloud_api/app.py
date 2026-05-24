"""Minimal cloud API scaffold for NjordHR M1."""

from __future__ import annotations

import os

from flask import Flask, jsonify, request

from .runtime import CloudApiSettings, cloud_api_settings_payload, load_cloud_api_settings


def _env_value(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _require_bearer_token(settings: CloudApiSettings):
    token = _env_value("NJORDHR_API_TOKEN") or _env_value("NJORDHR_ADMIN_TOKEN")
    if not token:
        return None
    header = str(request.headers.get("Authorization", "") or "")
    if header == f"Bearer {token}":
        return None
    return jsonify({"success": False, "message": "Unauthorized"}), 401


def create_app() -> Flask:
    app = Flask(__name__)
    settings = load_cloud_api_settings()
    app.config["NJORDHR_CLOUD_API_SETTINGS"] = settings

    @app.before_request
    def _auth_guard():
        if request.endpoint in {"health", "runtime_ready"}:
            return None
        return _require_bearer_token(settings)

    @app.route("/health", methods=["GET"])
    def health():
        payload = cloud_api_settings_payload(settings)
        payload.update({"status": "ok"})
        return jsonify(payload)

    @app.route("/runtime/ready", methods=["GET"])
    def runtime_ready():
        payload = cloud_api_settings_payload(settings)
        payload["status"] = "ready" if settings.ready else "not_ready"
        return jsonify(payload), 200 if settings.ready else 503

    @app.route("/v1/ping", methods=["GET"])
    def ping():
        return jsonify({"success": True, "message": "pong", "service": settings.service_name})

    return app
