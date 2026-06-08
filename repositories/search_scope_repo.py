from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence


class SearchScopeRepositoryError(RuntimeError):
    """Raised when authoritative search-scope persistence is unavailable."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def prompt_hash(prompt: Any) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8", "ignore")).hexdigest()


class SQLiteSearchScopeRepository:
    """Local authoritative scope store for AI Search refinement sessions."""

    DB_VERSION = 3
    MEMBERSHIP_RETENTION_DAYS = 30
    LINEAGE_RETENTION_DAYS = 365
    ACKNOWLEDGEMENT_TTL_MINUTES = 5
    RECOVERY_DRAFT_TTL_HOURS = 24
    RECOVERY_DRAFT_LIMIT = 3
    REQUEST_CLAIM_RETENTION_DAYS = 7

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._migrate_schema()
        self.cleanup_expired(limit=1000)

    def _get_db_version(self) -> int:
        with self.lock:
            try:
                result = self.conn.execute("SELECT version FROM schema_version").fetchone()
                return int(result[0]) if result else 0
            except sqlite3.OperationalError:
                return 0

    def _set_db_version(self, version: int) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version VALUES (?)", (int(version),))
            self.conn.commit()

    def _migrate_schema(self) -> None:
        current_version = self._get_db_version()
        if current_version < self.DB_VERSION:
            self._create_tables()
            self._set_db_version(self.DB_VERSION)

    def _create_tables(self) -> None:
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS search_session_lineage (
                    search_session_id TEXT PRIMARY KEY,
                    root_search_session_id TEXT NOT NULL,
                    parent_search_session_id TEXT,
                    search_mode TEXT NOT NULL,
                    refinement_depth INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    actor_username TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    rank_folder TEXT NOT NULL,
                    applied_ship_type TEXT NOT NULL DEFAULT '',
                    experienced_ship_type TEXT NOT NULL DEFAULT '',
                    prompt_hash TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    input_scope_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    membership_expires_at TEXT NOT NULL,
                    lineage_expires_at TEXT NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS search_scope_membership (
                    search_session_id TEXT NOT NULL,
                    candidate_scope_id TEXT NOT NULL,
                    content_hash_at_event TEXT NOT NULL DEFAULT '',
                    result_bucket TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    resume_id TEXT NOT NULL DEFAULT '',
                    decision_evidence_json TEXT NOT NULL DEFAULT '{}',
                    lineage_warning_codes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (search_session_id, candidate_scope_id),
                    FOREIGN KEY (search_session_id)
                        REFERENCES search_session_lineage(search_session_id)
                        ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scope_lineage_actor ON search_session_lineage(actor_user_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scope_lineage_parent ON search_session_lineage(parent_search_session_id)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scope_membership_candidate ON search_scope_membership(candidate_scope_id)"
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS search_changed_content_acknowledgement (
                    acknowledgement_id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    parent_search_session_id TEXT NOT NULL,
                    search_request_id TEXT NOT NULL,
                    changed_content_set_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scope_ack_binding "
                "ON search_changed_content_acknowledgement("
                "actor_user_id, parent_search_session_id, search_request_id)"
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_search_recovery_draft (
                    actor_user_id TEXT NOT NULL,
                    tab_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    PRIMARY KEY (actor_user_id, tab_id)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_search_recovery_actor_saved "
                "ON ai_search_recovery_draft(actor_user_id, saved_at DESC)"
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_search_request_claim (
                    search_request_id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    search_session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    failed_at TEXT,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_search_request_actor "
                "ON ai_search_request_claim(actor_user_id, created_at DESC)"
            )
            self.conn.commit()

    @staticmethod
    def _request_claim_response(
        row: Mapping[str, Any],
        *,
        actor_user_id: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        data = dict(row)
        search_request_id = str(data.get("search_request_id") or "").strip()
        search_session_id = str(data.get("search_session_id") or "").strip()
        if (
            str(data.get("actor_user_id") or "").strip() != actor_user_id
            or str(data.get("request_fingerprint") or "").strip() != request_fingerprint
        ):
            return {
                "claimed": False,
                "request_status": "SEARCH_REQUEST_ID_CONFLICT",
                "error_code": "SEARCH_REQUEST_ID_CONFLICT",
                "retryable": False,
                "search_request_id": search_request_id,
                "message": "This AI Search request identifier was already used. Start a new search to continue.",
            }

        status = str(data.get("status") or "started").strip().lower()
        if status == "complete":
            try:
                summary = json.loads(data.get("summary_json") or "{}")
            except Exception:
                summary = {}
            return {
                "claimed": False,
                "request_status": "SEARCH_REQUEST_ALREADY_COMPLETE",
                "retryable": False,
                "search_request_id": search_request_id,
                "search_session_id": search_session_id,
                "summary": summary,
                "message": "This AI Search request already completed. Start a new search to run again.",
            }
        if status == "failed":
            return {
                "claimed": False,
                "request_status": "SEARCH_REQUEST_ALREADY_FAILED",
                "retryable": False,
                "search_request_id": search_request_id,
                "search_session_id": search_session_id,
                "error_code": str(data.get("error_code") or "AI_SEARCH_REQUEST_FAILED"),
                "message": str(data.get("error_message") or "This AI Search request already failed. Start a new search to retry."),
            }
        return {
            "claimed": False,
            "request_status": "SEARCH_REQUEST_IN_PROGRESS",
            "retryable": True,
            "retry_after_seconds": 5,
            "search_request_id": search_request_id,
            "search_session_id": search_session_id,
            "message": "This AI Search request is already running. Start a new search to retry.",
        }

    def claim_search_request(
        self,
        *,
        search_request_id: str,
        actor_user_id: str,
        request_fingerprint: str,
        search_session_id: str,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        search_request_id = str(search_request_id or "").strip()
        actor_user_id = str(actor_user_id or "").strip()
        request_fingerprint = str(request_fingerprint or "").strip()
        search_session_id = str(search_session_id or "").strip()
        if not search_request_id:
            raise SearchScopeRepositoryError("search_request_id is required")
        if not actor_user_id:
            raise SearchScopeRepositoryError("actor_user_id is required")
        if not request_fingerprint:
            raise SearchScopeRepositoryError("request_fingerprint is required")
        if not search_session_id:
            raise SearchScopeRepositoryError("search_session_id is required")

        now = _iso(_utc_now())
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                existing = self.conn.execute(
                    "SELECT * FROM ai_search_request_claim WHERE search_request_id=?",
                    (search_request_id,),
                ).fetchone()
                if existing:
                    self.conn.commit()
                    return self._request_claim_response(
                        existing,
                        actor_user_id=actor_user_id,
                        request_fingerprint=request_fingerprint,
                    )
                self.conn.execute(
                    """
                    INSERT INTO ai_search_request_claim (
                        search_request_id, actor_user_id, request_fingerprint,
                        request_json, search_session_id, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'started', ?, ?)
                    """,
                    (
                        search_request_id,
                        actor_user_id,
                        request_fingerprint,
                        _json_dumps(dict(request or {})),
                        search_session_id,
                        now,
                        now,
                    ),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return {
            "claimed": True,
            "request_status": "started",
            "search_request_id": search_request_id,
            "search_session_id": search_session_id,
        }

    def complete_search_request(
        self,
        *,
        search_request_id: str,
        actor_user_id: str,
        request_fingerprint: str,
        summary: Mapping[str, Any] | None = None,
    ) -> bool:
        search_request_id = str(search_request_id or "").strip()
        actor_user_id = str(actor_user_id or "").strip()
        request_fingerprint = str(request_fingerprint or "").strip()
        if not search_request_id or not actor_user_id or not request_fingerprint:
            return False
        now = _iso(_utc_now())
        with self.lock:
            cursor = self.conn.execute(
                """
                UPDATE ai_search_request_claim
                SET status='complete', updated_at=?, completed_at=?,
                    error_code='', error_message='', summary_json=?
                WHERE search_request_id=?
                  AND actor_user_id=?
                  AND request_fingerprint=?
                  AND status='started'
                """,
                (
                    now,
                    now,
                    _json_dumps(dict(summary or {})),
                    search_request_id,
                    actor_user_id,
                    request_fingerprint,
                ),
            )
            self.conn.commit()
        return cursor.rowcount == 1

    def fail_search_request(
        self,
        *,
        search_request_id: str,
        actor_user_id: str,
        request_fingerprint: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        search_request_id = str(search_request_id or "").strip()
        actor_user_id = str(actor_user_id or "").strip()
        request_fingerprint = str(request_fingerprint or "").strip()
        if not search_request_id or not actor_user_id or not request_fingerprint:
            return False
        now = _iso(_utc_now())
        with self.lock:
            cursor = self.conn.execute(
                """
                UPDATE ai_search_request_claim
                SET status='failed', updated_at=?, failed_at=?,
                    error_code=?, error_message=?
                WHERE search_request_id=?
                  AND actor_user_id=?
                  AND request_fingerprint=?
                  AND status='started'
                """,
                (
                    now,
                    now,
                    str(error_code or "AI_SEARCH_REQUEST_FAILED"),
                    str(error_message or "AI Search request failed."),
                    search_request_id,
                    actor_user_id,
                    request_fingerprint,
                ),
            )
            self.conn.commit()
        return cursor.rowcount == 1

    def complete_search_session(
        self,
        *,
        search_session_id: str,
        actor_user_id: str,
        actor_username: str,
        actor_role: str,
        rank_folder: str,
        applied_ship_type: str = "",
        experienced_ship_type: str = "",
        prompt: str = "",
        search_mode: str = "root",
        root_search_session_id: str | None = None,
        parent_search_session_id: str | None = None,
        refinement_depth: int = 0,
        context: Mapping[str, Any] | None = None,
        input_scope: Mapping[str, Any] | None = None,
        output: Mapping[str, Any] | None = None,
        memberships: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        search_session_id = str(search_session_id or "").strip()
        actor_user_id = str(actor_user_id or "").strip()
        if not search_session_id:
            raise SearchScopeRepositoryError("search_session_id is required")
        if not actor_user_id:
            raise SearchScopeRepositoryError("actor_user_id is required")

        now = _utc_now()
        membership_expires_at = now + timedelta(days=self.MEMBERSHIP_RETENTION_DAYS)
        lineage_expires_at = now + timedelta(days=self.LINEAGE_RETENTION_DAYS)
        root_id = str(root_search_session_id or search_session_id).strip()
        parent_id = str(parent_search_session_id or "").strip() or None
        member_rows = [dict(member) for member in memberships or []]

        with self.lock:
            try:
                self.conn.execute("BEGIN")
                self.conn.execute(
                    """
                    INSERT INTO search_session_lineage (
                        search_session_id, root_search_session_id, parent_search_session_id,
                        search_mode, refinement_depth, status, actor_user_id,
                        actor_username, actor_role, rank_folder, applied_ship_type,
                        experienced_ship_type, prompt_hash, context_json,
                        input_scope_json, output_json, created_at, completed_at,
                        membership_expires_at, lineage_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(search_session_id) DO UPDATE SET
                        root_search_session_id=excluded.root_search_session_id,
                        parent_search_session_id=excluded.parent_search_session_id,
                        search_mode=excluded.search_mode,
                        refinement_depth=excluded.refinement_depth,
                        status=excluded.status,
                        actor_user_id=excluded.actor_user_id,
                        actor_username=excluded.actor_username,
                        actor_role=excluded.actor_role,
                        rank_folder=excluded.rank_folder,
                        applied_ship_type=excluded.applied_ship_type,
                        experienced_ship_type=excluded.experienced_ship_type,
                        prompt_hash=excluded.prompt_hash,
                        context_json=excluded.context_json,
                        input_scope_json=excluded.input_scope_json,
                        output_json=excluded.output_json,
                        completed_at=excluded.completed_at,
                        membership_expires_at=excluded.membership_expires_at,
                        lineage_expires_at=excluded.lineage_expires_at
                    """,
                    (
                        search_session_id,
                        root_id,
                        parent_id,
                        str(search_mode or "root"),
                        int(refinement_depth or 0),
                        "complete",
                        actor_user_id,
                        str(actor_username or ""),
                        str(actor_role or ""),
                        str(rank_folder or ""),
                        str(applied_ship_type or ""),
                        str(experienced_ship_type or ""),
                        prompt_hash(prompt),
                        _json_dumps(dict(context or {})),
                        _json_dumps(dict(input_scope or {})),
                        _json_dumps(dict(output or {})),
                        _iso(now),
                        _iso(now),
                        _iso(membership_expires_at),
                        _iso(lineage_expires_at),
                    ),
                )
                self.conn.execute(
                    "DELETE FROM search_scope_membership WHERE search_session_id=?",
                    (search_session_id,),
                )
                for member in member_rows:
                    candidate_scope_id = str(member.get("candidate_scope_id") or "").strip()
                    if not candidate_scope_id:
                        raise SearchScopeRepositoryError("candidate_scope_id is required for every scope member")
                    decision_evidence = {
                        "schema_version": "verified_scope_decision.v1",
                        "decision_mode": str(member.get("decision_mode") or "unknown"),
                        "facts_version": str(member.get("facts_version") or ""),
                        "reason_codes": list(member.get("reason_codes") or []),
                        "decision_summary": "Verified match emitted by AI Search.",
                    }
                    self.conn.execute(
                        """
                        INSERT INTO search_scope_membership (
                            search_session_id, candidate_scope_id, content_hash_at_event,
                            result_bucket, filename, resume_id, decision_evidence_json,
                            lineage_warning_codes_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            search_session_id,
                            candidate_scope_id,
                            str(member.get("content_hash_at_event") or ""),
                            str(member.get("result_bucket") or "verified_match"),
                            str(member.get("filename") or ""),
                            str(member.get("resume_id") or ""),
                            _json_dumps(decision_evidence),
                            _json_dumps(list(member.get("lineage_warning_codes") or [])),
                            _iso(now),
                        ),
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

        return {
            "search_session_id": search_session_id,
            "candidate_scope_member_count": len(member_rows),
            "membership_expires_at": _iso(membership_expires_at),
        }

    def get_session(self, search_session_id: str, *, actor_user_id: str | None = None) -> dict[str, Any] | None:
        search_session_id = str(search_session_id or "").strip()
        if not search_session_id:
            return None
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM search_session_lineage WHERE search_session_id=?",
                (search_session_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if actor_user_id is not None and data.get("actor_user_id") != str(actor_user_id or "").strip():
            return None
        for key in ("context_json", "input_scope_json", "output_json"):
            try:
                data[key[:-5]] = json.loads(data.get(key) or "{}")
            except Exception:
                data[key[:-5]] = {}
        return data

    def get_scope_memberships(
        self,
        search_session_id: str,
        *,
        actor_user_id: str,
    ) -> list[dict[str, Any]]:
        session = self.get_session(search_session_id, actor_user_id=actor_user_id)
        if not session:
            return []
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT candidate_scope_id, content_hash_at_event, result_bucket,
                       filename, resume_id, decision_evidence_json,
                       lineage_warning_codes_json, created_at
                FROM search_scope_membership
                WHERE search_session_id=?
                ORDER BY created_at ASC, candidate_scope_id ASC
                """,
                (str(search_session_id or "").strip(),),
            ).fetchall()
        memberships = []
        for row in rows:
            member = dict(row)
            try:
                member["decision_evidence"] = json.loads(member.get("decision_evidence_json") or "{}")
            except Exception:
                member["decision_evidence"] = {}
            try:
                member["lineage_warning_codes"] = json.loads(
                    member.get("lineage_warning_codes_json") or "[]"
                )
            except Exception:
                member["lineage_warning_codes"] = []
            memberships.append(member)
        return memberships

    def get_refinement_parent_scope(self, search_session_id: str, *, actor_user_id: str) -> dict[str, Any]:
        preflight = self.preflight_parent_scope(search_session_id, actor_user_id=actor_user_id)
        if not preflight.get("success"):
            return preflight
        session = self.get_session(search_session_id, actor_user_id=actor_user_id)
        memberships = self.get_scope_memberships(search_session_id, actor_user_id=actor_user_id)
        if not session or not memberships:
            return {
                "success": False,
                "available": False,
                "error_code": "REFINEMENT_SCOPE_EMPTY",
                "retryable": False,
                "requested_count": 0,
                "resolved_count": 0,
            }
        return {
            **preflight,
            "session": session,
            "memberships": memberships,
            "candidate_scope_ids": [
                str(member.get("candidate_scope_id") or "").strip()
                for member in memberships
                if str(member.get("candidate_scope_id") or "").strip()
            ],
        }

    def preflight_parent_scope(self, search_session_id: str, *, actor_user_id: str) -> dict[str, Any]:
        session = self.get_session(search_session_id, actor_user_id=actor_user_id)
        if not session:
            return {
                "success": False,
                "available": False,
                "error_code": "REFINEMENT_PARENT_NOT_FOUND",
                "retryable": False,
            }
        if session.get("status") != "complete":
            return {
                "success": False,
                "available": False,
                "error_code": "REFINEMENT_PARENT_NOT_COMPLETE",
                "retryable": False,
            }
        expires_raw = str(session.get("membership_expires_at") or "")
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except Exception:
            expires_at = _utc_now() - timedelta(seconds=1)
        if expires_at < _utc_now():
            return {
                "success": False,
                "available": False,
                "error_code": "REFINEMENT_PARENT_EXPIRED",
                "retryable": False,
            }

        with self.lock:
            rows = self.conn.execute(
                """
                SELECT candidate_scope_id, content_hash_at_event, filename, resume_id
                FROM search_scope_membership
                WHERE search_session_id=?
                ORDER BY created_at ASC, candidate_scope_id ASC
                """,
                (str(search_session_id or "").strip(),),
            ).fetchall()
        requested_count = len(rows)
        if requested_count <= 0:
            return {
                "success": False,
                "available": False,
                "error_code": "REFINEMENT_SCOPE_EMPTY",
                "retryable": False,
                "requested_count": 0,
                "resolved_count": 0,
            }
        return {
            "success": True,
            "available": True,
            "search_session_id": str(search_session_id or "").strip(),
            "search_mode": session.get("search_mode") or "root",
            "refinement_depth": int(session.get("refinement_depth") or 0),
            "root_search_session_id": session.get("root_search_session_id") or "",
            "parent_search_session_id": session.get("parent_search_session_id") or "",
            "requested_count": requested_count,
            "resolved_count": requested_count,
            "changed_content_count": 0,
            "unresolvable_count": 0,
            "retryable": False,
            "rank_folder": session.get("rank_folder") or "",
            "applied_ship_type": session.get("applied_ship_type") or "",
            "experienced_ship_type": session.get("experienced_ship_type") or "",
            "membership_expires_at": session.get("membership_expires_at") or "",
        }

    def issue_changed_content_acknowledgement(
        self,
        *,
        actor_user_id: str,
        parent_search_session_id: str,
        search_request_id: str,
        changed_content_set_fingerprint: str,
    ) -> dict[str, Any]:
        actor_user_id = str(actor_user_id or "").strip()
        parent_search_session_id = str(parent_search_session_id or "").strip()
        search_request_id = str(search_request_id or "").strip()
        fingerprint = str(changed_content_set_fingerprint or "").strip()
        if not all((actor_user_id, parent_search_session_id, search_request_id, fingerprint)):
            raise SearchScopeRepositoryError("A complete changed-content acknowledgement binding is required")

        now = _utc_now()
        expires_at = now + timedelta(minutes=self.ACKNOWLEDGEMENT_TTL_MINUTES)
        acknowledgement_id = str(uuid.uuid4())
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO search_changed_content_acknowledgement (
                    acknowledgement_id, actor_user_id, parent_search_session_id,
                    search_request_id, changed_content_set_fingerprint,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    acknowledgement_id,
                    actor_user_id,
                    parent_search_session_id,
                    search_request_id,
                    fingerprint,
                    _iso(now),
                    _iso(expires_at),
                ),
            )
            self.conn.commit()
        return {
            "acknowledgement_id": acknowledgement_id,
            "changed_content_set_fingerprint": fingerprint,
            "expires_at": _iso(expires_at),
        }

    def consume_changed_content_acknowledgement(
        self,
        *,
        acknowledgement_id: str,
        actor_user_id: str,
        parent_search_session_id: str,
        search_request_id: str,
        changed_content_set_fingerprint: str,
    ) -> bool:
        acknowledgement_id = str(acknowledgement_id or "").strip()
        binding = (
            str(actor_user_id or "").strip(),
            str(parent_search_session_id or "").strip(),
            str(search_request_id or "").strip(),
            str(changed_content_set_fingerprint or "").strip(),
        )
        if not acknowledgement_id or not all(binding):
            return False

        now = _utc_now()
        with self.lock:
            cursor = self.conn.execute(
                """
                UPDATE search_changed_content_acknowledgement
                SET consumed_at=?
                WHERE acknowledgement_id=?
                  AND actor_user_id=?
                  AND parent_search_session_id=?
                  AND search_request_id=?
                  AND changed_content_set_fingerprint=?
                  AND consumed_at IS NULL
                  AND expires_at >= ?
                """,
                (_iso(now), acknowledgement_id, *binding, _iso(now)),
            )
            self.conn.commit()
        return cursor.rowcount == 1

    def save_recovery_draft(
        self,
        *,
        actor_user_id: str,
        tab_id: str,
        draft: Mapping[str, Any],
    ) -> dict[str, Any]:
        actor_user_id = str(actor_user_id or "").strip()
        tab_id = str(tab_id or "").strip()
        if not actor_user_id or not tab_id:
            raise SearchScopeRepositoryError("actor_user_id and tab_id are required")
        now = _utc_now()
        expires_at = now + timedelta(hours=self.RECOVERY_DRAFT_TTL_HOURS)
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO ai_search_recovery_draft (
                    actor_user_id, tab_id, schema_version, saved_at, expires_at, draft_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(actor_user_id, tab_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    saved_at=excluded.saved_at,
                    expires_at=excluded.expires_at,
                    draft_json=excluded.draft_json
                """,
                (
                    actor_user_id,
                    tab_id,
                    str(draft.get("schema_version") or "ai_search_recovery.v1"),
                    _iso(now),
                    _iso(expires_at),
                    _json_dumps(dict(draft or {})),
                ),
            )
            stale_rows = self.conn.execute(
                """
                SELECT tab_id
                FROM ai_search_recovery_draft
                WHERE actor_user_id=?
                ORDER BY saved_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
                """,
                (actor_user_id, self.RECOVERY_DRAFT_LIMIT),
            ).fetchall()
            for row in stale_rows:
                self.conn.execute(
                    "DELETE FROM ai_search_recovery_draft WHERE actor_user_id=? AND tab_id=?",
                    (actor_user_id, row["tab_id"]),
                )
            self.conn.commit()
        return {"tab_id": tab_id, "saved_at": _iso(now), "expires_at": _iso(expires_at)}

    def load_latest_recovery_draft(self, *, actor_user_id: str) -> dict[str, Any] | None:
        actor_user_id = str(actor_user_id or "").strip()
        if not actor_user_id:
            return None
        now = _iso(_utc_now())
        with self.lock:
            row = self.conn.execute(
                """
                SELECT tab_id, schema_version, saved_at, expires_at, draft_json
                FROM ai_search_recovery_draft
                WHERE actor_user_id=? AND expires_at >= ?
                ORDER BY saved_at DESC, rowid DESC
                LIMIT 1
                """,
                (actor_user_id, now),
            ).fetchone()
        if not row:
            return None
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except Exception:
            return None
        return {
            "tab_id": row["tab_id"],
            "schema_version": row["schema_version"],
            "saved_at": row["saved_at"],
            "expires_at": row["expires_at"],
            "draft": draft,
        }

    def delete_recovery_drafts(self, *, actor_user_id: str, tab_id: str | None = None) -> None:
        actor_user_id = str(actor_user_id or "").strip()
        if not actor_user_id:
            return
        with self.lock:
            if tab_id:
                self.conn.execute(
                    "DELETE FROM ai_search_recovery_draft WHERE actor_user_id=? AND tab_id=?",
                    (actor_user_id, str(tab_id).strip()),
                )
            else:
                self.conn.execute(
                    "DELETE FROM ai_search_recovery_draft WHERE actor_user_id=?",
                    (actor_user_id,),
                )
            self.conn.commit()

    def cleanup_expired(self, *, limit: int = 1000) -> None:
        now = _iso(_utc_now())
        batch_limit = max(1, int(limit or 1000))
        with self.lock:
            expired_sessions = [
                row[0]
                for row in self.conn.execute(
                    """
                    SELECT search_session_id
                    FROM search_session_lineage
                    WHERE lineage_expires_at < ?
                    LIMIT ?
                    """,
                    (now, batch_limit),
                ).fetchall()
            ]
            for session_id in expired_sessions:
                self.conn.execute(
                    "DELETE FROM search_session_lineage WHERE search_session_id=?",
                    (session_id,),
                )
            self.conn.execute(
                """
                DELETE FROM search_scope_membership
                WHERE search_session_id IN (
                    SELECT search_session_id
                    FROM search_session_lineage
                    WHERE membership_expires_at < ?
                    LIMIT ?
                )
                """,
                (now, batch_limit),
            )
            self.conn.execute(
                "DELETE FROM search_changed_content_acknowledgement WHERE expires_at < ?",
                (now,),
            )
            self.conn.execute(
                "DELETE FROM ai_search_recovery_draft WHERE expires_at < ?",
                (now,),
            )
            request_claim_cutoff = _iso(
                _utc_now() - timedelta(days=self.REQUEST_CLAIM_RETENTION_DAYS)
            )
            self.conn.execute(
                "DELETE FROM ai_search_request_claim WHERE updated_at < ?",
                (request_claim_cutoff,),
            )
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()
