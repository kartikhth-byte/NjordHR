import os
import sqlite3
import threading

from repositories.feedback_repo import FeedbackRepo


class CSVFeedbackStore(FeedbackRepo):
    """SQLite-backed feedback adapter used by the current local runtime."""

    def __init__(self, db_path="feedback.db"):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    query TEXT NOT NULL,
                    llm_decision TEXT NOT NULL,
                    llm_reason TEXT,
                    llm_confidence REAL,
                    user_decision TEXT NOT NULL,
                    user_notes TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()

    def add_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes=""):
        with self.lock:
            self.conn.execute("""
                INSERT INTO feedback
                (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes))
            self.conn.commit()
        print(f"[FEEDBACK] Stored: {filename} - User: {user_decision}, LLM: {llm_decision}")

    def get_recent_feedback(self, query, limit=5):
        query_terms = (query or "").split()
        if not query_terms:
            return []

        with self.lock:
            cursor = self.conn.execute("""
                SELECT filename, llm_decision, user_decision, llm_reason, user_notes
                FROM feedback
                WHERE query LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (f"%{query_terms[0]}%", limit))
            return cursor.fetchall()

    def close(self):
        with self.lock:
            self.conn.close()
