import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime

from repositories.candidate_event_repo import CandidateEventRepo


class DualWriteCandidateEventRepo(CandidateEventRepo):
    """
    Dual-write adapter.
    - Reads are served from read_repo (defaults to primary).
    - Writes go to primary + secondary, with idempotency guard on log_event.
    """

    def __init__(self, primary_repo, secondary_repo, idempotency_db_path, read_repo=None):
        self.primary_repo = primary_repo
        self.secondary_repo = secondary_repo
        self.read_repo = read_repo or primary_repo
        self.idempotency_db_path = idempotency_db_path
        os.makedirs(os.path.dirname(idempotency_db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(idempotency_db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def _canonical_event_key(self, payload):
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return f"candidate_event:{digest}"

    def _has_key(self, key):
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM idempotency_keys WHERE key = ?",
                (key,)
            ).fetchone()
        return bool(row)

    def _store_key(self, key):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys(key, created_at) VALUES (?, ?)",
                (key, datetime.utcnow().isoformat() + "Z")
            )
            self._conn.commit()

    def log_event(
        self,
        candidate_id,
        filename,
        event_type,
        status='New',
        notes='',
        rank_applied_for='',
        search_ship_type='',
        ai_prompt='',
        ai_reason='',
        extracted_data=None,
        idempotency_key=None
    ):
        event_payload = {
            "candidate_id": str(candidate_id),
            "filename": filename,
            "event_type": event_type,
            "status": status,
            "notes": notes,
            "rank_applied_for": rank_applied_for,
            "search_ship_type": search_ship_type,
            "ai_prompt": ai_prompt,
            "ai_reason": ai_reason,
            "extracted_data": extracted_data or {},
        }
        key = idempotency_key or self._canonical_event_key(event_payload)
        if self._has_key(key):
            print(f"[DUAL-WRITE] Skipping duplicate log_event for key={key}")
            return True

        primary_ok = self.primary_repo.log_event(
            candidate_id=candidate_id,
            filename=filename,
            event_type=event_type,
            status=status,
            notes=notes,
            rank_applied_for=rank_applied_for,
            search_ship_type=search_ship_type,
            ai_prompt=ai_prompt,
            ai_reason=ai_reason,
            extracted_data=extracted_data,
        )
        if not primary_ok:
            return False

        self._store_key(key)

        secondary_ok = self.secondary_repo.log_event(
            candidate_id=candidate_id,
            filename=filename,
            event_type=event_type,
            status=status,
            notes=notes,
            rank_applied_for=rank_applied_for,
            search_ship_type=search_ship_type,
            ai_prompt=ai_prompt,
            ai_reason=ai_reason,
            extracted_data=extracted_data,
        )
        if not secondary_ok:
            print(f"[DUAL-WRITE] Secondary write failed for key={key}")
        return primary_ok

    def get_latest_status_per_candidate(self, *args, **kwargs):
        return self.read_repo.get_latest_status_per_candidate(*args, **kwargs)

    def get_candidate_history(self, *args, **kwargs):
        return self.read_repo.get_candidate_history(*args, **kwargs)

    def log_status_change(self, *args, **kwargs):
        primary_ok = self.primary_repo.log_status_change(*args, **kwargs)
        if primary_ok:
            secondary_ok = self.secondary_repo.log_status_change(*args, **kwargs)
            if not secondary_ok:
                print("[DUAL-WRITE] Secondary status_change write failed")
        return primary_ok

    def log_note_added(self, *args, **kwargs):
        primary_ok = self.primary_repo.log_note_added(*args, **kwargs)
        if primary_ok:
            secondary_ok = self.secondary_repo.log_note_added(*args, **kwargs)
            if not secondary_ok:
                print("[DUAL-WRITE] Secondary note_added write failed")
        return primary_ok

    def get_rank_counts(self, *args, **kwargs):
        return self.read_repo.get_rank_counts(*args, **kwargs)

    def get_csv_stats(self, *args, **kwargs):
        stats = self.primary_repo.get_csv_stats(*args, **kwargs)
        if isinstance(stats, dict):
            with self._lock:
                keys_count = self._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
            stats = dict(stats)
            stats["dual_write_idempotency_keys"] = int(keys_count)
            stats["write_mode"] = "dual_write"
            stats["read_mode"] = type(self.read_repo).__name__
        return stats
