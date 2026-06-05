import os
import sqlite3
import threading
import uuid

from repositories.registry_repo import RegistryRepo
from repositories.resume_identity import (
    LEGACY_UNKNOWN_PROVENANCE,
    VERIFIED_CONTENT_HASH_PROVENANCE,
    stable_resume_id,
    stable_resume_identity,
)


class CSVFileRegistry(RegistryRepo):
    """SQLite-backed registry adapter used by the current local runtime."""

    DB_VERSION = 4

    def __init__(self, db_path="registry.db"):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._migrate_schema()

    def _get_db_version(self):
        with self.lock:
            try:
                result = self.conn.execute("SELECT version FROM schema_version").fetchone()
                return result[0] if result else 0
            except sqlite3.OperationalError:
                return 0

    def _set_db_version(self, version):
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version VALUES (?)", (version,))
            self.conn.commit()

    def _migrate_schema(self):
        current_version = self._get_db_version()
        if current_version <= 0:
            self._create_table()
            self._ensure_identity_columns()
            self._set_db_version(self.DB_VERSION)
            return
        if current_version < self.DB_VERSION:
            print(f"[Database Migration] Upgrading from version {current_version} to {self.DB_VERSION}...")
            self._create_table()
            self._ensure_identity_columns()
            self._set_db_version(self.DB_VERSION)

    def _create_table(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    file_path TEXT PRIMARY KEY,
                    last_modified REAL NOT NULL,
                    resume_id TEXT NOT NULL,
                    resume_id_provenance TEXT NOT NULL DEFAULT 'legacy_unknown',
                    content_hash TEXT NOT NULL DEFAULT '',
                    candidate_scope_id TEXT NOT NULL DEFAULT ''
                )
            """)
            self.conn.commit()

    def _ensure_column(self, table_name, column_name, column_sql):
        with self.lock:
            columns = {
                row[1]
                for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name not in columns:
                self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
                self.conn.commit()

    def _ensure_identity_columns(self):
        self._ensure_column(
            "files",
            "resume_id_provenance",
            f"TEXT NOT NULL DEFAULT '{LEGACY_UNKNOWN_PROVENANCE}'",
        )
        self._ensure_column("files", "content_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("files", "candidate_scope_id", "TEXT NOT NULL DEFAULT ''")
        self._backfill_candidate_scope_ids()

    def _backfill_candidate_scope_ids(self):
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT file_path, candidate_scope_id
                FROM files
                WHERE candidate_scope_id IS NULL OR candidate_scope_id=''
                """
            ).fetchall()
            for file_path, candidate_scope_id in rows:
                if candidate_scope_id:
                    continue
                self.conn.execute(
                    "UPDATE files SET candidate_scope_id=? WHERE file_path=?",
                    (str(uuid.uuid4()), file_path),
                )
            if rows:
                self.conn.commit()

    def _candidate_scope_id_for_identity(self, file_path, identity):
        with self.lock:
            existing = self.conn.execute(
                "SELECT candidate_scope_id FROM files WHERE file_path=?",
                (file_path,),
            ).fetchone()
            if existing and str(existing[0] or "").strip():
                return str(existing[0]).strip()

            if identity.content_hash:
                content_match = self.conn.execute(
                    """
                    SELECT candidate_scope_id
                    FROM files
                    WHERE content_hash=?
                      AND resume_id_provenance=?
                      AND candidate_scope_id IS NOT NULL
                      AND candidate_scope_id!=''
                    ORDER BY rowid ASC
                    LIMIT 1
                    """,
                    (identity.content_hash, VERIFIED_CONTENT_HASH_PROVENANCE),
                ).fetchone()
                if content_match and str(content_match[0] or "").strip():
                    return str(content_match[0]).strip()

        return str(uuid.uuid4())

    def generate_resume_id(self, file_path):
        return stable_resume_id(file_path)

    def generate_resume_identity(self, file_path):
        return stable_resume_identity(file_path)

    def needs_processing(self, file_path, last_modified):
        with self.lock:
            result = self.conn.execute(
                "SELECT last_modified FROM files WHERE file_path=?",
                (file_path,),
            ).fetchone()
        return not result or result[0] < last_modified

    def upsert_file_record(self, file_path, last_modified, resume_id):
        identity = stable_resume_identity(file_path)
        if identity.resume_id == str(resume_id):
            provenance = identity.alias_provenance
            content_hash = identity.content_hash
        else:
            provenance = LEGACY_UNKNOWN_PROVENANCE
            content_hash = ""
        candidate_scope_id = self._candidate_scope_id_for_identity(file_path, identity)
        with self.lock:
            self.conn.execute("""
                INSERT INTO files (
                    file_path, last_modified, resume_id, resume_id_provenance, content_hash, candidate_scope_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    last_modified=excluded.last_modified,
                    resume_id=excluded.resume_id,
                    resume_id_provenance=excluded.resume_id_provenance,
                    content_hash=excluded.content_hash,
                    candidate_scope_id=excluded.candidate_scope_id
            """, (file_path, last_modified, resume_id, provenance, content_hash, candidate_scope_id))
            self.conn.commit()

    def get_resume_id(self, file_path):
        with self.lock:
            result = self.conn.execute(
                "SELECT resume_id FROM files WHERE file_path=?",
                (file_path,),
            ).fetchone()
        return result[0] if result else self.generate_resume_id(file_path)

    def get_resume_identity_record(self, file_path):
        with self.lock:
            result = self.conn.execute(
                """
                SELECT resume_id, resume_id_provenance, content_hash, candidate_scope_id
                FROM files
                WHERE file_path=?
                """,
                (file_path,),
            ).fetchone()
        if result:
            return {
                "resume_id": result[0],
                "alias_provenance": result[1],
                "content_hash": result[2],
                "candidate_scope_id": result[3],
                "is_authoritative_content_alias": (
                    result[1] == VERIFIED_CONTENT_HASH_PROVENANCE
                    and bool(result[2])
                    and result[0] == result[2]
                ),
            }
        identity = stable_resume_identity(file_path)
        return {
            "resume_id": identity.resume_id,
            "alias_provenance": identity.alias_provenance,
            "content_hash": identity.content_hash,
            "candidate_scope_id": "",
            "is_authoritative_content_alias": identity.is_authoritative_content_alias,
        }

    def get_candidate_scope_id(self, file_path):
        return str(self.get_resume_identity_record(file_path).get("candidate_scope_id") or "").strip()

    def close(self):
        with self.lock:
            self.conn.close()
