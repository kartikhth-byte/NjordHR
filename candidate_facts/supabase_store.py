"""Supabase-backed persistence helpers for approved candidate facts rows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Mapping

import requests

from .persistence import build_candidate_resume_facts_content_hash


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class SupabaseCandidateFactsStore:
    """Small Supabase REST adapter for authoritative candidate facts rows."""

    supabase_url: str
    service_role_key: str
    timeout_seconds: int = 20
    table_name: str = "candidate_resume_facts"

    def __post_init__(self) -> None:
        self.supabase_url = str(self.supabase_url or "").strip().rstrip("/")
        self.service_role_key = str(self.service_role_key or "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.supabase_url and self.service_role_key)

    def _request(self, method: str, path: str, *, params=None, json_body=None, headers=None):
        if not self.ready:
            raise RuntimeError("Supabase candidate facts store is not configured")
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
            raise RuntimeError(f"Supabase request failed {resp.status_code}: {resp.text}")
        if not resp.text:
            return []
        try:
            return resp.json()
        except Exception:
            return []

    def _build_payload(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        facts_json = deepcopy(row.get("facts_json") or {})
        candidate_facts_hash = str(row.get("candidate_facts_hash") or "")
        if not candidate_facts_hash and isinstance(facts_json, Mapping):
            candidate_facts_hash = build_candidate_resume_facts_content_hash(facts_json)
        payload = {
            "id": str(row.get("id") or ""),
            "candidate_id": str(row.get("candidate_id") or ""),
            "candidate_resume_id": str(row.get("candidate_resume_id") or ""),
            "resume_blob_id": str(row.get("resume_blob_id") or ""),
            "schema_version": str(row.get("schema_version") or "candidate_facts.v1"),
            "parser_version": str(row.get("parser_version") or ""),
            "facts_revision": str(row.get("facts_revision") or ""),
            "candidate_facts_hash": candidate_facts_hash,
            "facts_json": facts_json,
            "extraction_status": str(row.get("extraction_status") or "failed"),
            "extraction_warnings": list(row.get("extraction_warnings") or []),
            "is_current_for_resume": bool(row.get("is_current_for_resume")),
            "created_at": str(row.get("created_at") or _utc_now_iso()),
            "updated_at": _utc_now_iso(),
        }
        return payload

    def _build_rpc_payload(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        payload = self._build_payload(row)
        return {
            "p_id": payload["id"],
            "p_candidate_id": payload["candidate_id"],
            "p_candidate_resume_id": payload["candidate_resume_id"],
            "p_resume_blob_id": payload["resume_blob_id"],
            "p_schema_version": payload["schema_version"],
            "p_parser_version": payload["parser_version"],
            "p_facts_revision": payload["facts_revision"],
            "p_candidate_facts_hash": payload["candidate_facts_hash"],
            "p_facts_json": payload["facts_json"],
            "p_extraction_status": payload["extraction_status"],
            "p_extraction_warnings": payload["extraction_warnings"],
            "p_is_current_for_resume": payload["is_current_for_resume"],
            "p_created_at": payload["created_at"],
            "p_updated_at": payload["updated_at"],
        }

    def promote_candidate_resume_facts_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Persist a current candidate facts row to Supabase."""

        if not self.ready:
            raise RuntimeError("Supabase candidate facts store is not configured")

        payload = self._build_payload(row)
        rpc_payload = self._build_rpc_payload(row)
        response = self._request(
            "POST",
            "/rest/v1/rpc/njordhr_promote_candidate_resume_facts",
            json_body=rpc_payload,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        persisted_row = response[0] if isinstance(response, list) and response else response or payload
        return {
            "success": True,
            "status": "persisted" if bool(persisted_row.get("is_current_for_resume")) else "persisted_non_current",
            "row": persisted_row,
            "committed": bool(persisted_row.get("is_current_for_resume")),
            "row_id": str(persisted_row.get("id") or ""),
            "table_name": self.table_name,
        }
