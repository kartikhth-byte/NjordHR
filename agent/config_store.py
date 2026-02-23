import json
import os
import platform
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
    "log_level": "INFO",
}


def _default_download_folder():
    return os.path.abspath(os.path.expanduser("~/Downloads/NjordHR"))


def agent_config_path():
    override = os.getenv("NJORDHR_AGENT_CONFIG_PATH", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    home = os.path.expanduser("~")
    system = platform.system().lower()
    if system == "darwin":
        base = os.path.join(home, "Library", "Application Support", "NjordHR")
    elif system == "windows":
        appdata = os.getenv("APPDATA", home)
        base = os.path.join(appdata, "NjordHR")
    else:
        base = os.path.join(home, ".config", "njordhr")
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
            self.save()

    def _merge_defaults(self, raw):
        cfg = deepcopy(DEFAULTS)
        cfg.update({k: v for k, v in (raw or {}).items() if k in cfg})
        if not str(cfg.get("device_id", "")).strip():
            cfg["device_id"] = str(uuid.uuid4())
        if not str(cfg.get("download_folder", "")).strip():
            cfg["download_folder"] = _default_download_folder()
        cfg["download_folder"] = os.path.abspath(os.path.expanduser(cfg["download_folder"]))
        return cfg

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

