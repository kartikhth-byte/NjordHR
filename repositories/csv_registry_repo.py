import os
import sqlite3
import threading

from repositories.registry_repo import RegistryRepo
from repositories.resume_identity import stable_resume_id


class CSVFileRegistry(RegistryRepo):
    """SQLite-backed registry adapter used by the current local runtime."""

    DB_VERSION = 2

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
        if current_version < self.DB_VERSION:
            if current_version > 0:
                print(f"[Database Migration] Upgrading from version {current_version} to {self.DB_VERSION}...")
                with self.lock:
                    self.conn.execute("DROP TABLE IF EXISTS files")
            self._create_table()
            self._set_db_version(self.DB_VERSION)

    def _create_table(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    file_path TEXT PRIMARY KEY,
                    last_modified REAL NOT NULL,
                    resume_id TEXT NOT NULL
                )
            """)
            self.conn.commit()

    def generate_resume_id(self, file_path):
        return stable_resume_id(file_path)

    def needs_processing(self, file_path, last_modified):
        with self.lock:
            result = self.conn.execute(
                "SELECT last_modified FROM files WHERE file_path=?",
                (file_path,),
            ).fetchone()
        return not result or result[0] < last_modified

    def upsert_file_record(self, file_path, last_modified, resume_id):
        with self.lock:
            self.conn.execute("""
                INSERT INTO files (file_path, last_modified, resume_id) VALUES (?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    last_modified=excluded.last_modified,
                    resume_id=excluded.resume_id
            """, (file_path, last_modified, resume_id))
            self.conn.commit()

    def get_resume_id(self, file_path):
        with self.lock:
            result = self.conn.execute(
                "SELECT resume_id FROM files WHERE file_path=?",
                (file_path,),
            ).fetchone()
        return result[0] if result else self.generate_resume_id(file_path)

    def close(self):
        with self.lock:
            self.conn.close()
