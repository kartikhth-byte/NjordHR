import hashlib
import os
import threading
from datetime import UTC, datetime

import requests

from repositories.registry_repo import RegistryRepo
from runtime_env import normalize_env_value, normalized_url


def resolve_supabase_api_key():
    """Prefer the modern secret key, fall back to the legacy service role key."""
    return (
        normalize_env_value(os.getenv("SUPABASE_SECRET_KEY", ""))
        or normalize_env_value(os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    )


def canonical_file_key(file_path):
    raw = normalize_env_value(str(file_path or ""))
    if not raw:
        return ""
    return os.path.normpath(os.path.abspath(os.path.expanduser(raw)))


class SupabaseFileRegistry(RegistryRepo):
    """Supabase-backed replacement for the local file registry."""

    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self, supabase_url=None, service_role_key=None, timeout_seconds=30):
        self.supabase_url = normalized_url(supabase_url or os.getenv("SUPABASE_URL", ""))
        self.api_key = normalize_env_value(service_role_key or resolve_supabase_api_key())
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.lock = threading.Lock()

    def _request(self, method, path, params=None, json_body=None, timeout=None, headers=None):
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT_SECONDS
        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)
        resp = requests.request(
            method=method,
            url=f"{self.supabase_url}{path}",
            params=params or {},
            json=json_body,
            headers=req_headers,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Supabase AI store request failed ({resp.status_code}): {resp.text}")
        if not resp.text:
            return []
        try:
            return resp.json()
        except Exception:
            return []

    @staticmethod
    def _file_key(file_path):
        return canonical_file_key(file_path)

    def generate_resume_id(self, file_path):
        file_key = self._file_key(file_path)
        return hashlib.sha1(file_key.encode()).hexdigest()

    def needs_processing(self, file_path, last_modified):
        file_key = self._file_key(file_path)
        with self.lock:
            rows = self._request(
                "GET",
                "/rest/v1/ai_file_registry",
                params={"select": "last_modified", "file_key": f"eq.{file_key}", "limit": 1},
            )
        if not rows:
            return True
        stored = float(rows[0].get("last_modified", 0) or 0)
        return stored < float(last_modified)

    def upsert_file_record(self, file_path, last_modified, resume_id):
        file_key = self._file_key(file_path)
        body = [{
            "file_key": file_key,
            "last_modified": float(last_modified),
            "resume_id": str(resume_id),
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }]
        with self.lock:
            self._request(
                "POST",
                "/rest/v1/ai_file_registry",
                params={"on_conflict": "file_key"},
                json_body=body,
                timeout=45,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )

    def get_resume_id(self, file_path):
        file_key = self._file_key(file_path)
        with self.lock:
            rows = self._request(
                "GET",
                "/rest/v1/ai_file_registry",
                params={"select": "resume_id", "file_key": f"eq.{file_key}", "limit": 1},
            )
        if rows:
            resume_id = str(rows[0].get("resume_id", "")).strip()
            if resume_id:
                return resume_id
        return self.generate_resume_id(file_path)
