import json
import os
import platform
import shutil
import threading
import uuid
from copy import deepcopy


DEFAULTS = {
    "device_id": "",
    "api_base_url": "",
    "device_token": "",
    "download_folder": "",
    "auto_start": False,
    "cloud_sync_enabled": True,
    "cloud_upload_resumes": False,
    "update_manifest_url": "",
    "log_level": "INFO",
    "email_intake_enabled": False,
    "email_intake_mailbox": "",
    "email_intake_monitored_folder": "Inbox/NjordHR Resumes",
    "email_intake_processed_folder": "Inbox/NjordHR Processed",
    "email_intake_failed_folder": "Inbox/NjordHR Failed",
    "email_intake_poll_interval_seconds": 60,
    "outlook_client_id": "",
    "outlook_tenant_id": "organizations",
    "outlook_connected_account": "",
    "outlook_last_auth_error": "",
}


def _agent_data_dir(home=None, system=None):
    home_dir = os.path.abspath(os.path.expanduser(home or "~"))
    system_name = (system or platform.system()).lower()
    if system_name == "darwin":
        return os.path.join(home_dir, "Library", "Application Support", "NjordHR")
    if system_name == "windows":
        appdata = os.getenv("APPDATA", home_dir)
        return os.path.join(appdata, "NjordHR")
    return os.path.join(home_dir, ".config", "njordhr")


def _legacy_download_folder(home=None):
    home_dir = os.path.abspath(os.path.expanduser(home or "~"))
    return os.path.join(home_dir, "Downloads", "NjordHR")


def _default_download_folder(base_dir=None):
    target_base = os.path.abspath(os.path.expanduser(base_dir)) if base_dir else _agent_data_dir()
    return os.path.join(target_base, "Resumes")


def agent_config_path():
    override = os.getenv("NJORDHR_AGENT_CONFIG_PATH", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    base = _agent_data_dir()
    return os.path.join(base, "agent.json")


class AgentConfigStore:
    def __init__(self, path=None):
        self.path = path or agent_config_path()
        self._lock = threading.RLock()
        self._config = {}
        self._ensure_loaded()

    @property
    def base_dir(self):
        return os.path.dirname(self.path)

    def _ensure_loaded(self):
        with self._lock:
            os.makedirs(self.base_dir, exist_ok=True)
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
            else:
                raw = {}
            self._config = self._merge_defaults(raw)
            self._config = self._apply_download_folder_migration(self._config, raw)
            self.save()

    def _merge_defaults(self, raw):
        cfg = deepcopy(DEFAULTS)
        cfg.update({k: v for k, v in (raw or {}).items() if k in cfg})
        if not str(cfg.get("device_id", "")).strip():
            cfg["device_id"] = str(uuid.uuid4())
        if not str(cfg.get("download_folder", "")).strip():
            cfg["download_folder"] = _default_download_folder(self.base_dir)
        cfg["download_folder"] = os.path.abspath(os.path.expanduser(cfg["download_folder"]))
        cfg["email_intake_mailbox"] = str(cfg.get("email_intake_mailbox", "")).strip()
        cfg["email_intake_monitored_folder"] = str(
            cfg.get("email_intake_monitored_folder", DEFAULTS["email_intake_monitored_folder"])
        ).strip() or DEFAULTS["email_intake_monitored_folder"]
        cfg["email_intake_processed_folder"] = str(
            cfg.get("email_intake_processed_folder", DEFAULTS["email_intake_processed_folder"])
        ).strip() or DEFAULTS["email_intake_processed_folder"]
        cfg["email_intake_failed_folder"] = str(
            cfg.get("email_intake_failed_folder", DEFAULTS["email_intake_failed_folder"])
        ).strip() or DEFAULTS["email_intake_failed_folder"]
        cfg["outlook_client_id"] = str(cfg.get("outlook_client_id", "")).strip()
        cfg["outlook_tenant_id"] = str(
            cfg.get("outlook_tenant_id", DEFAULTS["outlook_tenant_id"])
        ).strip() or DEFAULTS["outlook_tenant_id"]
        cfg["outlook_connected_account"] = str(cfg.get("outlook_connected_account", "")).strip()
        cfg["outlook_last_auth_error"] = str(cfg.get("outlook_last_auth_error", "")).strip()
        try:
            poll_interval = int(cfg.get("email_intake_poll_interval_seconds", DEFAULTS["email_intake_poll_interval_seconds"]))
        except (TypeError, ValueError):
            poll_interval = DEFAULTS["email_intake_poll_interval_seconds"]
        cfg["email_intake_poll_interval_seconds"] = max(15, min(poll_interval, 3600))
        return cfg

    def _apply_download_folder_migration(self, cfg, raw):
        migrated = deepcopy(cfg)
        current_folder = os.path.abspath(os.path.expanduser(str(migrated.get("download_folder", "")).strip()))
        target_folder = os.path.abspath(os.path.expanduser(_default_download_folder(self.base_dir)))
        legacy_folder = os.path.abspath(os.path.expanduser(_legacy_download_folder()))
        raw_download_folder = os.path.abspath(os.path.expanduser(str((raw or {}).get("download_folder", "")).strip()))

        should_migrate = current_folder == legacy_folder or raw_download_folder == legacy_folder
        if not should_migrate:
            return migrated

        try:
            migrated["download_folder"] = self._migrate_download_folder(legacy_folder, target_folder)
        except Exception:
            migrated["download_folder"] = current_folder
        return migrated

    def _migrate_download_folder(self, source_folder, target_folder):
        source_path = os.path.abspath(os.path.expanduser(source_folder))
        target_path = os.path.abspath(os.path.expanduser(target_folder))
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        if source_path == target_path:
            os.makedirs(target_path, exist_ok=True)
            return target_path

        if not os.path.exists(source_path):
            os.makedirs(target_path, exist_ok=True)
            return target_path

        if not os.path.exists(target_path):
            shutil.move(source_path, target_path)
            return target_path

        self._merge_directory_contents(source_path, target_path)
        try:
            if os.path.isdir(source_path) and not os.listdir(source_path):
                os.rmdir(source_path)
        except OSError:
            pass
        return target_path

    def _merge_directory_contents(self, source_folder, target_folder):
        os.makedirs(target_folder, exist_ok=True)
        for child_name in os.listdir(source_folder):
            source_path = os.path.join(source_folder, child_name)
            target_path = self._next_available_path(os.path.join(target_folder, child_name))
            shutil.move(source_path, target_path)

    def _next_available_path(self, path):
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        if os.path.isdir(path):
            root, ext = path, ""
        counter = 1
        while True:
            candidate = f"{root}_{counter}{ext}"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def get(self):
        with self._lock:
            return deepcopy(self._config)

    def save(self):
        with self._lock:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._config, fh, indent=2)

    def update(self, patch):
        with self._lock:
            for key, value in (patch or {}).items():
                if key in DEFAULTS:
                    self._config[key] = value
            self._config = self._merge_defaults(self._config)
            self.save()
            return deepcopy(self._config)
