import os
import threading
from datetime import UTC, datetime

import requests

from repositories.feedback_repo import FeedbackRepo
from runtime_env import config_value, normalize_env_value, normalized_url


def resolve_supabase_api_key():
    """Prefer the modern secret key, fall back to the legacy service role key."""
    return (
        config_value("Credentials", "Supabase_Secret_Key", "")
        or config_value("Credentials", "Supabase_Service_Role_Key", "")
        or normalize_env_value(os.getenv("SUPABASE_SECRET_KEY", ""))
        or normalize_env_value(os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    )


class SupabaseFeedbackStore(FeedbackRepo):
    """Supabase-backed replacement for the local feedback store."""

    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self, supabase_url=None, service_role_key=None, timeout_seconds=30):
        self.supabase_url = normalized_url(
            supabase_url
            or config_value("Advanced", "supabase_url", "")
            or os.getenv("SUPABASE_URL", "")
        )
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

    def add_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence,
                     user_decision, user_notes=""):
        body = [{
            "filename": filename,
            "query": query,
            "llm_decision": llm_decision,
            "llm_reason": llm_reason,
            "llm_confidence": llm_confidence,
            "user_decision": user_decision,
            "user_notes": user_notes or "",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }]
        with self.lock:
            self._request("POST", "/rest/v1/ai_feedback", json_body=body, timeout=45)
        print(f"[FEEDBACK] Stored (cloud): {filename} - User: {user_decision}, LLM: {llm_decision}")

    def get_recent_feedback(self, query, limit=5):
        query_terms = (query or "").split()
        if not query_terms:
            return []
        like_term = f"*{query_terms[0]}*"
        with self.lock:
            rows = self._request(
                "GET",
                "/rest/v1/ai_feedback",
                params={
                    "select": "filename,llm_decision,user_decision,llm_reason,user_notes",
                    "query": f"ilike.{like_term}",
                    "order": "timestamp.desc",
                    "limit": int(limit),
                },
            )
        return [
            (
                row.get("filename", ""),
                row.get("llm_decision", ""),
                row.get("user_decision", ""),
                row.get("llm_reason", ""),
                row.get("user_notes", ""),
            )
            for row in (rows or [])
        ]
