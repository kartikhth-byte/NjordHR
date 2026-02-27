import hashlib
import json
import os
import platform
import re
import tempfile
from datetime import datetime, timezone

import requests


def _version_key(value):
    text = str(value or "").strip()
    if not text:
        return ()
    parts = re.split(r"[^0-9A-Za-z]+", text)
    key = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            key.append((0, int(p)))
        else:
            key.append((1, p.lower()))
    return tuple(key)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class AgentUpdater:
    def __init__(self, settings_store, agent_version=None):
        self.settings_store = settings_store
        self.agent_version = str(agent_version or os.getenv("NJORDHR_AGENT_VERSION", "0.1.0"))
        self.update_dir = os.path.join(settings_store.base_dir, "updates")
        os.makedirs(self.update_dir, exist_ok=True)
        self.state_path = os.path.join(self.update_dir, "update_state.json")

    def _platform(self):
        sys_name = platform.system().lower()
        if "darwin" in sys_name:
            return "macos"
        if "windows" in sys_name:
            return "windows"
        return "all"

    def _manifest_url(self):
        cfg = self.settings_store.get()
        explicit = str(cfg.get("update_manifest_url", "")).strip()
        if explicit:
            return explicit
        base = str(cfg.get("api_base_url", "")).rstrip("/")
        if not base:
            return ""
        return f"{base}/updates/manifest"

    def _pick_artifact(self, artifacts, platform_name):
        scoped = [a for a in artifacts if str(a.get("platform", "all")).lower() in {platform_name, "all"}]
        if not scoped:
            return None
        if platform_name == "macos":
            preferred = [a for a in scoped if str(a.get("name", "")).endswith("NjordHR-unsigned.pkg")]
            if preferred:
                return preferred[0]
            pkgs = [a for a in scoped if str(a.get("name", "")).lower().endswith(".pkg")]
            if pkgs:
                return pkgs[0]
        if platform_name == "windows":
            exe = [a for a in scoped if str(a.get("name", "")).lower().endswith(".exe")]
            if exe:
                return exe[0]
            msi = [a for a in scoped if str(a.get("name", "")).lower().endswith(".msi")]
            if msi:
                return msi[0]
        return scoped[0]

    def check(self, timeout=20):
        manifest_url = self._manifest_url()
        if not manifest_url:
            return {"success": False, "message": "update manifest URL not configured"}
        platform_name = self._platform()
        try:
            resp = requests.get(manifest_url, params={"channel": "stable", "platform": platform_name}, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            return {"success": False, "message": f"manifest fetch failed: {exc}"}

        if not payload.get("success"):
            return {"success": False, "message": payload.get("message", "manifest response failed")}

        target_version = str(payload.get("version", "")).strip()
        artifacts = payload.get("artifacts", [])
        artifact = self._pick_artifact(artifacts, platform_name)
        if not artifact:
            return {"success": False, "message": f"no artifact found for platform={platform_name}"}

        update_available = _version_key(target_version) > _version_key(self.agent_version)
        result = {
            "success": True,
            "platform": platform_name,
            "current_version": self.agent_version,
            "target_version": target_version,
            "update_available": bool(update_available),
            "artifact": artifact,
            "manifest_url": manifest_url,
            "checked_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        self._save_state({"last_check": result})
        return result

    def download(self, artifact_url, expected_sha256="", timeout=120):
        if not artifact_url:
            return {"success": False, "message": "artifact_url is required"}
        expected_sha256 = str(expected_sha256 or "").strip().lower()

        name = os.path.basename(artifact_url.split("?", 1)[0]) or "update.bin"
        fd, tmp_path = tempfile.mkstemp(prefix="njordhr_update_", suffix=f"_{name}", dir=self.update_dir)
        os.close(fd)

        try:
            with requests.get(artifact_url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)
        except Exception as exc:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return {"success": False, "message": f"download failed: {exc}"}

        actual_sha = _sha256_file(tmp_path).lower()
        checksum_ok = (not expected_sha256) or (actual_sha == expected_sha256)
        state = {
            "downloaded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "artifact_url": artifact_url,
            "artifact_name": name,
            "local_path": tmp_path,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha,
            "checksum_ok": checksum_ok,
        }
        self._save_state({"last_download": state})
        return {"success": True, **state}

    def verify(self, local_path="", expected_sha256=""):
        path = str(local_path or "").strip()
        expected_sha256 = str(expected_sha256 or "").strip().lower()
        if not path:
            state = self._load_state()
            path = str(((state or {}).get("last_download") or {}).get("local_path", "")).strip()
            if not path:
                return {"success": False, "message": "no downloaded artifact found; call download first"}
            if not expected_sha256:
                expected_sha256 = str(((state or {}).get("last_download") or {}).get("expected_sha256", "")).strip().lower()

        if not os.path.isfile(path):
            return {"success": False, "message": "artifact file not found"}

        actual_sha = _sha256_file(path).lower()
        checksum_ok = (not expected_sha256) or (actual_sha == expected_sha256)
        result = {
            "success": True,
            "local_path": path,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha,
            "checksum_ok": checksum_ok,
            "signature_verified": False,
            "signature_message": "signature verification not implemented yet",
        }
        self._save_state({"last_verify": result})
        return result

    def _load_state(self):
        if not os.path.isfile(self.state_path):
            return {}
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_state(self, patch):
        state = self._load_state()
        state.update(patch or {})
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
