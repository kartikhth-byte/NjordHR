import os
import sqlite3
import threading
from datetime import UTC, datetime


class DualWriteStateStore:
    def __init__(self, db_path):
        db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dual_write_state (
                    key TEXT PRIMARY KEY,
                    primary_done INTEGER NOT NULL DEFAULT 0,
                    secondary_done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()
            self._migrate_legacy_idempotency_keys()

    @staticmethod
    def _now_iso():
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _ensure_row(self, key):
        now = self._now_iso()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO dual_write_state(key, primary_done, secondary_done, created_at, updated_at)
            VALUES (?, 0, 0, ?, ?)
            """,
            (key, now, now),
        )

    def _migrate_legacy_idempotency_keys(self):
        tables = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('idempotency_keys', 'dual_write_state')"
        ).fetchall()
        table_names = {row[0] for row in tables}
        if "idempotency_keys" not in table_names:
            return
        rows = self._conn.execute("SELECT key, created_at FROM idempotency_keys").fetchall()
        if not rows:
            return
        now = self._now_iso()
        for key, created_at in rows:
            legacy_created = str(created_at or now)
            self._conn.execute(
                """
                INSERT OR IGNORE INTO dual_write_state(
                    key, primary_done, secondary_done, created_at, updated_at
                ) VALUES (?, 1, 1, ?, ?)
                """,
                (str(key), legacy_created, legacy_created),
            )
        self._conn.commit()

    def get(self, key):
        with self._lock:
            row = self._conn.execute(
                """
                SELECT primary_done, secondary_done, created_at, updated_at
                FROM dual_write_state
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if not row:
            return None
        return {
            "primary_done": bool(row[0]),
            "secondary_done": bool(row[1]),
            "created_at": row[2],
            "updated_at": row[3],
        }

    def is_complete(self, key):
        state = self.get(key)
        return bool(state and state["primary_done"] and state["secondary_done"])

    def is_primary_done(self, key):
        state = self.get(key)
        return bool(state and state["primary_done"])

    def is_secondary_done(self, key):
        state = self.get(key)
        return bool(state and state["secondary_done"])

    def mark_primary_done(self, key):
        with self._lock:
            self._ensure_row(key)
            self._conn.execute(
                """
                UPDATE dual_write_state
                SET primary_done = 1,
                    updated_at = ?
                WHERE key = ?
                """,
                (self._now_iso(), key),
            )
            self._conn.commit()

    def mark_secondary_done(self, key):
        with self._lock:
            self._ensure_row(key)
            self._conn.execute(
                """
                UPDATE dual_write_state
                SET secondary_done = 1,
                    updated_at = ?
                WHERE key = ?
                """,
                (self._now_iso(), key),
            )
            self._conn.commit()

    def count(self):
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM dual_write_state").fetchone()
        return int(row[0] if row else 0)
