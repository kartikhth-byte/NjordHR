"""Supabase telemetry sink for recruiter-mode logs and prompt audits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

import requests


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class SupabaseTelemetryStore:
    """Tiny Supabase REST adapter for app-level telemetry events."""

    supabase_url: str
    service_role_key: str
    timeout_seconds: int = 20
    table_name: str = "njordhr_telemetry_logs"

    def __post_init__(self) -> None:
        self.supabase_url = str(self.supabase_url or "").strip().rstrip("/")
        self.service_role_key = str(self.service_role_key or "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.supabase_url and self.service_role_key)

    def _request(self, method: str, path: str, *, json_body=None, params=None, headers=None):
        if not self.ready:
            raise RuntimeError("Supabase telemetry store is not configured")
        req_headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }
        if headers:
            req_headers.update(headers)
        resp = requests.request(
            method=method,
            url=f"{self.supabase_url}{path}",
            headers=req_headers,
            params=params,
            json=json_body,
            timeout=self.timeout_seconds,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Supabase telemetry request failed {resp.status_code}: {resp.text}")
        if not resp.text:
            return []
        try:
            return resp.json()
        except Exception:
            return []

    def build_payload(
        self,
        *,
        telemetry_kind: str,
        category: str,
        status: str,
        summary: str,
        payload: Mapping[str, Any],
        prompt_hash: str | None = None,
        prompt_text: str | None = None,
        actor_role: str | None = None,
        actor_username: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        return {
            "telemetry_kind": str(telemetry_kind or ""),
            "category": str(category or ""),
            "status": str(status or ""),
            "summary": str(summary or ""),
            "prompt_hash": str(prompt_hash or ""),
            "prompt_text": str(prompt_text or ""),
            "actor_role": str(actor_role or ""),
            "actor_username": str(actor_username or ""),
            "session_id": str(session_id or ""),
            "source": str(source or "backend"),
            "payload": dict(payload or {}),
            "created_at": _utc_now_iso(),
        }

    def log_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError("Supabase telemetry store is not configured")
        body = [dict(payload)]
        self._request(
            "POST",
            f"/rest/v1/{self.table_name}",
            json_body=body,
            headers={"Prefer": "return=minimal"},
        )
        return {"success": True, "table_name": self.table_name, "row": dict(payload)}

    def list_events(
        self,
        *,
        limit: int = 200,
        telemetry_kind: str | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.ready:
            return []
        params: dict[str, Any] = {
            "select": "id,telemetry_kind,category,status,summary,prompt_hash,prompt_text,actor_role,actor_username,session_id,source,payload,created_at",
            "order": "created_at.desc",
            "limit": max(1, min(int(limit or 200), 1000)),
        }
        if telemetry_kind:
            params["telemetry_kind"] = f"eq.{telemetry_kind}"
        if category:
            params["category"] = f"eq.{category}"
        if status:
            params["status"] = f"eq.{status}"
        rows = self._request("GET", f"/rest/v1/{self.table_name}", params=params)
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def list_prompt_audit_summaries(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not self.ready:
            return []
        params: dict[str, Any] = {
            "select": "prompt_hash,total_count,issue_count,ok_count,disabled_count,first_seen_at,last_seen_at",
            "order": "last_seen_at.desc",
            "limit": max(1, min(int(limit or 200), 1000)),
        }
        if offset:
            params["offset"] = max(0, int(offset))
        rows = self._request(
            "GET",
            "/rest/v1/njordhr_telemetry_prompt_audit_summary",
            params=params,
        )
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def get_prompt_audit_totals(self) -> dict[str, Any]:
        if not self.ready:
            return {
                "total_count": 0,
                "issue_count": 0,
                "ok_count": 0,
                "disabled_count": 0,
                "prompt_hash_count": 0,
            }
        rows = self._request(
            "GET",
            "/rest/v1/njordhr_telemetry_prompt_audit_totals",
            params={"select": "total_count,issue_count,ok_count,disabled_count,prompt_hash_count", "limit": 1},
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            return dict(rows[0])
        if isinstance(rows, Mapping):
            return dict(rows)
        return {
            "total_count": 0,
            "issue_count": 0,
            "ok_count": 0,
            "disabled_count": 0,
            "prompt_hash_count": 0,
        }
