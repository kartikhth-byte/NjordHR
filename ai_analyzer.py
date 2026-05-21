# ai_analyzer.py - COMPLETE VERSION WITH SSE STREAMING + LEARNING + MULTI-STAGE RETRIEVAL

import os
import sys
import json
import time
import hashlib
import sqlite3
import configparser
import re
import io
import threading
from pathlib import Path
from datetime import UTC, datetime, date, timedelta
from collections import Counter

# --- Core Dependencies ---
import requests
import fitz  # PyMuPDF
from PIL import Image
from pinecone import Pinecone, ServerlessSpec
from rank_folders import rank_folder_path
from runtime_env import normalize_env_value, normalized_url

# --- Optional/Specialized Dependencies ---
try:
    import pytesseract
    HAS_OCR = True
    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = os.getenv(
            "TESSERACT_CMD",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        )
except ImportError:
    print("Warning: Tesseract OCR not found. OCR features will be disabled.")
    HAS_OCR = False
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    print("Warning: pdfplumber not found. Using PyMuPDF as primary extractor.")
    HAS_PDFPLUMBER = False


# ==============================================================================
# 1. CONFIGURATION MANAGER
# ==============================================================================
class ConfigManager:
    """Reads and validates configuration from a configparser object."""
    def __init__(self, config_parser):
        self.config = config_parser

    def _resolve_runtime_path(self, raw_path, fallback_name):
        candidate = str(raw_path or "").strip()
        if not candidate:
            candidate = fallback_name
        candidate = os.path.expanduser(candidate)
        if os.path.isabs(candidate):
            return os.path.abspath(candidate)
        return os.path.abspath(os.path.join(self.log_dir, candidate))

    @property
    def gemini_api_key(self): return os.getenv('GEMINI_API_KEY', self.config.get('Credentials', 'Gemini_API_Key'))
    @property
    def pinecone_api_key(self): return os.getenv('PINECONE_API_KEY', self.config.get('Credentials', 'Pinecone_API_Key'))
    @property
    def download_root(self): return self.config.get('Settings', 'Default_Download_Folder')
    @property
    def pinecone_env(self): return self.config.get('Advanced', 'pinecone_environment')
    @property
    def pinecone_index(self): return self.config.get('Advanced', 'pinecone_index_name')
    @property
    def embedding_model(self): return self.config.get('Advanced', 'embedding_model_name')
    @property
    def reasoning_model(self): return self.config.get('Advanced', 'reasoning_model_name')
    @property
    def min_similarity_score(self): return self.config.getfloat('Advanced', 'min_similarity_score')
    @property
    def log_dir(self): return os.path.abspath(os.path.expanduser(self.config.get('Advanced', 'log_dir', fallback='logs')))
    @property
    def registry_db_path(self): return self._resolve_runtime_path(self.config.get('Advanced', 'registry_db_path', fallback='registry.db'), 'registry.db')
    @property
    def feedback_db_path(self): return self._resolve_runtime_path(self.config.get('Advanced', 'feedback_db_path', fallback='feedback.db'), 'feedback.db')


# ==============================================================================
# 2. LOCAL FILE REGISTRY (SQLite)
# ==============================================================================
class FileRegistry:
    """Manages a local SQLite database to track file states and avoid reprocessing."""
    DB_VERSION = 2
    
    def __init__(self, db_path='registry.db'):
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
        return hashlib.sha1(file_path.encode()).hexdigest()

    def needs_processing(self, file_path, last_modified):
        with self.lock:
            result = self.conn.execute(
                "SELECT last_modified FROM files WHERE file_path=?",
                (file_path,)
            ).fetchone()
        return not result or result[0] < last_modified

    def upsert_file_record(self, file_path, last_modified, resume_id):
        with self.lock:
            self.conn.execute("""
                INSERT INTO files (file_path, last_modified, resume_id) VALUES (?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET last_modified=excluded.last_modified
            """, (file_path, last_modified, resume_id))
            self.conn.commit()

    def get_resume_id(self, file_path):
        with self.lock:
            result = self.conn.execute(
                "SELECT resume_id FROM files WHERE file_path=?",
                (file_path,)
            ).fetchone()
        return result[0] if result else self.generate_resume_id(file_path)


# ==============================================================================
# 3. FEEDBACK STORE
# ==============================================================================
class FeedbackStore:
    """Stores user feedback on match decisions for learning"""
    
    def __init__(self, db_path='feedback.db'):
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
    
    def add_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence, 
                     user_decision, user_notes=""):
        """Store user feedback"""
        with self.lock:
            self.conn.execute("""
                INSERT INTO feedback 
                (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (filename, query, llm_decision, llm_reason, llm_confidence, user_decision, user_notes))
            self.conn.commit()
        print(f"[FEEDBACK] Stored: {filename} - User: {user_decision}, LLM: {llm_decision}")
    
    def get_recent_feedback(self, query, limit=5):
        """Get recent feedback for similar queries (for learning)"""
        query_terms = (query or "").split()
        if not query_terms:
            return []

        # Simple keyword matching - could be improved with embeddings
        with self.lock:
            cursor = self.conn.execute("""
                SELECT filename, llm_decision, user_decision, llm_reason, user_notes
                FROM feedback 
                WHERE query LIKE ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (f"%{query_terms[0]}%", limit))
            return cursor.fetchall()


def _resolve_supabase_api_key():
    return (
        normalize_env_value(os.getenv("SUPABASE_SECRET_KEY", ""))
        or normalize_env_value(os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    )


def _should_use_cloud_ai_store():
    return (
        normalize_env_value(os.getenv("USE_SUPABASE_DB", "")).lower() in {"1", "true", "yes", "on"}
        and bool(normalized_url(os.getenv("SUPABASE_URL", "")))
        and bool(_resolve_supabase_api_key())
    )


class SupabaseStoreBase:
    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self):
        self.supabase_url = normalized_url(os.getenv("SUPABASE_URL", ""))
        self.api_key = _resolve_supabase_api_key()
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


class SupabaseFileRegistry(SupabaseStoreBase):
    """Cloud-backed replacement for local registry.db."""

    @staticmethod
    def _file_key(file_path):
        return os.path.basename(str(file_path or "")).strip()

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


class SupabaseFeedbackStore(SupabaseStoreBase):
    """Cloud-backed replacement for local feedback.db."""

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


# ==============================================================================
# 4. PDF PROCESSING MODULE
# ==============================================================================
class AdvancedPDFProcessor:
    """Handles advanced PDF text extraction and OCR."""
    def extract_text(self, file_path):
        text = ""
        if HAS_PDFPLUMBER:
            try:
                with pdfplumber.open(file_path) as pdf:
                    text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
                if len(text.strip()) > 100: return text
            except Exception: pass
        
        try:
            with fitz.open(file_path) as doc:
                text = "\n".join(page.get_text() for page in doc)
            if len(text.strip()) > 100: return text
        except Exception: pass

        if HAS_OCR:
            try:
                doc = fitz.open(file_path)
                ocr_text = ""
                for page in doc:
                    pix = page.get_pixmap(dpi=300)
                    img = Image.open(io.BytesIO(pix.tobytes()))
                    ocr_text += pytesseract.image_to_string(img) + "\n"
                return ocr_text
            except Exception: pass
        
        return ""


# ==============================================================================
# 5. RAG PREPARATION MODULE
# ==============================================================================
class RAGPrepper:
    """Handles text chunking and calls the Gemini API for embeddings."""
    def __init__(self, config_manager):
        self.config = config_manager
        self.last_error = None
        self._resolved_embedding_model = None

    def expected_embedding_dimension(self):
        """
        Return known embedding dimension for configured model when available.
        This avoids Pinecone dimension mismatches during index selection.
        """
        model = self.config.embedding_model
        known_dims = {
            "text-embedding-004": 768,
            "gemini-embedding-001": 3072,
        }
        return known_dims.get(model)

    def _list_available_embedding_models(self):
        """Return embedding-capable model names from Gemini ListModels."""
        api_url = "https://generativelanguage.googleapis.com/v1beta/models"
        params = {"key": self.config.gemini_api_key, "pageSize": 1000}
        try:
            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            supported = []
            for model in models:
                name = model.get("name", "")
                methods = model.get("supportedGenerationMethods", [])
                if not name.startswith("models/"):
                    continue
                short_name = name.split("/", 1)[1]
                method_set = set(methods or [])
                if "batchEmbedContents" in method_set or "embedContent" in method_set:
                    supported.append(short_name)
            return supported
        except requests.exceptions.RequestException as e:
            self.last_error = f"Could not list Gemini models: {e}"
            return []

    def _chunk_metadata(self, resume_id, rank, chunk_text, filename=None, source_path=None):
        metadata = {
            "resume_id": resume_id,
            "rank": rank,
            "raw_text": chunk_text,
        }
        if filename:
            metadata["filename"] = str(filename)
        if source_path:
            metadata["source_path"] = str(source_path)
        return metadata

    def _split_structure_blocks(self, text):
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        blocks = []
        current_lines = []

        for raw_line in normalized.split("\n"):
            line = raw_line.strip()
            if not line:
                if current_lines:
                    blocks.append("\n".join(current_lines))
                    current_lines = []
                continue
            current_lines.append(line)

        if current_lines:
            blocks.append("\n".join(current_lines))

        return [block for block in blocks if block.strip()]

    def _split_large_block(self, block_text, chunk_size):
        lines = [line.strip() for line in str(block_text or "").split("\n") if line.strip()]
        if len(lines) <= 1:
            tokens = str(block_text or "").split()
            return [" ".join(tokens[i:i + chunk_size]) for i in range(0, len(tokens), chunk_size)]

        chunks = []
        current_lines = []
        current_token_count = 0

        for line in lines:
            line_tokens = line.split()
            line_token_count = len(line_tokens)
            if line_token_count >= chunk_size:
                if current_lines:
                    chunks.append("\n".join(current_lines))
                    current_lines = []
                    current_token_count = 0
                chunks.extend(" ".join(line_tokens[i:i + chunk_size]) for i in range(0, line_token_count, chunk_size))
                continue

            if current_lines and current_token_count + line_token_count > chunk_size:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_token_count = 0

            current_lines.append(line)
            current_token_count += line_token_count

        if current_lines:
            chunks.append("\n".join(current_lines))

        return chunks

    def chunk_text(self, text, resume_id, rank, filename=None, source_path=None):
        chunk_size, overlap = 400, 50
        blocks = self._split_structure_blocks(text)
        if not blocks:
            return []

        units = []
        for block in blocks:
            token_count = len(block.split())
            if token_count <= chunk_size:
                units.append(block)
            else:
                units.extend(self._split_large_block(block, chunk_size))

        chunks = []
        current_units = []
        current_token_count = 0
        overlap_seed = ""

        for unit in units:
            unit_token_count = len(unit.split())
            if current_units and current_token_count + unit_token_count > chunk_size:
                chunk_text = "\n\n".join(current_units).strip()
                if chunk_text:
                    chunks.append({
                        "text": chunk_text,
                        "metadata": self._chunk_metadata(
                            resume_id, rank, chunk_text, filename=filename, source_path=source_path
                        ),
                    })
                    overlap_seed = " ".join(chunk_text.split()[-overlap:])
                current_units = [overlap_seed, unit] if overlap_seed else [unit]
                current_token_count = len(" ".join(current_units).split())
                continue

            current_units.append(unit)
            current_token_count += unit_token_count

        if current_units:
            chunk_text = "\n\n".join(part for part in current_units if str(part or "").strip()).strip()
            if chunk_text:
                chunks.append({
                    "text": chunk_text,
                    "metadata": self._chunk_metadata(
                        resume_id, rank, chunk_text, filename=filename, source_path=source_path
                    ),
                })

        return chunks

    def get_embeddings(self, texts):
        if not texts: return []

        self.last_error = None
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.config.gemini_api_key}

        configured_model = self.config.embedding_model
        model_candidates = []
        if self._resolved_embedding_model:
            model_candidates.append(self._resolved_embedding_model)
        model_candidates.append(configured_model)
        # Fallback to current Gemini embedding model if older model is configured.
        if configured_model == "text-embedding-004":
            model_candidates.append("gemini-embedding-001")
        elif configured_model == "gemini-embedding-001":
            model_candidates.append("text-embedding-004")

        # Deduplicate while preserving order.
        deduped = []
        seen = set()
        for m in model_candidates:
            if m and m not in seen:
                deduped.append(m)
                seen.add(m)
        model_candidates = deduped

        # Gemini embedding endpoints are reliably exposed on v1beta.
        api_versions = ["v1beta"]
        attempt_errors = []

        for model_name in model_candidates:
            requests_data = [
                {"model": f"models/{model_name}", "content": {"parts": [{"text": t}]}}
                for t in texts
            ]
            for api_version in api_versions:
                api_url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model_name}:batchEmbedContents"
                try:
                    response = requests.post(
                        api_url,
                        headers=headers,
                        json={"requests": requests_data},
                        timeout=45
                    )
                    if response.ok:
                        payload = response.json()
                        if "embeddings" in payload:
                            self._resolved_embedding_model = model_name
                            return [item["values"] for item in payload["embeddings"]]
                        attempt_errors.append(
                            f"{model_name}@{api_version}: Embedding API returned no embeddings."
                        )
                        continue

                    error_text = response.text.strip().replace("\n", " ")
                    if len(error_text) > 300:
                        error_text = error_text[:300] + "..."
                    attempt_errors.append(
                        f"Embedding request failed ({response.status_code}) "
                        f"using {model_name} on {api_version}: {error_text}"
                    )
                except requests.exceptions.RequestException as e:
                    attempt_errors.append(
                        f"Embedding request error using {model_name} on {api_version}: {e}"
                    )

        # If preferred models fail, discover a working embedding model for this key.
        discovered_models = self._list_available_embedding_models()
        for model_name in discovered_models:
            if model_name in seen:
                continue
            requests_data = [
                {"model": f"models/{model_name}", "content": {"parts": [{"text": t}]}}
                for t in texts
            ]
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:batchEmbedContents"
            try:
                response = requests.post(
                    api_url,
                    headers=headers,
                    json={"requests": requests_data},
                    timeout=45
                )
                if response.ok:
                    payload = response.json()
                    if "embeddings" in payload:
                        self._resolved_embedding_model = model_name
                        return [item["values"] for item in payload["embeddings"]]
                    attempt_errors.append(
                        f"{model_name}@v1beta: Embedding API returned no embeddings."
                    )
                    continue

                error_text = response.text.strip().replace("\n", " ")
                if len(error_text) > 300:
                    error_text = error_text[:300] + "..."
                attempt_errors.append(
                    f"Embedding request failed ({response.status_code}) "
                    f"using {model_name} on v1beta: {error_text}"
                )
            except requests.exceptions.RequestException as e:
                attempt_errors.append(
                    f"Embedding request error using {model_name} on v1beta: {e}"
                )

        if attempt_errors:
            self.last_error = " | ".join(attempt_errors[-3:])
            print(f"[ERROR] {self.last_error}")
        else:
            self.last_error = "Embedding request failed for unknown reasons."
            print(f"[ERROR] {self.last_error}")
        return []


# ==============================================================================
# 6. PINECONE VECTOR DATABASE MANAGER
# ==============================================================================
class PineconeManager:
    """Manages the connection and querying of a Pinecone index."""
    EMPTY_NAMESPACE_RETRY_COUNT = 2
    EMPTY_NAMESPACE_RETRY_DELAY_SECONDS = 3

    def __init__(self, config_manager, embedding_dimension=None):
        self.config = config_manager
        self.pc = Pinecone(api_key=self.config.pinecone_api_key)
        self.base_index_name = self.config.pinecone_index
        self.index_name = self.base_index_name
        self.embedding_dimension = embedding_dimension
        self._index = None
        self.last_error = None

    def _coerce_dict(self, value):
        if isinstance(value, dict):
            return value
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return {}

    def _index_name_for_dimension(self, dimension):
        # Pinecone index names should stay compact and deterministic.
        suffix = f"-d{dimension}"
        if self.base_index_name.endswith(suffix):
            return self.base_index_name
        max_base_len = 45 - len(suffix)
        trimmed_base = self.base_index_name[:max_base_len]
        return f"{trimmed_base}{suffix}"

    def _ensure_dimension(self, vector_dimension):
        if vector_dimension is None:
            return
        if self.embedding_dimension != vector_dimension:
            self.embedding_dimension = vector_dimension
            self._index = None

    def _candidate_dimensions(self):
        candidates = []
        for dimension in (self.embedding_dimension, 768, 3072):
            if dimension and dimension not in candidates:
                candidates.append(dimension)
        return candidates

    def _candidate_index_names(self):
        candidates = []
        for dimension in self._candidate_dimensions():
            index_name = self._index_name_for_dimension(dimension)
            if index_name not in candidates:
                candidates.append(index_name)
        if self.base_index_name not in candidates:
            candidates.append(self.base_index_name)
        return candidates

    def namespace_vector_count(self, namespace):
        try:
            retry_count = max(0, int(getattr(self, "EMPTY_NAMESPACE_RETRY_COUNT", 0)))
            retry_delay = max(0, float(getattr(self, "EMPTY_NAMESPACE_RETRY_DELAY_SECONDS", 0)))

            for attempt in range(retry_count + 1):
                stats = self._coerce_dict(self.index.describe_index_stats())
                namespaces = stats.get("namespaces", {})
                ns_meta = namespaces.get(namespace, {})
                count = int(ns_meta.get("vector_count", 0) or 0)
                if count > 0:
                    return count

                for index_name in self._candidate_index_names():
                    has_vectors = self.namespace_has_vectors(namespace, index_name=index_name)
                    if has_vectors:
                        return 1

                if attempt < retry_count:
                    time.sleep(retry_delay)

            return 0
        except Exception as e:
            self.last_error = f"Failed to inspect namespace stats: {e}"
            print(f"[ERROR] {self.last_error}")
            return 0

    def namespace_has_vectors(self, namespace, dimension=None, index_name=None):
        try:
            resolved_index_name = index_name or self.index_name

            index = self.index
            if resolved_index_name != self.index_name:
                available_indexes = self.pc.list_indexes().names()
                if resolved_index_name not in available_indexes:
                    return False
                index = self.pc.Index(resolved_index_name)

            try:
                pages = index.list(namespace=namespace, limit=1)
                first_page = next(iter(pages), [])
                ids = list(first_page or [])
                return bool(ids)
            except Exception as list_error:
                query_dimension = dimension
                if query_dimension is None:
                    if resolved_index_name.endswith("-d3072"):
                        query_dimension = 3072
                    elif resolved_index_name.endswith("-d768"):
                        query_dimension = 768
                    else:
                        query_dimension = self.embedding_dimension or 768
                print(
                    f"[WARN] namespace list probe failed for namespace={namespace} "
                    f"index={resolved_index_name}: {list_error}. Falling back to query probe."
                )
                results = index.query(
                    namespace=namespace,
                    vector=[0.0] * query_dimension,
                    top_k=1,
                    include_metadata=False,
                )
                if isinstance(results, dict):
                    matches = results.get("matches", [])
                else:
                    matches = getattr(results, "matches", [])
                return bool(matches)
        except Exception as e:
            self.last_error = f"Failed to probe namespace contents: {e}"
            print(f"[WARN] {self.last_error}")
            return False

    @property
    def index(self):
        if self._index is None:
            dimension = self.embedding_dimension or 768
            self.index_name = self._index_name_for_dimension(dimension)
            available_indexes = self.pc.list_indexes().names()

            if self.index_name not in available_indexes:
                print(f"[PINECONE] Creating new index: {self.index_name}")
                self.pc.create_index(
                    name=self.index_name,
                    dimension=dimension,
                    metric='cosine',
                    spec=ServerlessSpec(cloud='aws', region='us-east-1')
                )

            self._index = self.pc.Index(self.index_name)
        return self._index

    def upsert_chunks(self, chunks, embeddings, rank):
        if not embeddings: return
        self.last_error = None
        self._ensure_dimension(len(embeddings[0]))

        vectors_to_upsert = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{chunk['metadata']['resume_id']}-{i}"
            vectors_to_upsert.append({"id": chunk_id, "values": embeddings[i], "metadata": chunk['metadata']})

        if not vectors_to_upsert:
            return
        try:
            index = self.index
            index.upsert(vectors=vectors_to_upsert, namespace=rank)
        except Exception as e:
            self.last_error = f"Upsert failed: {e}"
            print(f"[ERROR] {self.last_error}")

    def query(self, query_embedding, rank, top_k=30):
        self.last_error = None
        self._ensure_dimension(len(query_embedding))
        try:
            results = self.index.query(namespace=rank, vector=query_embedding, top_k=top_k, include_metadata=True)
            return results['matches']
        except Exception as e:
            self.last_error = f"Query failed: {e}"
            print(f"[ERROR] {self.last_error}")
            return []


# ==============================================================================
# 7. CORE RAG ENGINE WITH LEARNING + MULTI-STAGE RETRIEVAL
# ==============================================================================
class AIResumeAnalyzer:
    """The main RAG pipeline engine with feedback learning and multi-stage retrieval."""
    FACTS_VERSION = "2.0"
    SYNC_REEXTRACT_COOLDOWN_SECONDS = 24 * 60 * 60
    SYNC_REEXTRACT_PER_SEARCH_LIMIT = 5
    SYNC_REEXTRACT_TIMEOUT_SECONDS = 10
    SYNC_REEXTRACT_WAIT_SECONDS = 12
    LLM_RATE_LIMIT_SLEEP_SECONDS = 0.5
    LLM_REQUEST_TIMEOUT_SECONDS = 45
    LLM_CONTEXT_TEXT_CHAR_LIMIT = 30000
    V2_ONLY_CONSTRAINT_IDS = {
        "rank_match",
        "coc_document_gate",
        "stcw_basic",
        "company_continuity",
        "passport_validity",
        "availability",
        "coc_grade_match",
        "stcw_endorsement",
        "recency",
    }
    RANK_ALIAS_TABLE = {
        "master": {
            "canonical_id": "master",
            "department": "deck",
            "seniority_bucket": "command",
        },
        "captain": {
            "canonical_id": "master",
            "department": "deck",
            "seniority_bucket": "command",
        },
        "chief officer": {
            "canonical_id": "chief_officer",
            "department": "deck",
            "seniority_bucket": "senior_officer",
        },
        "chief mate": {
            "canonical_id": "chief_officer",
            "department": "deck",
            "seniority_bucket": "senior_officer",
        },
        "c o": {
            "canonical_id": "chief_officer",
            "department": "deck",
            "seniority_bucket": "senior_officer",
        },
        "2nd officer": {
            "canonical_id": "2nd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "second officer": {
            "canonical_id": "2nd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "2 o": {
            "canonical_id": "2nd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "3rd officer": {
            "canonical_id": "3rd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "third officer": {
            "canonical_id": "3rd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "3 o": {
            "canonical_id": "3rd_officer",
            "department": "deck",
            "seniority_bucket": "junior_officer",
        },
        "chief engineer": {
            "canonical_id": "chief_engineer",
            "department": "engine",
            "seniority_bucket": "command",
        },
        "c e": {
            "canonical_id": "chief_engineer",
            "department": "engine",
            "seniority_bucket": "command",
        },
        "2nd engineer": {
            "canonical_id": "2nd_engineer",
            "department": "engine",
            "seniority_bucket": "senior_officer",
        },
        "2nd eng": {
            "canonical_id": "2nd_engineer",
            "department": "engine",
            "seniority_bucket": "senior_officer",
        },
        "2nd engg": {
            "canonical_id": "2nd_engineer",
            "department": "engine",
            "seniority_bucket": "senior_officer",
        },
        "second engineer": {
            "canonical_id": "2nd_engineer",
            "department": "engine",
            "seniority_bucket": "senior_officer",
        },
        "2 e": {
            "canonical_id": "2nd_engineer",
            "department": "engine",
            "seniority_bucket": "senior_officer",
        },
        "3rd engineer": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "3rd eng": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "3rd engg": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "3 eng": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "third engineer": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "3 e": {
            "canonical_id": "3rd_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "4th engineer": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "4th eng": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "4th engg": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "4 eng": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "junior 4th engineer": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "fourth engineer": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "junior fourth engineer": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "4 e": {
            "canonical_id": "4th_engineer",
            "department": "engine",
            "seniority_bucket": "junior_officer",
        },
        "deck cadet": {
            "canonical_id": "deck_cadet",
            "department": "deck",
            "seniority_bucket": "cadet",
        },
        "junior engineer": {
            "canonical_id": "junior_engineer",
            "department": "engine",
            "seniority_bucket": "junior_engineer",
        },
        "electrical officer": {
            "canonical_id": "electrical_officer",
            "department": "engine",
            "seniority_bucket": "specialist_officer",
        },
        "eto": {
            "canonical_id": "electro_technical_officer",
            "department": "engine",
            "seniority_bucket": "specialist_officer",
        },
        "electro technical officer": {
            "canonical_id": "electro_technical_officer",
            "department": "engine",
            "seniority_bucket": "specialist_officer",
        },
        "bosun": {
            "canonical_id": "bosun",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "os": {
            "canonical_id": "os",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "ordinary seaman": {
            "canonical_id": "os",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "ab": {
            "canonical_id": "ab",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "a b": {
            "canonical_id": "ab",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "able seaman": {
            "canonical_id": "ab",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "able bodied seaman": {
            "canonical_id": "ab",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "trainee ordinary seaman": {
            "canonical_id": "os",
            "department": "deck",
            "seniority_bucket": "rating",
        },
        "wiper": {
            "canonical_id": "wiper",
            "department": "engine",
            "seniority_bucket": "rating",
        },
        "pumpman": {
            "canonical_id": "pumpman",
            "department": "engine",
            "seniority_bucket": "rating",
        },
        "fitter": {
            "canonical_id": "fitter",
            "department": "engine",
            "seniority_bucket": "rating",
        },
        "chief cook": {
            "canonical_id": "chief_cook",
            "department": "hotel",
            "seniority_bucket": "rating",
        },
        "oiler": {
            "canonical_id": "oiler",
            "department": "engine",
            "seniority_bucket": "rating",
        },
    }
    
    def __init__(self, config_parser):
        print("[INIT] Initializing AIResumeAnalyzer with Multi-Stage Retrieval...")
        self.config = ConfigManager(config_parser)
        self.ingest_registry_cache = None
        if _should_use_cloud_ai_store():
            try:
                self.registry = SupabaseFileRegistry()
                self.feedback = SupabaseFeedbackStore()
                self.ingest_registry_cache = FileRegistry(self.config.registry_db_path)
                print("[INIT] Using Supabase-backed AI registry/feedback stores.")
            except Exception as exc:
                raise RuntimeError(f"Cloud AI stores are required but unavailable: {exc}")
        else:
            self.registry = FileRegistry(self.config.registry_db_path)
            self.feedback = FeedbackStore(self.config.feedback_db_path)
            print("[INIT] Using local AI registry/feedback stores.")
        self.pdf_processor = AdvancedPDFProcessor()
        self.prepper = RAGPrepper(self.config)
        self.vector_db = PineconeManager(self.config, embedding_dimension=self.prepper.expected_embedding_dimension())
        self._sync_reextract_state_lock = threading.Lock()
        self._sync_reextract_last_run = {}
        self._sync_reextract_inflight = {}
        print("[INIT] Initialization complete")

    def _ingest_needs_processing(self, file_path, last_modified):
        cache = getattr(self, "ingest_registry_cache", None)
        if cache is not None:
            return cache.needs_processing(file_path, last_modified)
        return self.registry.needs_processing(file_path, last_modified)

    def _ingest_mark_processed(self, file_path, last_modified, resume_id):
        self.registry.upsert_file_record(file_path, last_modified, resume_id)
        cache = getattr(self, "ingest_registry_cache", None)
        if cache is not None:
            cache.upsert_file_record(file_path, last_modified, resume_id)

    def _ensure_sync_reextract_state(self):
        if not hasattr(self, "_sync_reextract_state_lock"):
            self._sync_reextract_state_lock = threading.Lock()
        if not hasattr(self, "_sync_reextract_last_run"):
            self._sync_reextract_last_run = {}
        if not hasattr(self, "_sync_reextract_inflight"):
            self._sync_reextract_inflight = {}

    def _active_v2_only_constraints(self, job_constraints):
        applied_constraints = set((job_constraints or {}).get("applied_constraints") or [])
        return applied_constraints & self.V2_ONLY_CONSTRAINT_IDS

    def _should_try_sync_reextract(self, candidate_facts, job_constraints):
        facts_version = str((candidate_facts or {}).get("facts_version") or self.FACTS_VERSION)
        return facts_version == "1.1" and bool(self._active_v2_only_constraints(job_constraints))

    def _synchronous_reextract_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        return self._build_candidate_facts(
            filename,
            rank,
            chunks,
            original_path=original_path,
            text_cache=text_cache,
            folder_metadata=folder_metadata,
        )

    def _sync_reextract_fallback_meta(self, reason, candidate_id, active_constraints, detail=""):
        return {
            "attempted": True,
            "succeeded": False,
            "fallback_reason": str(reason or ""),
            "candidate_id": str(candidate_id or ""),
            "active_constraints": sorted(active_constraints or []),
            "detail": str(detail or ""),
        }

    def _record_perf_timing(self, perf_state, key, elapsed_seconds):
        if not isinstance(perf_state, dict):
            return
        stats = perf_state.setdefault(key, {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0})
        stats["count"] += 1
        stats["total_seconds"] += float(elapsed_seconds or 0.0)
        stats["max_seconds"] = max(float(stats.get("max_seconds", 0.0) or 0.0), float(elapsed_seconds or 0.0))

    def _perf_snapshot(self, perf_state):
        snapshot = {}
        for key, stats in (perf_state or {}).items():
            count = int(stats.get("count", 0) or 0)
            total_seconds = float(stats.get("total_seconds", 0.0) or 0.0)
            snapshot[key] = {
                "count": count,
                "total_seconds": round(total_seconds, 3),
                "avg_seconds": round((total_seconds / count), 3) if count else 0.0,
                "max_seconds": round(float(stats.get("max_seconds", 0.0) or 0.0), 3),
            }
        return snapshot

    def _maybe_sync_reextract_candidate(
        self,
        candidate_facts,
        job_constraints,
        filename,
        rank,
        chunks,
        original_path,
        text_cache,
        folder_metadata,
        search_state,
        perf_state=None,
    ):
        sync_started_at = time.perf_counter()
        if not self._should_try_sync_reextract(candidate_facts, job_constraints):
            self._record_perf_timing(perf_state, "sync_reextract_gate_skipped", time.perf_counter() - sync_started_at)
            return candidate_facts, None

        self._ensure_sync_reextract_state()
        active_constraints = self._active_v2_only_constraints(job_constraints)
        candidate_id = str((candidate_facts or {}).get("candidate_id") or filename or "").strip() or str(filename or "")
        now_ts = time.time()

        with self._sync_reextract_state_lock:
            last_run = float(self._sync_reextract_last_run.get(candidate_id, 0) or 0)
            if last_run and (now_ts - last_run) < self.SYNC_REEXTRACT_COOLDOWN_SECONDS:
                self._record_perf_timing(perf_state, "sync_reextract_cooldown", time.perf_counter() - sync_started_at)
                return candidate_facts, self._sync_reextract_fallback_meta(
                    "cooldown",
                    candidate_id,
                    active_constraints,
                    "Candidate was synchronously re-extracted within the last 24 hours.",
                )

            if int(search_state.get("sync_reextract_count", 0)) >= self.SYNC_REEXTRACT_PER_SEARCH_LIMIT:
                self._record_perf_timing(perf_state, "sync_reextract_per_search_limit", time.perf_counter() - sync_started_at)
                return candidate_facts, self._sync_reextract_fallback_meta(
                    "per_search_limit",
                    candidate_id,
                    active_constraints,
                    "Per-search synchronous re-extraction limit reached.",
                )

            inflight = self._sync_reextract_inflight.get(candidate_id)
            is_owner = inflight is None
            if is_owner:
                inflight = {
                    "event": threading.Event(),
                    "result": None,
                    "error": None,
                }
                self._sync_reextract_inflight[candidate_id] = inflight
                search_state["sync_reextract_count"] = int(search_state.get("sync_reextract_count", 0)) + 1

        if not is_owner:
            completed = inflight["event"].wait(self.SYNC_REEXTRACT_WAIT_SECONDS)
            if completed and inflight.get("result") is not None:
                self._record_perf_timing(perf_state, "sync_reextract_waiter_success", time.perf_counter() - sync_started_at)
                return inflight["result"], {
                    "attempted": True,
                    "succeeded": True,
                    "mode": "waiter",
                    "candidate_id": candidate_id,
                    "active_constraints": sorted(active_constraints),
                }
            if completed and inflight.get("error") is not None:
                self._record_perf_timing(perf_state, "sync_reextract_waiter_failure", time.perf_counter() - sync_started_at)
                return candidate_facts, self._sync_reextract_fallback_meta(
                    "failure",
                    candidate_id,
                    active_constraints,
                    str(inflight.get("error")),
                )
            self._record_perf_timing(perf_state, "sync_reextract_concurrency_guard", time.perf_counter() - sync_started_at)
            return candidate_facts, self._sync_reextract_fallback_meta(
                "concurrency_guard",
                candidate_id,
                active_constraints,
                "Another search is already re-extracting this candidate.",
            )

        def _worker():
            try:
                result = self._synchronous_reextract_candidate_facts(
                    filename,
                    rank,
                    chunks,
                    original_path=original_path,
                    text_cache=text_cache,
                    folder_metadata=folder_metadata,
                )
                facts_version = str((result or {}).get("facts_version") or "")
                if facts_version != self.FACTS_VERSION:
                    raise RuntimeError(
                        f"Synchronous re-extraction did not produce {self.FACTS_VERSION} facts."
                    )
                inflight["result"] = result
                with self._sync_reextract_state_lock:
                    self._sync_reextract_last_run[candidate_id] = time.time()
            except Exception as exc:
                inflight["error"] = exc
            finally:
                inflight["event"].set()
                with self._sync_reextract_state_lock:
                    current = self._sync_reextract_inflight.get(candidate_id)
                    if current is inflight:
                        self._sync_reextract_inflight.pop(candidate_id, None)

        threading.Thread(target=_worker, daemon=True).start()
        completed = inflight["event"].wait(self.SYNC_REEXTRACT_TIMEOUT_SECONDS)
        if completed and inflight.get("result") is not None:
            self._record_perf_timing(perf_state, "sync_reextract_success", time.perf_counter() - sync_started_at)
            return inflight["result"], {
                "attempted": True,
                "succeeded": True,
                "mode": "owner",
                "candidate_id": candidate_id,
                "active_constraints": sorted(active_constraints),
            }
        if completed and inflight.get("error") is not None:
            self._record_perf_timing(perf_state, "sync_reextract_failure", time.perf_counter() - sync_started_at)
            return candidate_facts, self._sync_reextract_fallback_meta(
                "failure",
                candidate_id,
                active_constraints,
                str(inflight.get("error")),
            )
        self._record_perf_timing(perf_state, "sync_reextract_timeout", time.perf_counter() - sync_started_at)
        return candidate_facts, self._sync_reextract_fallback_meta(
            "timeout",
            candidate_id,
            active_constraints,
            "Synchronous re-extraction timed out.",
        )

    def _extract_age_constraint(self, user_prompt):
        prompt = str(user_prompt or "").strip().lower()
        if not prompt:
            return None

        range_patterns = [
            r'between\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})\s+years?\s+old',
            r'between\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'between\s+ages?\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'within\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'within\s+the\s+age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'with\s*in\s+the\s+age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'age\s+between\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'age\s+range\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'age\s+range\s+of\s+(\d{1,2})\s*-\s*(\d{1,2})',
            r'age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})\s+years?\s+old',
            r'ages?\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'aged?\s+(\d{1,2})\s*(?:-|to|and)\s*(\d{1,2})',
            r'(\d{1,2})\s*(?:-|to)\s*(\d{1,2})\s+years?\s+old',
        ]
        for pattern in range_patterns:
            match = re.search(pattern, prompt)
            if match:
                lower = int(match.group(1))
                upper = int(match.group(2))
                if lower > upper:
                    lower, upper = upper, lower
                return {"min_age": lower, "max_age": upper}

        # Generic age-context fallback for recruiter phrasing like
        # "should be with in the age of 30 and 50 years old".
        if any(token in prompt for token in (" age ", " ages ", "aged", "years old", "year old")):
            nums = [int(n) for n in re.findall(r'\b(\d{1,2})\b', prompt)]
            plausible = [n for n in nums if 18 <= n <= 80]
            if len(plausible) >= 2:
                lower, upper = plausible[0], plausible[1]
                if lower > upper:
                    lower, upper = upper, lower
                return {"min_age": lower, "max_age": upper}

        min_patterns = [
            r'at\s+least\s+(\d{1,2})\s+years?\s+old',
            r'older\s+than\s+(\d{1,2})',
            r'over\s+the\s+age\s+of\s+(\d{1,2})(?:\s+years?)?',
            r'over\s+(\d{1,2})',
            r'above\s+the\s+age\s+of\s+(\d{1,2})',
            r'above\s+(\d{1,2})',
            r'minimum\s+age\s+(?:of\s+)?(\d{1,2})',
            r'minimum\s+age\s+should\s+be\s+(\d{1,2})',
        ]
        for pattern in min_patterns:
            match = re.search(pattern, prompt)
            if match:
                value = int(match.group(1))
                matched_phrase = match.group(0).lower()
                if any(term in matched_phrase for term in ("older than", "over", "above")):
                    value += 1
                return {"min_age": value, "max_age": None}

        max_patterns = [
            r'up\s+to\s+(\d{1,2})\s+years?\s+old',
            r'younger\s+than\s+(\d{1,2})',
            r'below\s+the\s+age\s+of\s+(\d{1,2})',
            r'below\s+age\s+(\d{1,2})',
            r'less\s+than\s+(\d{1,2})\s+years?\s+old',
            r'not\s+more\s+than\s+(\d{1,2})\s+years?\s+old',
            r'under\s+(\d{1,2})',
            r'below\s+(\d{1,2})',
            r'maximum\s+age\s+(?:of\s+)?(\d{1,2})',
            r'maximum\s+age\s+should\s+be\s+(\d{1,2})',
        ]
        for pattern in max_patterns:
            match = re.search(pattern, prompt)
            if match:
                value = int(match.group(1))
                matched_phrase = match.group(0).lower()
                if any(term in matched_phrase for term in ("younger than", "under", "below", "less than")):
                    value -= 1
                return {"min_age": None, "max_age": value}

        return None

    def _extract_rank_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt.strip():
            return None

        seen = []
        for alias, entry in self.RANK_ALIAS_TABLE.items():
            if re.search(rf"\b{re.escape(alias)}\b", prompt, flags=re.IGNORECASE):
                canonical_id = entry["canonical_id"]
                if canonical_id not in seen:
                    seen.append(canonical_id)
        if not seen:
            return None
        return {
            "applied_rank_normalized": seen,
            "operator": "contains_any",
        }

    def _extract_coc_requirement_constraint(self, user_prompt):
        prompt = str(user_prompt or "").lower()
        patterns = [
            r"\bvalid\s+coc\b",
            r"\bcoc\s+required\b",
            r"\bcoc\s+mandatory\b",
            r"\bcoc\s+holder\b",
            r"\bvalid\s+certificate\s+of\s+competency\b",
            r"\bcertificate\s+of\s+competency\s+required\b",
            r"\bvalid\s+certificate\s+of\s+competency\s+required\b",
            r"\bmust\s+hold\s+valid\s+coc\b",
            r"\bmust\s+hold\s+coc\b",
        ]
        if any(re.search(pattern, prompt) for pattern in patterns):
            return {
                "coc_required": True,
                "coc_valid_required": True,
            }
        return None

    def _extract_coc_grade_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt.strip():
            return None

        rank_aliases = sorted(self.RANK_ALIAS_TABLE.items(), key=lambda item: len(item[0]), reverse=True)
        for alias, entry in rank_aliases:
            alias_pattern = re.escape(alias)
            patterns = [
                rf"\b{alias_pattern}(?:'s)?\s+coc\b",
                rf"\b{alias_pattern}(?:'s)?\s+certificate\s+of\s+competency\b",
                rf"\bcoc\b(?:\s+grade)?\s+(?:for\s+)?{alias_pattern}\b",
                rf"\bcertificate\s+of\s+competency\b(?:\s+grade)?\s+(?:for\s+)?{alias_pattern}\b",
            ]
            for pattern in patterns:
                match = re.search(pattern, prompt, flags=re.IGNORECASE)
                if match:
                    return {
                        "required_grades": [entry["canonical_id"]],
                        "operator": "contains_any",
                        "display_value": match.group(0).strip(),
                        "matched_span": match.span(),
                    }
        return None

    def _extract_stcw_basic_constraint(self, user_prompt):
        prompt = str(user_prompt or "").lower()
        patterns = [
            r"\bvalid\s+stcw\s+basic\b",
            r"\bstcw\s+basic\s+required\b",
            r"\bbasic\s+stcw\s+required\b",
            r"\ball\s+basic\s+stcw\s+required\b",
            r"\bmust\s+hold\s+all\s+basic\s+stcw\s+certificates\b",
            r"\bvalid\s+stcw\s+basic\s+required\b",
            r"\bmust\s+hold\s+valid\s+basic\s+stcw\b",
        ]
        if any(re.search(pattern, prompt) for pattern in patterns):
            return {"required": True}
        return None

    def _extract_min_sea_service_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        cleaned_prompt = self._strip_age_constraint_phrases(prompt)
        patterns = [
            r"\b(?:minimum|at\s+least)?\s*(\d+)\s*\+?\s*(years?|months?)\s*(?:of\s+)?(sea\s+service|sea\s+time|sailing\s+experience)\b",
            r"\b(?:minimum|at\s+least)?\s*(\d+)\s*\+?\s*(years?|months?)\s*(?:of\s+)?experience\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned_prompt, flags=re.IGNORECASE)
            if not match:
                continue
            value = int(match.group(1))
            unit = match.group(2).lower()
            months = value * 12 if unit.startswith("year") else value
            original_phrase = match.group(0).strip()
            return {"min_total_months": months, "display_value": original_phrase, "operator": "gte"}
        return None

    def _extract_recent_contract_vessel_experience_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt.strip():
            return None

        normalized_prompt = self._normalize_ship_type(prompt)
        ship_matches = self._extract_configured_ship_types(normalized_prompt)
        if not ship_matches:
            for canonical, aliases in self._ship_type_aliases().items():
                for alias in aliases:
                    normalized_alias = self._normalize_ship_type(alias)
                    if normalized_alias and re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_prompt):
                        ship_matches = [canonical]
                        break
                if ship_matches:
                    break
        if not ship_matches:
            return None

        if "experience" not in normalized_prompt and " on " not in normalized_prompt:
            return None

        contracts_match = re.search(r"\b(?:last|recent)\s+(\d+)\s+contracts?\b", normalized_prompt, flags=re.IGNORECASE)
        if not contracts_match:
            return None

        months_match = re.search(
            r"\b(?:minimum|at\s+least)?\s*(\d+)\s*(years?|months?)\b",
            normalized_prompt,
            flags=re.IGNORECASE,
        )
        min_months = 0
        if months_match:
            value = int(months_match.group(1))
            unit = months_match.group(2).lower()
            min_months = value * 12 if unit.startswith("year") else value
        lookback_contracts = int(contracts_match.group(1))
        vessel_type = ship_matches[0]
        requested_label = (
            f"{min_months} months experience on {vessel_type} in last {lookback_contracts} contracts"
            if min_months
            else f"{vessel_type} experience in last {lookback_contracts} contracts"
        )
        return {
            "vessel_type": vessel_type,
            "min_months": min_months,
            "lookback_contracts": lookback_contracts,
            "requested_label": requested_label,
            "display_value": " ".join(prompt.split()),
        }

    def _extract_ship_type_from_prompt(self, user_prompt):
        prompt = str(user_prompt or "")
        normalized_prompt = self._normalize_ship_type(prompt)
        ship_matches = self._extract_configured_ship_types(normalized_prompt)
        if ship_matches:
            return ship_matches[0]

        fallback_aliases = []
        for canonical, aliases in self._ship_type_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                if normalized_alias:
                    fallback_aliases.append((normalized_alias, canonical))
        for normalized_alias, canonical in sorted(fallback_aliases, key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_prompt):
                return normalized_alias if normalized_alias != canonical else canonical
        return None

    def _engine_type_aliases(self):
        aliases = {
            "man_b_w_me": [
                "MAN B&W",
                "MAN & B&W",
                "MAN B and W",
                "MAN BW",
                "MAN-B&W",
                "B&W",
                "ME engine",
                "ME engines",
                "ME-C",
                "ME-B",
                "MAN ME",
            ],
            "man_b_w_me_gi": [
                "ME-GI",
                "MEGI",
                "GI engine",
                "gas injection",
                "LNG ME-GI",
            ],
            "man_b_w_me_ga": [
                "ME-GA",
                "MEGA",
                "gas admission",
                "low pressure gas engine",
            ],
            "man_b_w_me_lgi": [
                "ME-LGI",
                "MELGI",
                "LGI",
                "liquid gas injection",
            ],
            "man_b_w_me_lgim": [
                "ME-LGIM",
                "MELGIM",
                "methanol engine",
                "methanol dual fuel",
            ],
            "man_b_w_me_lgip": [
                "ME-LGIP",
                "MELGIP",
                "LPG engine",
                "propane engine",
            ],
            "man_b_w_me_gie": [
                "ME-GIE",
                "ethane engine",
                "LEG engine",
            ],
            "wingd_x_df": [
                "WinGD X-DF",
                "X-DF",
                "XDF",
                "dual fuel WinGD",
                "LNG X-DF",
            ],
            "wingd_x_df_m": [
                "X-DF-M",
                "XDFM",
                "X-DF-M/E",
                "XDFME",
                "methanol WinGD",
                "methanol X-DF",
            ],
            "wingd_x_df_a": [
                "X-DF-A",
                "XDFA",
                "ammonia WinGD",
                "ammonia X-DF",
            ],
            "wingd_x_engines": [
                "X-Engine",
                "X-Engines",
                "WinGD X engine",
            ],
            "wartsila_dual_fuel": [
                "Wartsila DF",
                "Wärtsilä DF",
                "Wartsila dual fuel",
                "Wärtsilä dual fuel",
                "32DF",
                "34DF",
                "46DF",
                "50DF",
                "dual fuel Wartsila",
                "dual fuel Wärtsilä",
            ],
            "wartsila_rt_flex": [
                "RT-flex",
                "RT Flex",
                "Wartsila RT-flex",
                "Wärtsilä RT-flex",
                "Sulzer RT-flex",
                "Sulzer RT Flex",
            ],
            "mitsubishi_uec": [
                "Mitsubishi UEC",
                "UEC",
            ],
            "dual_fuel": [
                "dual fuel",
                "dual-fuel",
                "DF engine",
                "DF engines",
                "dual fuel engine",
                "dual fuel engines",
            ],
            "methanol_engine": [
                "methanol engine",
                "methanol fuel engine",
                "MeOH engine",
            ],
            "ammonia_engine": [
                "ammonia engine",
                "ammonia fuel engine",
                "NH3 engine",
            ],
        }
        runtime_config = getattr(getattr(self, "config", None), "config", None)
        if runtime_config and runtime_config.has_section("EngineTypes"):
            for canonical, raw_value in runtime_config.items("EngineTypes"):
                configured_aliases = [line.strip() for line in str(raw_value or "").splitlines() if line.strip()]
                if configured_aliases:
                    aliases[self._normalize_engine_type(canonical)] = configured_aliases
        return aliases

    def _normalize_engine_type(self, value):
        normalized = str(value or "").strip().lower()
        normalized = normalized.replace("wärtsilä", "wartsila")
        normalized = re.sub(r"[_/]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _engine_alias_matches_text(self, normalized_text, alias):
        normalized_alias = self._normalize_engine_type(alias)
        if not normalized_text or not normalized_alias:
            return False
        if normalized_alias in {"me", "df", "gi", "lgi"}:
            return False
        if normalized_alias in {"me engine", "me engines"}:
            return bool(re.search(r"\bme\s+engines?\b", normalized_text))
        alias_pattern = re.escape(normalized_alias)
        alias_pattern = alias_pattern.replace(r"\ ", r"[\s./()&-]+")
        return bool(re.search(rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])", normalized_text))

    def _extract_engine_types_from_text(self, raw_text):
        text = self._normalize_engine_type(raw_text)
        if not text:
            return []
        matches = []
        for canonical, aliases in self._engine_type_aliases().items():
            canonical_id = self._normalize_engine_type(canonical).replace(" ", "_")
            for alias in aliases:
                if self._engine_alias_matches_text(text, alias):
                    matches.append(canonical_id)
                    break
        return list(dict.fromkeys(matches))

    def _engine_type_expected_values(self, requested_engine_type):
        requested = self._normalize_engine_type(requested_engine_type).replace(" ", "_")
        if not requested:
            return []
        expanded = {
            "man_b_w_me": [
                "man_b_w_me",
                "man_b_w_me_gi",
                "man_b_w_me_ga",
                "man_b_w_me_lgi",
                "man_b_w_me_lgim",
                "man_b_w_me_lgip",
                "man_b_w_me_gie",
            ],
            "dual_fuel": [
                "dual_fuel",
                "man_b_w_me_gi",
                "man_b_w_me_ga",
                "man_b_w_me_lgi",
                "man_b_w_me_lgim",
                "man_b_w_me_lgip",
                "man_b_w_me_gie",
                "wingd_x_df",
                "wingd_x_df_m",
                "wingd_x_df_a",
                "wartsila_dual_fuel",
                "methanol_engine",
                "ammonia_engine",
            ],
            "methanol_engine": [
                "methanol_engine",
                "man_b_w_me_lgim",
                "wingd_x_df_m",
            ],
            "ammonia_engine": [
                "ammonia_engine",
                "wingd_x_df_a",
            ],
        }
        return expanded.get(requested, [requested])

    def _extract_engine_experience_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        normalized_prompt = self._normalize_engine_type(prompt)
        if not normalized_prompt:
            return None

        matches = []
        for canonical, aliases in self._engine_type_aliases().items():
            canonical_id = self._normalize_engine_type(canonical).replace(" ", "_")
            for alias in aliases:
                normalized_alias = self._normalize_engine_type(alias)
                if normalized_alias == "b&w":
                    continue
                if normalized_alias == "me engine" and re.search(r"\bme\s+engine(?:s)?\b", normalized_prompt):
                    matches.append(canonical_id)
                    break
                if self._engine_alias_matches_text(normalized_prompt, alias):
                    matches.append(canonical_id)
                    break

        if not matches:
            return None
        for generic_engine_type in ("methanol_engine", "ammonia_engine"):
            if generic_engine_type in matches:
                matches = [generic_engine_type] + [match for match in matches if match != generic_engine_type]
                break
        engine_type = matches[0]
        contract_patterns = [
            r"\b(?:last|recent|latest)\s+(\d+)\s+(?:contracts?|vessels?|ships?)\b",
            r"\b(?:contracts?|vessels?|ships?)\s+(?:last|recent|latest)\s+(\d+)\b",
            r"\b(?:in|within|across|during)\s+(?:the\s+)?(?:last|recent|latest)\s+(\d+)\s+(?:contracts?|vessels?|ships?)\b",
        ]
        contracts_match = None
        for pattern in contract_patterns:
            contracts_match = re.search(pattern, normalized_prompt, flags=re.IGNORECASE)
            if contracts_match:
                break
        recent_contract_match_mode = "any"
        if contracts_match:
            strict_contract_patterns = [
                r"\ball\s+(?:of\s+)?(?:the\s+)?(?:last|recent|latest)\s+\d+\s+(?:contracts?|vessels?|ships?)\b",
                r"\b(?:last|recent|latest)\s+\d+\s+(?:contracts?|vessels?|ships?)\s+(?:with|on|fitted\s+with|should\s+(?:be|have)|must\s+(?:be|have)|need(?:s)?\s+to\s+(?:be|have))\b",
                r"\b(?:contracts?|vessels?|ships?)\s+(?:last|recent|latest)\s+\d+\s+(?:with|on|fitted\s+with|should\s+(?:be|have)|must\s+(?:be|have))\b",
            ]
            if any(re.search(pattern, normalized_prompt, flags=re.IGNORECASE) for pattern in strict_contract_patterns):
                recent_contract_match_mode = "all"
        months_match = re.search(
            r"\b(?:minimum|at\s+least)?\s*(\d+)\s*(years?|months?)\b",
            normalized_prompt,
            flags=re.IGNORECASE,
        )
        min_months = 0
        if months_match:
            value = int(months_match.group(1))
            unit = months_match.group(2).lower()
            min_months = value * 12 if unit.startswith("year") else value
        has_engine_intent = any(token in normalized_prompt for token in (
            "experience",
            "experienced",
            "engine",
            "engines",
            "worked",
            "sailed",
            "served",
            "handled",
            "background",
            "machinery",
            "fitted",
            "must have",
            "need",
            "needs",
            "should be",
            "should have",
            "only",
            "with",
            "has",
        ))
        if not has_engine_intent and not contracts_match and min_months == 0:
            return None
        return {
            "engine_type": engine_type,
            "expected_values": self._engine_type_expected_values(engine_type),
            "min_months": min_months,
            "lookback_contracts": int(contracts_match.group(1)) if contracts_match else 0,
            "recent_contract_match_mode": recent_contract_match_mode,
            "display_value": " ".join(prompt.split()),
            "operator": "contains_any",
        }

    def _extract_engine_vessel_experience_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        normalized_prompt = self._normalize_engine_type(prompt)
        if not normalized_prompt:
            return None

        # Keep explicit "engine experience and vessel experience" prompts as two
        # independent constraints. Combined rules are for same-row phrasing.
        if re.search(r"\bexperience\b.*\band\b.*\bexperience\b", normalized_prompt):
            return None

        engine_constraint = self._extract_engine_experience_constraint(prompt)
        vessel_type = self._extract_ship_type_from_prompt(prompt)
        if not engine_constraint or not vessel_type:
            return None

        same_row_intent = (
            " and " not in normalized_prompt
            or any(token in normalized_prompt for token in (" on ", " with ", "fitted", "machinery"))
        )
        if not same_row_intent:
            return None

        return {
            "engine_type": engine_constraint["engine_type"],
            "expected_engine_values": engine_constraint["expected_values"],
            "vessel_type": vessel_type,
            "expected_vessel_values": self._ship_type_expected_values(vessel_type),
            "min_months": engine_constraint.get("min_months", 0),
            "lookback_contracts": engine_constraint.get("lookback_contracts", 0),
            "recent_contract_match_mode": engine_constraint.get("recent_contract_match_mode", "any"),
            "display_value": " ".join(prompt.split()),
            "operator": "contains_all_same_row",
        }

    def _extract_company_continuity_constraint(self, user_prompt):
        prompt = " ".join(str(user_prompt or "").split())
        if not prompt:
            return None

        patterns = [
            (r"\b(?:same|one)\s+(?:company|employer)\s+for\s+more\s+than\s+(\d+)\s+contracts?\b", "gt"),
            (r"\bmore\s+than\s+(\d+)\s+contracts?\s+(?:in|with|under|for)\s+(?:one|same)\s+(?:company|employer)\b", "gt"),
            (r"\b(?:at\s+least|minimum)\s+(\d+)\s+contracts?\s+(?:in|with|under|for)\s+(?:one|same)\s+(?:company|employer)\b", "gte"),
            (r"\bserved\s+(?:at\s+least|minimum)\s+(\d+)\s+contracts?\s+(?:in|with|under|for)\s+(?:one|same)\s+(?:company|employer)\b", "gte"),
            (r"\b(?:same|one)\s+(?:company|employer)\s+(?:for|with|at\s+least|minimum)\s+(\d+)\s+contracts?\b", "gte"),
            (r"\bhas\s+worked\s+(?:for|with|under|in)\s+(?:a|one|same)\s+(?:company|employer)\s+for\s+more\s+than\s+(\d+)\s+contracts?\b", "gt"),
            (r"\bhas\s+worked\s+(?:for|with|under|in)\s+(?:a|one|same)\s+(?:company|employer)\s+for\s+(\d+)\s+contracts?\b", "gte"),
            (r"\bworked\s+(?:for|with|under|in)\s+(?:a|one|same)\s+(?:company|employer)\s+for\s+more\s+than\s+(\d+)\s+contracts?\b", "gt"),
            (r"\bworked\s+(?:for|with|under|in)\s+(?:a|one|same)\s+(?:company|employer)\s+for\s+(\d+)\s+contracts?\b", "gte"),
        ]
        for pattern, mode in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if not match:
                continue
            value = int(match.group(1))
            minimum = value + 1 if mode == "gt" else value
            return {
                "min_same_company_contract_count": minimum,
                "display_value": match.group(0).strip(),
                "operator": "gte",
            }
        return None

    def _extract_recency_constraint(self, user_prompt):
        prompt = " ".join(str(user_prompt or "").split())
        if not prompt:
            return None

        patterns = [
            r"\bsigned\s+off\s+in\s+last\s+(\d+)\s+months?\b",
            r"\bsigned\s+off\s+within\s+(\d+)\s+months?\b",
            r"\blast\s+sign(?:ed)?\s+off\s+within\s+(\d+)\s+months?\b",
            r"\blast\s+sign(?:ed)?\s+off\s+in\s+last\s+(\d+)\s+months?\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if match:
                months = int(match.group(1))
                return {
                    "max_months_since_sign_off": months,
                    "display_value": match.group(0).strip(),
                    "operator": "lte",
                }
        return None

    def _extract_vessel_type_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        configured_matches = self._extract_configured_ship_types(prompt)
        if configured_matches:
            return {"required": configured_matches, "display_value": ", ".join(configured_matches), "operator": "contains_any"}

        # Configured labels returned nothing; fall back to hardcoded alias table.
        if not self._configured_ship_type_labels():
            print("[WARN] _extract_vessel_type_constraint: no configured ship-type labels found; "
                  "using hardcoded fallback aliases. Add [ShipTypes] ship_type_options to config.ini.")
        lowered = prompt.lower()
        seen = []
        for canonical, aliases in self._ship_type_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                if not normalized_alias:
                    continue
                alias_pattern = re.escape(normalized_alias).replace(r"\ ", r"\s+")
                # Treat softened forms like "VLCC-ish" as unclassified noise, not a clean constraint hit.
                if re.search(rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9-])", lowered, flags=re.IGNORECASE):
                    if canonical not in seen:
                        seen.append(canonical)
                    break
        if not seen:
            return None
        return {"required": seen, "display_value": ", ".join(seen), "operator": "contains_any"}

    def _extract_availability_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        immediate_match = re.search(r"\b(available immediately|join immediately)\b", prompt, flags=re.IGNORECASE)
        if immediate_match:
            return {"value_type": "status", "status": "immediately", "display_value": immediate_match.group(1).lower()}

        date_match = re.search(r"\bavailable\s+from\s+([A-Za-z]{3,9}\s+\d{1,2}(?:,\s*\d{4})?)\b", prompt, flags=re.IGNORECASE)
        if date_match:
            raw_date = date_match.group(1).strip()
            date_fact = self._extract_date_fact_from_snippet(raw_date)
            if date_fact.get("status") != "PARSED" and re.fullmatch(r"[A-Za-z]{3,9}\s+\d{1,2}", raw_date):
                candidate_fact = self._extract_date_fact_from_snippet(f"{raw_date}, {date.today().year}")
                parsed_candidate = candidate_fact.get("date")
                if candidate_fact.get("status") == "PARSED" and parsed_candidate:
                    if parsed_candidate < date.today():
                        candidate_fact = self._extract_date_fact_from_snippet(f"{raw_date}, {date.today().year + 1}")
                    date_fact = candidate_fact
            if date_fact.get("status") == "PARSED":
                return {
                    "value_type": "date",
                    "available_from_date": date_fact["date"].isoformat(),
                    "display_value": date_match.group(0).strip(),
                }

        relative_match = re.search(r"\bjoinable\s+in\s+(\d+)\s+days\b", prompt, flags=re.IGNORECASE)
        if relative_match:
            return {
                "value_type": "relative_phrase",
                "relative_days": int(relative_match.group(1)),
                "relative_display_value": f"in {relative_match.group(1)} days",
                "display_value": relative_match.group(0).strip().lower(),
            }
        return None

    def _extract_endorsement_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        mappings = [
            (
                r"\badvanced\s+igf\s+(?:cop|certificate(?:\s+of\s+proficiency)?)\b|\bigf\s+advanced\s+(?:cop|certificate(?:\s+of\s+proficiency)?)\b|\badvanced\s+training\b.{0,80}\bigf\s+code\b",
                "igf_advanced_cop",
                "advanced IGF CoP",
            ),
            (
                r"\bbasic\s+igf\s+(?:cop|certificate(?:\s+of\s+proficiency)?)\b|\bigf\s+basic\s+(?:cop|certificate(?:\s+of\s+proficiency)?)\b|\bbasic\s+training\b.{0,80}\bigf\s+code\b",
                "igf_basic_cop",
                "basic IGF CoP",
            ),
            (
                r"\badvanced\s+oil\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\boil\s+tanker\s+(?:advanced|management)\b|\boil\s+(?:tanker\s+)?dce?\s+management\b|\boil\s+tanker\s+dc\s+management\b",
                "tanker_oil_advanced_cop",
                "advanced oil tanker CoP",
            ),
            (
                r"\bbasic\s+oil\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\boil\s+tanker\s+(?:basic|support)\b|\boil\s+(?:tanker\s+)?dce?\s+support\b|\boil\s+tanker\s+dc\s+support\b",
                "tanker_oil_basic_cop",
                "basic oil tanker CoP",
            ),
            (
                r"\badvanced\s+chemical\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\bchemical\s+tanker\s+(?:advanced|management)\b|\bchemical\s+(?:tanker\s+)?dce?\s+management\b|\bchemical\s+tanker\s+dc\s+management\b",
                "tanker_chemical_advanced_cop",
                "advanced chemical tanker CoP",
            ),
            (
                r"\bbasic\s+chemical\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\bchemical\s+tanker\s+(?:basic|support)\b|\bchemical\s+(?:tanker\s+)?dce?\s+support\b|\bchemical\s+tanker\s+dc\s+support\b",
                "tanker_chemical_basic_cop",
                "basic chemical tanker CoP",
            ),
            (
                r"\badvanced\s+(?:gas|liquefied\s+gas)\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\b(?:gas|liquefied\s+gas)\s+tanker\s+(?:advanced|management)\b|\bgas\s+(?:tanker\s+)?dce?\s+management\b|\bgas\s+tanker\s+dc\s+management\b",
                "tanker_gas_advanced_cop",
                "advanced gas tanker CoP",
            ),
            (
                r"\bbasic\s+(?:gas|liquefied\s+gas)\s+tanker\s+(?:cop|certificate(?:\s+of\s+proficiency)?|endorsement)\b|\b(?:gas|liquefied\s+gas)\s+tanker\s+(?:basic|support)\b|\bgas\s+(?:tanker\s+)?dce?\s+support\b|\bgas\s+tanker\s+dc\s+support\b",
                "tanker_gas_basic_cop",
                "basic gas tanker CoP",
            ),
            (r"\becdis\b|\belectronic\s+chart\s+display(?:\s+and\s+information\s+system)?\b", "cert_ecdis", "ECDIS"),
            (r"\barpa\b|\bautomatic\s+radar\s+plotting\s+aid\b", "cert_arpa", "ARPA"),
            (r"\bbrm\b|\bbtm\b|\bbridge\s+resource\s+management\b|\bbridge\s+team\s+management\b", "cert_brm_btm", "BRM/BTM"),
            (r"\berm\b|\bengine(?:\s+room)?\s+resource\s+management\b", "cert_erm", "ERM"),
            (r"\bpscrb\b|\bproficiency\s+in\s+survival\s+craft(?:\s+and\s+rescue\s+boats?)?\b|\bsurvival\s+craft\s+and\s+rescue\s+boats?\b", "cert_pscrb", "PSCRB"),
            (r"\baff\b|\badvanced?\s+fire\s+fighting\b", "cert_aff", "AFF"),
            (r"\bmfa\b|\bmedical\s+first\s+aid\b", "cert_mfa", "MFA"),
            (r"\bmedical\s+care\b", "cert_medical_care", "Medical Care"),
            (r"\bsso\b|\bship\s+security\s+officer\b", "cert_sso", "SSO"),
            (r"\bdpo\b|\bdp operator\b", "dp_operational", "DPO"),
            (r"\bgmdss\b", "gmdss", "GMDSS"),
            (r"\boil tanker endorsement\b|\boil tanker dce?\b|\boil tanker dc\b|\boil dc\b", "tanker_oil", "oil tanker endorsement"),
            (r"\bchemical tanker endorsement\b|\bchemical tanker dce?\b|\bchemical tanker dc\b|\bchemical dc\b", "tanker_chemical", "chemical tanker endorsement"),
            (r"\bgas tanker endorsement\b|\bgas tanker dce?\b|\bgas tanker dc\b|\bgas dc\b", "tanker_gas", "gas tanker endorsement"),
        ]
        matches = []
        for pattern, canonical_id, display_value in mappings:
            if re.search(pattern, prompt, flags=re.IGNORECASE):
                matches.append((canonical_id, display_value))
        if matches:
            ordered = list(dict.fromkeys(matches))
            specific_to_generic = {
                "tanker_oil_basic_cop": "tanker_oil",
                "tanker_oil_advanced_cop": "tanker_oil",
                "tanker_chemical_basic_cop": "tanker_chemical",
                "tanker_chemical_advanced_cop": "tanker_chemical",
                "tanker_gas_basic_cop": "tanker_gas",
                "tanker_gas_advanced_cop": "tanker_gas",
            }
            specific_ids = {canonical_id for canonical_id, _display in ordered if canonical_id in specific_to_generic}
            generic_ids_to_drop = {specific_to_generic[canonical_id] for canonical_id in specific_ids}
            ordered = [
                (canonical_id, display)
                for canonical_id, display in ordered
                if canonical_id not in generic_ids_to_drop
            ]
            return {
                "endorsements_required": [canonical_id for canonical_id, _display in ordered],
                "display_value": " and ".join(display for _canonical_id, display in ordered),
            }

        if re.search(r"\btanker endorsement\b", prompt, flags=re.IGNORECASE):
            return {"ambiguous": True, "fragment": "tanker endorsement"}
        if re.search(r"\bigf\s+(?:cop|certificate(?:\s+of\s+proficiency)?)\b", prompt, flags=re.IGNORECASE):
            return {"ambiguous": True, "fragment": "IGF CoP"}
        if re.search(r"\bDP2\b|\bDP3\b|\bDP\b", prompt):
            return {"ambiguous": True, "fragment": re.search(r"\bDP2\b|\bDP3\b|\bDP\b", prompt).group(0)}
        return None

    def _rank_certificate_expectations(self):
        deck_common = [
            "gmdss",
            "cert_arpa",
            "cert_pscrb",
            "cert_mfa",
            "cert_sso",
        ]
        engine_common = [
            "cert_erm",
            "cert_pscrb",
            "cert_mfa",
            "cert_aff",
        ]
        return {
            "master": deck_common + ["cert_medical_care"],
            "chief_officer": deck_common + ["cert_medical_care"],
            "2nd_officer": deck_common,
            "3rd_officer": deck_common,
            "chief_engineer": engine_common,
            "2nd_engineer": engine_common,
        }

    def _extract_rank_certificate_expectation_constraint(self, user_prompt, rank=None):
        prompt = str(user_prompt or "")
        normalized_prompt = self._normalize_rank_label(prompt)
        if not normalized_prompt:
            return None

        intent_patterns = [
            r"\b(?:rank\s+)?(?:required|mandatory|standard|expected)\s+(?:course\s+)?cert(?:ificate)?s\b",
            r"\brequired\s+cert(?:ificate)?s\s+for\s+(?:the\s+)?rank\b",
            r"\bcert(?:ificate)?s\s+for\s+(?:the\s+)?(?:rank|role)\b",
            r"\brank\s+cert(?:ificate)?\s+check\b",
        ]
        if not any(re.search(pattern, normalized_prompt, flags=re.IGNORECASE) for pattern in intent_patterns):
            return None

        rank_constraint = self._extract_rank_constraint(prompt)
        rank_id = None
        if rank_constraint:
            prompt_ranks = rank_constraint.get("applied_rank_normalized") or []
            if len(prompt_ranks) == 1:
                rank_id = prompt_ranks[0]
            elif len(prompt_ranks) > 1:
                return {"ambiguous": True, "fragment": "rank certificates"}

        if not rank_id:
            rank_id, _department, _seniority_bucket, _confidence = self._normalize_rank(rank)

        expectations = self._rank_certificate_expectations()
        required = expectations.get(rank_id)
        if not rank_id or not required:
            return {"ambiguous": True, "fragment": "rank certificates"}

        return {
            "endorsements_required": list(required),
            "display_value": f"standard {rank_id.replace('_', ' ')} certificates",
        }

    def _extract_job_constraints(self, user_prompt, rank=None):
        constraints = {
            "rank": str(rank or "").strip(),
            "hard_constraints": {},
            "applied_constraints": [],
            "unapplied_constraints": [],
            "parsing_notes": [],
        }
        age_constraint = self._extract_age_constraint(user_prompt)
        if age_constraint:
            constraints["hard_constraints"]["age_years"] = age_constraint
            constraints["applied_constraints"].append("age_range")

        rank_prompt = str(user_prompt or "")
        coc_grade_constraint = self._extract_coc_grade_constraint(user_prompt)
        if coc_grade_constraint:
            matched_span = coc_grade_constraint.get("matched_span")
            if matched_span and len(matched_span) == 2:
                start, end = matched_span
                rank_prompt = f"{rank_prompt[:start]} {rank_prompt[end:]}"

        rank_constraint = self._extract_rank_constraint(rank_prompt)
        if rank_constraint:
            constraints["hard_constraints"]["rank"] = rank_constraint
            constraints["applied_constraints"].append("rank_match")

        visa_constraint = self._extract_us_visa_constraint(user_prompt)
        if visa_constraint:
            constraints["hard_constraints"]["us_visa"] = visa_constraint
            constraints["applied_constraints"].append("us_visa")

        passport_constraint = self._extract_passport_validity_constraint(user_prompt)
        if passport_constraint:
            constraints["hard_constraints"]["passport_validity"] = passport_constraint
            constraints["applied_constraints"].append("passport_validity")

        coc_constraint = self._extract_coc_requirement_constraint(user_prompt)
        if coc_constraint:
            constraints["hard_constraints"]["certifications"] = coc_constraint
            constraints["applied_constraints"].append("coc_document_gate")

        if coc_grade_constraint:
            coc_grade_constraint = dict(coc_grade_constraint)
            coc_grade_constraint.pop("matched_span", None)
            constraints["hard_constraints"]["coc_grade"] = coc_grade_constraint
            constraints["applied_constraints"].append("coc_grade_match")

        stcw_constraint = self._extract_stcw_basic_constraint(user_prompt)
        if stcw_constraint:
            constraints["hard_constraints"]["stcw_basic"] = stcw_constraint
            constraints["applied_constraints"].append("stcw_basic")

        company_continuity_constraint = self._extract_company_continuity_constraint(user_prompt)
        if company_continuity_constraint:
            constraints["hard_constraints"]["company_continuity"] = company_continuity_constraint
            constraints["applied_constraints"].append("company_continuity")

        engine_vessel_experience_constraint = self._extract_engine_vessel_experience_constraint(user_prompt)
        if engine_vessel_experience_constraint:
            constraints["hard_constraints"]["engine_vessel_experience"] = engine_vessel_experience_constraint
            constraints["applied_constraints"].append("engine_vessel_experience")

        recent_contract_vessel_experience_constraint = None if engine_vessel_experience_constraint else self._extract_recent_contract_vessel_experience_constraint(user_prompt)
        if recent_contract_vessel_experience_constraint:
            constraints["hard_constraints"]["recent_contract_vessel_experience"] = recent_contract_vessel_experience_constraint
            constraints["applied_constraints"].append("recent_contract_vessel_experience")

        engine_experience_constraint = None if engine_vessel_experience_constraint else self._extract_engine_experience_constraint(user_prompt)
        if engine_experience_constraint:
            constraints["hard_constraints"]["engine_experience"] = engine_experience_constraint
            constraints["applied_constraints"].append("engine_experience")

        experienced_ship_type = None if (engine_vessel_experience_constraint or recent_contract_vessel_experience_constraint or engine_experience_constraint) else self._extract_experience_ship_type_constraint(user_prompt)
        if experienced_ship_type:
            constraints["hard_constraints"]["experience_ship_type"] = experienced_ship_type
            constraints["applied_constraints"].append("experience_ship_type")

        recency_constraint = self._extract_recency_constraint(user_prompt)
        if recency_constraint:
            constraints["hard_constraints"]["recency"] = recency_constraint
            constraints["applied_constraints"].append("recency")

        sea_service_constraint = None if (engine_vessel_experience_constraint or recent_contract_vessel_experience_constraint or engine_experience_constraint) else self._extract_min_sea_service_constraint(user_prompt)
        if sea_service_constraint:
            constraints["hard_constraints"]["sea_service"] = sea_service_constraint
            constraints["unapplied_constraints"].append("min_sea_service")

        vessel_type_constraint = None if (engine_vessel_experience_constraint or recent_contract_vessel_experience_constraint) else self._extract_vessel_type_constraint(user_prompt)
        if vessel_type_constraint:
            constraints["hard_constraints"]["vessel_type"] = vessel_type_constraint
            constraints["unapplied_constraints"].append("vessel_type")

        availability_constraint = self._extract_availability_constraint(user_prompt)
        if availability_constraint:
            constraints["hard_constraints"]["availability"] = availability_constraint
            constraints["applied_constraints"].append("availability")

        endorsement_constraint = self._extract_endorsement_constraint(user_prompt)
        if endorsement_constraint:
            if endorsement_constraint.get("ambiguous"):
                constraints["parsing_notes"].append(endorsement_constraint["fragment"])
            else:
                certs = constraints["hard_constraints"].setdefault("certifications", {})
                certs["endorsements_required"] = endorsement_constraint["endorsements_required"]
                certs["endorsement_display_value"] = endorsement_constraint["display_value"]
                constraints["applied_constraints"].append("stcw_endorsement")

        rank_certificate_constraint = self._extract_rank_certificate_expectation_constraint(user_prompt, rank=rank)
        if rank_certificate_constraint:
            if rank_certificate_constraint.get("ambiguous"):
                constraints["parsing_notes"].append(rank_certificate_constraint["fragment"])
            else:
                certs = constraints["hard_constraints"].setdefault("certifications", {})
                existing = certs.get("endorsements_required") or []
                combined = list(dict.fromkeys([*existing, *rank_certificate_constraint["endorsements_required"]]))
                certs["endorsements_required"] = combined
                existing_display = certs.get("endorsement_display_value")
                certs["endorsement_display_value"] = (
                    f"{existing_display} and {rank_certificate_constraint['display_value']}"
                    if existing_display
                    else rank_certificate_constraint["display_value"]
                )
                constraints["applied_constraints"].append("stcw_endorsement")

        if not constraints["applied_constraints"] and not constraints["unapplied_constraints"] and str(user_prompt or "").strip():
            constraints["parsing_notes"].append(str(user_prompt).strip())

        constraints["applied_constraints"] = list(dict.fromkeys(constraints["applied_constraints"]))
        constraints["unapplied_constraints"] = list(dict.fromkeys(constraints["unapplied_constraints"]))
        constraints["parsing_notes"] = list(dict.fromkeys(note for note in constraints["parsing_notes"] if note))
        return constraints

    def _normalize_ship_type(self, ship_type):
        normalized = str(ship_type or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _normalize_rank_label(self, raw_rank):
        normalized = str(raw_rank or "").strip().lower()
        normalized = re.sub(r'(?<=\b[a-z])[./](?=[a-z]\b)', '', normalized)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _normalize_rank(self, raw_rank):
        normalized_rank = self._normalize_rank_label(raw_rank)
        alias_entry = self.RANK_ALIAS_TABLE.get(normalized_rank)
        if not alias_entry:
            return None, None, None, None

        canonical_id = alias_entry["canonical_id"]
        department = alias_entry["department"]
        seniority_bucket = alias_entry["seniority_bucket"]
        confidence = 1.0 if normalized_rank.replace(" ", "_") == canonical_id else 0.9
        return canonical_id, department, seniority_bucket, confidence

    def _extract_rank_fact_from_text(self, text):
        source_text = str(text or "")
        if not source_text.strip():
            return {
                "raw_rank": None,
                "canonical_id": None,
                "department": None,
                "seniority_bucket": None,
                "confidence": None,
                "status": "MISSING",
                "extraction_method": "labeled_field_regex",
                "source_label": None,
            }

        present_patterns = [
            r"(?i)\bpresent\s+rank\s*[:\-]?\s*(.+?)(?=\s+(?:from\s+date|till\s+date|personal(?:\s*&\s*contact)?\s+details|name|email|availability\s+details)\b|$)",
            r"(?im)^(?:current|present)\s+rank\s*[:\-]\s*([^\n,]+)",
            r"(?i)\bappraisee['’]s\s+rank\s*[:\-]?\s*(.+?)(?=\s+(?:department\s+head|highest\s+license\s+held|master|nationality|this\s+appraisal)\b|$)",
            r"(?i)\bposition\s*[:\-]?\s*(.+?)(?=\s+(?:desired\s+type\s+of\s+ship|full\s+name|available|date\s+of\s+birth|citizenship|place\s+of\s+birth|phones?|email|address)\b|$)",
            r"(?im)^rank\s*[:\-]\s*([^\n,]+)",
        ]
        applied_patterns = [
            r"(?i)\bapplied\s+for\s+rank\s*[:\-]?\s*(.+?)(?=\s+(?:present\s+rank|from\s+date|till\s+date|personal(?:\s*&\s*contact)?\s+details|name|email|availability\s+details)\b|$)",
            r"(?i)\bpost\s+applied\s+for\s*[:\-]*\s*(.+?)(?=\s*(?:\W{0,4})?(?:name|father(?:'s)?\s+name|date(?:\s*&)?\s+place\s+of\s+birth|date\s+of\s+birth|nationality|marital\s+status|gender|religion|language|languages\s+known|email|phone|mobile|contact|address|objective|personal\s+details|documents|pre[\s\-]?sea|sea\s+experience|work\s+experience)\b|$)",
            r"(?i)\bpost\s+applied\s*[:\-]*\s*(.+?)(?=\s*(?:\W{0,4})?(?:name|surname|first\s+name|middle\s+name|father(?:'s)?\s+name|date(?:\s*&)?\s+place\s+of\s+birth|date\s+of\s+birth|nationality|marital\s+status|gender|religion|language|languages\s+known|email|phone|mobile|contact|address|objective|personal\s+details|documents|pre[\s\-]?sea|sea\s+experience|work\s+experience)\b|$)",
            r"(?i)\bapplied\s+for\s*[:\-]*\s*(.+?)(?=\s*(?:\W{0,4})?(?:name|father(?:'s)?\s+name|date(?:\s*&)?\s+place\s+of\s+birth|date\s+of\s+birth|nationality|marital\s+status|gender|religion|language|languages\s+known|email|phone|mobile|contact|address|objective|personal\s+details|documents|pre[\s\-]?sea|sea\s+experience|work\s+experience|academic\s+performance)\b|$)",
        ]

        def _normalize_raw_rank(raw_rank):
            cleaned = re.sub(r"\s+", " ", str(raw_rank or "").strip(" ,:-"))
            canonical_id, department, seniority_bucket, confidence = self._normalize_rank(cleaned)
            if canonical_id:
                return cleaned, canonical_id, department, seniority_bucket, confidence

            normalized_cleaned = self._normalize_rank_label(cleaned)
            tokens = normalized_cleaned.split()
            for end in range(len(tokens), 0, -1):
                candidate_label = " ".join(tokens[:end])
                alias_entry = self.RANK_ALIAS_TABLE.get(candidate_label)
                if not alias_entry:
                    continue
                return (
                    cleaned[: len(" ".join(cleaned.split()[:end]))].strip(" ,:-"),
                    alias_entry["canonical_id"],
                    alias_entry["department"],
                    alias_entry["seniority_bucket"],
                    0.85,
                )
            return cleaned, canonical_id, department, seniority_bucket, confidence

        present_unknown = None
        for pattern in present_patterns:
            match = re.search(pattern, source_text)
            if not match:
                continue
            raw_rank, canonical_id, department, seniority_bucket, confidence = _normalize_raw_rank(match.group(1))
            if canonical_id:
                return {
                    "raw_rank": raw_rank,
                    "canonical_id": canonical_id,
                    "department": department,
                    "seniority_bucket": seniority_bucket,
                    "confidence": confidence,
                    "status": "PARSED",
                    "extraction_method": "labeled_field_regex",
                    "source_label": "current_rank",
                }
            present_unknown = {
                "raw_rank": raw_rank,
                "canonical_id": None,
                "department": None,
                "seniority_bucket": None,
                "confidence": None,
                "status": "UNKNOWN",
                "extraction_method": "labeled_field_regex",
                "source_label": "current_rank",
            }
            break

        for pattern in applied_patterns:
            match = re.search(pattern, source_text)
            if not match:
                continue
            raw_applied = re.sub(r"\s+", " ", match.group(1).strip(" ,:-"))
            for candidate_rank in re.split(r"\s*,\s*", raw_applied):
                raw_rank, canonical_id, department, seniority_bucket, confidence = _normalize_raw_rank(candidate_rank)
                if canonical_id:
                    return {
                        "raw_rank": raw_rank,
                        "canonical_id": canonical_id,
                        "department": department,
                        "seniority_bucket": seniority_bucket,
                        "confidence": confidence,
                        "status": "PARSED",
                        "extraction_method": "labeled_field_regex",
                        "source_label": "applied_for_rank",
                    }

        if present_unknown:
            return present_unknown

        return {
            "raw_rank": None,
            "canonical_id": None,
            "department": None,
            "seniority_bucket": None,
            "confidence": None,
            "status": "MISSING",
            "extraction_method": "labeled_field_regex",
            "source_label": None,
        }

    def _ship_type_aliases(self):
        # LEGACY FALLBACK ONLY.
        #
        # The authoritative ship-type vocabulary is [ShipTypes] ship_type_options
        # in config.ini, read at process start via self.config.config.
        # _extract_configured_ship_types() is the primary extraction path.
        #
        # This dict is used ONLY when the runtime config has no [ShipTypes] section
        # (i.e. _configured_ship_type_labels() returned an empty list).  Callers
        # that reach this path log a warning so the gap is never silent.
        #
        # If a term appears here but NOT in config.ini, the two paths will return
        # different results for the same prompt.  Keep config.ini as the single
        # source of truth and add any new aliases there instead of here.
        return {
            "tanker": [
                "tanker", "oil tanker", "product tanker", "crude oil tanker",
                "chemical tanker", "oil chem tanker", "bitumen tanker", "mr tanker", "vlcc",
            ],
            "bulk carrier": [
                "bulk carrier", "mini bulk carrier", "cape bulk", "bulk vessel",
            ],
            "container": [
                "container", "container vessel", "cellular container", "reefer container", "reefer container vessel",
            ],
            "offshore": [
                "offshore", "offshore supply", "offshore supply vessel", "aht", "ahts", "platform supply vessel", "psv",
            ],
            "lng": [
                "lng", "lng carrier",
            ],
            "lpg": [
                "lpg", "lpg carrier",
            ],
            "ro-ro": [
                "ro-ro", "roro", "ro ro", "ro-ro vessel",
            ],
            "car carrier": [
                "car carrier",
            ],
        }

    def _configured_ship_type_labels(self):
        cache = getattr(self, "_configured_ship_type_labels_cache", None)
        if cache is not None:
            return cache

        # Use the runtime config object loaded at process start.
        # Do NOT create a new ConfigParser or read from disk here — that would
        # diverge from the config the process was actually started with.
        labels = []
        runtime_config = getattr(self, "config", None)
        runtime_parser = getattr(runtime_config, "config", None)
        if runtime_parser and runtime_parser.has_section("ShipTypes") and runtime_parser.has_option("ShipTypes", "ship_type_options"):
            raw_value = runtime_parser.get("ShipTypes", "ship_type_options")
            labels = [line.strip() for line in raw_value.splitlines() if line.strip()]

        normalized = []
        seen = set()
        for label in labels:
            clean = self._normalize_ship_type(label)
            if clean and clean not in seen:
                normalized.append(clean)
                seen.add(clean)

        self._configured_ship_type_labels_cache = normalized
        return normalized

    def _extract_configured_ship_types(self, raw_text):
        original_text = str(raw_text or "")
        if not original_text.strip():
            return []
        lowered = original_text.lower()

        matches = []
        occupied = []
        labels = sorted(self._configured_ship_type_labels(), key=len, reverse=True)
        for label in labels:
            alias_pattern = re.escape(label).replace(r"\ ", r"[\s./()\-]+")
            pattern = rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9-])"
            match = re.search(pattern, lowered)
            if not match:
                continue
            span = match.span()
            if any(not (span[1] <= start or span[0] >= end) for start, end in occupied):
                continue
            occupied.append(span)
            matches.append((span[0], label))

        return [label for _, label in sorted(matches, key=lambda item: item[0])]

    def _extract_experience_ship_type_constraint(self, user_prompt):
        prompt = self._normalize_ship_type(user_prompt)
        if not prompt:
            return None
        if not any(token in prompt for token in ("experience", "experienced", "sailed", "worked on", "vessel", "ship", "background")):
            return None
        configured_matches = self._extract_configured_ship_types(prompt)
        if configured_matches:
            return configured_matches[0]
        for canonical, aliases in self._experience_keyword_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                escaped = re.escape(normalized_alias)
                patterns = [
                    rf'\b{escaped}\s+experience\b',
                    rf'\bexperience\s+(?:on|in|with)?\s*{escaped}\b',
                    rf'\bexperienced\s+(?:on|with)?\s*{escaped}\b',
                    rf'\bhas\s+{escaped}\s+experience\b',
                    rf'\bwith\s+{escaped}\s+experience\b',
                    rf'\bworked\s+(?:on|with)?\s*{escaped}\b',
                    rf'\bsailed\s+(?:on|with)?\s*{escaped}\b',
                    rf'\b{escaped}\s+background\b',
                    rf'\bbackground\s+(?:on|in|with)?\s*{escaped}\b',
                ]
                if any(re.search(pattern, prompt) for pattern in patterns):
                    return canonical
        # Configured labels returned nothing; fall back to hardcoded alias table.
        if not self._configured_ship_type_labels():
            print("[WARN] _extract_experienced_vessel_type: no configured ship-type labels found; "
                  "using hardcoded fallback aliases. Add [ShipTypes] ship_type_options to config.ini.")
        for canonical, aliases in self._ship_type_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                escaped = re.escape(normalized_alias)
                patterns = [
                    rf'\b{escaped}\s+experience\b',
                    rf'\bexperience\s+(?:on|in|with)?\s*{escaped}\b',
                    rf'\bexperienced\s+on\s+{escaped}\b',
                    rf'\bsailed on\s+{escaped}\b',
                    rf'\bworked on\s+{escaped}\b',
                    rf'\b{escaped}\s+background\b',
                    rf'\bbackground\s+(?:on|in|with)?\s*{escaped}\b',
                ]
                if any(re.search(pattern, prompt) for pattern in patterns):
                    return canonical
        return None

    def _ship_type_expected_values(self, requested_ship_type):
        normalized_requested = self._normalize_ship_type(requested_ship_type)
        if not normalized_requested:
            return []

        experience_aliases = self._experience_keyword_aliases().get(normalized_requested)
        if experience_aliases:
            expected_values = []
            seen = set()
            for alias in experience_aliases:
                normalized_alias = self._normalize_ship_type(alias)
                if normalized_alias and normalized_alias not in seen:
                    expected_values.append(normalized_alias)
                    seen.add(normalized_alias)
            return expected_values or [normalized_requested]

        aliases = self._ship_type_aliases().get(normalized_requested)
        if not aliases:
            for canonical, candidate_aliases in self._ship_type_aliases().items():
                normalized_aliases = [self._normalize_ship_type(alias) for alias in candidate_aliases]
                if normalized_requested in normalized_aliases:
                    aliases = candidate_aliases
                    break
        if not aliases:
            return [normalized_requested]

        expected_values = []
        seen = set()
        for alias in aliases:
            normalized_alias = self._normalize_ship_type(alias)
            if normalized_alias and normalized_alias not in seen:
                expected_values.append(normalized_alias)
                seen.add(normalized_alias)
        return expected_values or [normalized_requested]

    def _experience_keyword_aliases(self):
        return {}

    def _visa_type_definitions(self):
        return [
            {
                "canonical": "C1/D (USA)",
                "group": "usa",
                "patterns": [r"\bc1\s*/\s*d\b", r"\bc1d\b"],
            },
            {
                "canonical": "B1/B2 (USA)",
                "group": "usa",
                "patterns": [r"\bb1\s*/\s*b2\b", r"\bb1b2\b"],
            },
            {
                "canonical": "C1 (USA)",
                "group": "usa",
                "patterns": [r"\bc1\s+visa\b", r"\bc1\s*\(\s*usa\s*\)", r"\bc1\b(?!\s*/\s*d)"],
            },
            {
                "canonical": "D (USA)",
                "group": "usa",
                "patterns": [r"\bd\s+visa\b", r"\bd\s*\(\s*usa\s*\)"],
            },
            {
                "canonical": "US Visa (USA)",
                "group": "usa",
                "patterns": [
                    r"\bus\s+visa\b",
                    r"\busa\s+visa\b",
                    r"\bamerican\s+visa\b",
                    r"\bus\s+work\s+authorization\b",
                ],
            },
            {
                "canonical": "Australia Entry visa",
                "group": "australia",
                "patterns": [r"\baustralia(?:n)?\s+entry\s+visa\b"],
            },
            {
                "canonical": "MCV (Australia)",
                "group": "australia",
                "patterns": [
                    r"\bmcv\s*\(\s*australia\s*\)",
                    r"\bmcv\b",
                    r"\bonline\s+maritime\s+crew\s+visa\b",
                    r"\bmaritime\s+crew\s+visa\b",
                ],
            },
            {
                "canonical": "Schengen",
                "group": "schengen",
                "patterns": [r"\bschengen(?:\s+visa)?\b"],
            },
        ]

    def _extract_specific_visa_type_from_prompt(self, prompt):
        for visa_def in self._visa_type_definitions():
            if any(re.search(pattern, prompt, flags=re.IGNORECASE) for pattern in visa_def["patterns"]):
                return visa_def
        return None

    def _extract_us_visa_constraint(self, user_prompt):
        prompt = str(user_prompt or "").strip().lower()
        if not prompt:
            return None

        window_patterns = [
            (
                "usa",
                [
                    r"\bus\s+visa\s+is\s+valid\s+(?:at\s+least\s+for\s+|for\s+at\s+least\s+|for\s+minimum\s+|for\s+)?(\d+)\s+months?\b",
                    r"\bvalid\s+us\s+visa\s+(?:for\s+)?(?:at\s+least\s+|minimum\s+)?(\d+)\s+months?\b",
                    r"\bminimum\s+(\d+)\s+months?\s+(?:of\s+)?(?:validity\s+on\s+)?us\s+visa\b",
                    r"\bus\s+visa\s+should\s+be\s+valid\s+for\s+(?:at\s+least\s+)?(\d+)\s+months?\b",
                ],
                "valid US visa",
            ),
        ]
        for group, patterns, base_label in window_patterns:
            for pattern in patterns:
                match = re.search(pattern, prompt)
                if not match:
                    continue
                months = int(match.group(1))
                accepted = [
                    visa_def["canonical"]
                    for visa_def in self._visa_type_definitions()
                    if visa_def["group"] == group
                ]
                return {
                    "required": True,
                    "must_be_valid": True,
                    "accepted_types": accepted,
                    "visa_group": group,
                    "minimum_months_remaining": months,
                    "requested_label": f"{base_label} for at least {months} months",
                    "display_value": match.group(0).strip(),
                }

        group_patterns = [
            (
                "usa",
                [
                    r"\bvalid\s+us\s+visa\b",
                    r"\bcurrent\s+us\s+visa\b",
                    r"\bhas\s+a\s+valid\s+us\s+visa\b",
                    r"\bholding\s+valid\s+us\s+visa\b",
                    r"\bwith\s+valid\s+us\s+visa\b",
                    r"\bmust\s+have\s+valid\s+us\s+visa\b",
                    r"\bus\s+visa\s+holder\b",
                    r"\bus\s+visa\b",
                    r"\bamerican\s+visa\b",
                    r"\bus\s+work\s+authorization\b",
                ],
                "valid US visa",
            ),
            (
                "australia",
                [
                    r"\bvalid\s+australia(?:n)?\s+visa\b",
                    r"\bcurrent\s+australia(?:n)?\s+visa\b",
                    r"\bholding\s+valid\s+australia(?:n)?\s+visa\b",
                    r"\bwith\s+valid\s+australia(?:n)?\s+visa\b",
                    r"\baustralia(?:n)?\s+visa\s+holder\b",
                    r"\baustralia(?:n)?\s+visa\b",
                ],
                "valid Australia visa",
            ),
            (
                "schengen",
                [
                    r"\bvalid\s+schengen\s+visa\b",
                    r"\bcurrent\s+schengen\s+visa\b",
                    r"\bholding\s+valid\s+schengen\s+visa\b",
                    r"\bwith\s+valid\s+schengen\s+visa\b",
                    r"\bschengen\s+visa\s+holder\b",
                    r"\bschengen\s+visa\b",
                ],
                "valid Schengen visa",
            ),
        ]
        for group, patterns, label in group_patterns:
            if any(re.search(pattern, prompt) for pattern in patterns):
                accepted = [
                    visa_def["canonical"]
                    for visa_def in self._visa_type_definitions()
                    if visa_def["group"] == group
                ]
                return {
                    "required": True,
                    "must_be_valid": True,
                    "accepted_types": accepted,
                    "visa_group": group,
                    "requested_label": label,
                }

        specific = self._extract_specific_visa_type_from_prompt(prompt)
        if specific:
            return {
                "required": True,
                "must_be_valid": True,
                "accepted_types": [specific["canonical"]],
                "visa_group": specific["group"],
                "requested_label": specific["canonical"],
            }

        unsupported_patterns = [
            (r"\bvalid\s+uk\s+visa\b", "valid UK visa"),
            (r"\bcurrent\s+uk\s+visa\b", "valid UK visa"),
            (r"\bhaving\s+valid\s+uk\s+visa\b", "valid UK visa"),
            (r"\buk\s+visa\b", "valid UK visa"),
            (r"\bvalid\s+united\s+kingdom\s+visa\b", "valid UK visa"),
            (r"\bunited\s+kingdom\s+visa\b", "valid UK visa"),
        ]
        for pattern, label in unsupported_patterns:
            if re.search(pattern, prompt):
                return {
                    "required": True,
                    "must_be_valid": True,
                    "accepted_types": [],
                    "visa_group": "uk",
                    "requested_label": label,
                    "supported": False,
                }
        return None

    def _extract_passport_validity_constraint(self, user_prompt):
        prompt = str(user_prompt or "").strip().lower()
        if not prompt:
            return None

        window_patterns = [
            r"\bpassport\s+valid(?:ity)?\s+(?:for\s+)?(?:at\s+least\s+|minimum\s+)?(\d+)\s+months?\b",
            r"\b(?:at\s+least|minimum)\s+(\d+)\s+months?\s+(?:of\s+)?passport\s+valid(?:ity)?\b",
            r"\b(\d+)\s+months?\s+(?:of\s+)?passport\s+valid(?:ity)?\b",
            r"\bpassport\s+should\s+be\s+valid\s+for\s+(?:at\s+least\s+)?(\d+)\s+months?\b",
        ]
        for pattern in window_patterns:
            match = re.search(pattern, prompt)
            if match:
                months = int(match.group(1))
                return {
                    "required": True,
                    "must_be_valid": True,
                    "minimum_months_remaining": months,
                    "requested_label": f"passport valid for at least {months} months",
                    "display_value": match.group(0).strip(),
                }

        patterns = [
            r"\bvalid\s+passport\b",
            r"\bpassport\s+required\b",
            r"\bpassport\s+mandatory\b",
            r"\bmust\s+have\s+valid\s+passport\b",
            r"\bmust\s+hold\s+valid\s+passport\b",
            r"\bpassport\s+holder\b",
            r"\bvalid\s+passport\s+holder\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, prompt)
            if match:
                return {
                    "required": True,
                    "must_be_valid": True,
                    "requested_label": "valid passport",
                    "display_value": match.group(0).strip(),
                }
        return None

    def _months_remaining_from_date(self, future_date, reference_date):
        if not future_date or not reference_date:
            return None
        months = (future_date.year - reference_date.year) * 12 + (future_date.month - reference_date.month)
        if future_date.day < reference_date.day:
            months -= 1
        return max(0, months)

    def _strip_age_constraint_phrases(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt:
            return ""

        patterns = [
            r'between\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'between\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'between\s+ages?\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'with\s*in\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+between\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+range\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+range\s+of\s+\d{1,2}\s*-\s*\d{1,2}',
            r'age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'ages?\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'aged?\s+\d{1,2}\s*(?:-|to|and)\s*\d{1,2}',
            r'\d{1,2}\s*(?:-|to)\s*\d{1,2}\s+years?\s+old',
            r'at\s+least\s+\d{1,2}\s+years?\s+old',
            r'older\s+than\s+\d{1,2}',
            r'over\s+the\s+age\s+of\s+\d{1,2}(?:\s+years?)?',
            r'over\s+\d{1,2}',
            r'above\s+the\s+age\s+of\s+\d{1,2}',
            r'above\s+\d{1,2}',
            r'minimum\s+age\s+(?:of\s+)?\d{1,2}',
            r'minimum\s+age\s+should\s+be\s+\d{1,2}',
            r'up\s+to\s+\d{1,2}\s+years?\s+old',
            r'younger\s+than\s+\d{1,2}',
            r'below\s+the\s+age\s+of\s+\d{1,2}',
            r'below\s+age\s+\d{1,2}',
            r'less\s+than\s+\d{1,2}\s+years?\s+old',
            r'not\s+more\s+than\s+\d{1,2}\s+years?\s+old',
            r'under\s+\d{1,2}',
            r'below\s+\d{1,2}',
            r'maximum\s+age\s+(?:of\s+)?\d{1,2}',
            r'maximum\s+age\s+should\s+be\s+\d{1,2}',
        ]

        cleaned = prompt
        for pattern in patterns:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)

        cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ,.-")
        return cleaned

    def _strip_visa_constraint_phrases(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt:
            return ""
        patterns = [
            r'having\s+valid\s+uk\s+visa',
            r'has\s+a\s+valid\s+us\s+visa',
            r'having\s+valid\s+us\s+visa',
            r'valid\s+us\s+visa',
            r'current\s+us\s+visa',
            r'us\s+visa',
            r'usa\s+visa',
            r'c1/d\s+visa',
            r'c1\s+visa',
            r'd\s+visa',
            r'b1/b2\s+visa',
            r'american\s+visa',
            r'us\s+work\s+authorization',
            r'having\s+valid\s+australia(?:n)?\s+visa',
            r'valid\s+australia(?:n)?\s+visa',
            r'current\s+australia(?:n)?\s+visa',
            r'australia(?:n)?\s+visa',
            r'australia(?:n)?\s+entry\s+visa',
            r'mcv\s*\(\s*australia\s*\)',
            r'mcv',
            r'having\s+valid\s+schengen\s+visa',
            r'valid\s+schengen\s+visa',
            r'current\s+schengen\s+visa',
            r'schengen\s+visa',
            r'valid\s+uk\s+visa',
            r'current\s+uk\s+visa',
            r'uk\s+visa',
            r'valid\s+united\s+kingdom\s+visa',
            r'united\s+kingdom\s+visa',
        ]
        cleaned = prompt
        for pattern in patterns:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(" ,.-")
        return cleaned

    def _is_structured_only_prompt(self, user_prompt, job_constraints=None, has_semantic_intent=None):
        applied_constraints = ((job_constraints or {}).get("applied_constraints") or [])
        if applied_constraints and has_semantic_intent is False:
            return True

        stripped = self._strip_age_constraint_phrases(user_prompt)
        stripped = self._strip_visa_constraint_phrases(stripped).lower()
        if not stripped:
            return True
        filler_tokens = {
            "candidate", "candidates", "should", "be", "must", "need", "needs",
            "within", "the", "age", "ages", "years", "year", "old", "of",
            "show", "find", "give", "me", "with", "who", "that", "are", "is",
            "valid", "visa", "visas", "us", "usa", "american", "work", "authorization",
            "current", "has", "australia", "australian", "entry", "mcv", "schengen",
            "uk", "united", "kingdom", "having",
        }
        terms = re.findall(r"[a-zA-Z0-9/+.-]{2,}", stripped)
        meaningful = [term for term in terms if term not in filler_tokens]
        return len(meaningful) == 0

    def _has_semantic_intent(self, user_prompt, job_constraints):
        prompt = str(user_prompt or "").strip().lower()
        if not prompt:
            return False

        applied_constraints = (job_constraints or {}).get("applied_constraints") or []
        unapplied_constraints = (job_constraints or {}).get("unapplied_constraints") or []

        # Preserve graceful-failure behavior for prompts that are already
        # recognized as unsupported structured constraints.
        if not applied_constraints and not unapplied_constraints:
            experience_semantic_groups = [
                (
                    r"\b(?:experience|experienced|background|exposure)\b",
                    r"\b(?:ship|ships|vessel|vessels|fleet)\b",
                ),
                (
                    r"\b(?:worked|working|sailed|sailing|served|serving)\b",
                    r"\b(?:ship|ships|vessel|vessels|fleet|company|employer|contract|contracts)\b",
                ),
            ]
            for left_pattern, right_pattern in experience_semantic_groups:
                if re.search(left_pattern, prompt, flags=re.IGNORECASE) and re.search(right_pattern, prompt, flags=re.IGNORECASE):
                    return True

        semantic_patterns = [
            r"\bleadership\b",
            r"\bunder pressure\b",
            r"\bstability\b",
            r"\bbest fit\b",
            r"\bcommunication\b",
            r"\bproblem solving\b",
            r"\breliable\b",
            r"\bmotivated\b",
            r"\bteam player\b",
            r"\boffshore exposure\b",
            r"\bstrong\b",
            r"\bgood\b",
        ]
        if not any(re.search(pattern, prompt, flags=re.IGNORECASE) for pattern in semantic_patterns):
            return False

        residual = prompt
        rank_constraint = ((job_constraints or {}).get("hard_constraints") or {}).get("rank") or {}
        for candidate_phrase in [
            rank_constraint.get("requested_label"),
            ((job_constraints or {}).get("hard_constraints") or {}).get("sea_service", {}).get("display_value"),
            ((job_constraints or {}).get("hard_constraints") or {}).get("vessel_type", {}).get("display_value"),
            ((job_constraints or {}).get("hard_constraints") or {}).get("availability", {}).get("display_value"),
            ((job_constraints or {}).get("hard_constraints") or {}).get("certifications", {}).get("endorsement_display_value"),
        ]:
            if candidate_phrase:
                residual = residual.replace(str(candidate_phrase).lower(), " ")
        residual = self._strip_age_constraint_phrases(residual)
        residual = self._strip_visa_constraint_phrases(residual)
        residual = re.sub(r"\s+", " ", residual).strip(" ,.-")
        return bool(residual)

    def _iter_pdf_files(self, folder_path):
        folder = Path(folder_path)
        if not folder.exists():
            return []
        return sorted(
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        )

    def _enumerate_rank_candidates(self, target_folder, rank):
        candidates = {}
        for idx, pdf_path in enumerate(self._iter_pdf_files(target_folder)):
            try:
                text = self.pdf_processor.extract_text(str(pdf_path))
            except Exception:
                continue
            if not text:
                continue
            # Full-scan hard-filter mode does not need a remote registry lookup.
            # Use the registry's deterministic ID generator and carry the source
            # path forward in chunk metadata for later local access.
            resume_id = self.registry.generate_resume_id(str(pdf_path))
            candidates[resume_id] = [{
                "id": f"fullscan-{resume_id}-{idx}",
                "score": 1.0,
                "metadata": {
                    "resume_id": resume_id,
                    "rank": rank,
                    "filename": pdf_path.name,
                    "source_path": str(pdf_path),
                    "raw_text": text[:self.LLM_CONTEXT_TEXT_CHAR_LIMIT],
                }
            }]
        print(f"[FULL SCAN] Enumerated {len(candidates)} candidate resumes from rank folder")
        return candidates

    def _rank_manifest_metadata(self, target_folder):
        manifest_path = Path(target_folder) / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        files = data.get("files") if isinstance(data, dict) else {}
        return files if isinstance(files, dict) else {}

    def _extract_same_company_contract_count_fact_from_text(self, raw_text, original_path=None):
        text = str(raw_text or "")
        if not text.strip():
            return {
                "count": None,
                "repeat_employer_present": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": None,
            }

        path_name = Path(original_path).name if original_path else ""
        if path_name.upper().startswith("EMAIL_"):
            return {
                "count": None,
                "repeat_employer_present": None,
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "email_resume_excluded",
            }

        upper = text.upper()
        has_seajobs_banner = (
            "NJORDSHIPS MANAGEMENT INDIA PVT LTD" in upper
            or "NJORSHIPS MANAGEMENT INDIA PVT LTD" in upper
        )
        if "SEAMEN EXPERIENCE DETAILS" not in upper or not has_seajobs_banner:
            return {
                "count": None,
                "repeat_employer_present": None,
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "non_seajobs_resume_excluded",
            }

        section = self._extract_seajobs_experience_section(text)
        if not section:
            return {
                "count": None,
                "repeat_employer_present": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        company_counts = self._extract_seajobs_company_occurrence_counts(section)
        if not company_counts:
            return {
                "count": None,
                "repeat_employer_present": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        max_count = max(company_counts.values())
        return {
            "count": max_count,
            "repeat_employer_present": max_count >= 2,
            "status": "PARSED",
            "confidence": 0.9,
            "extraction_method": "seajobs_service_history",
            "source_label": "seajobs_resume",
        }

    def _extract_seajobs_experience_section(self, text):
        content = str(text or "")
        if not content:
            return ""
        upper = content.upper()
        start = upper.find("SEAMEN EXPERIENCE DETAILS")
        if start < 0:
            return ""
        tail = content[start:]
        upper_tail = tail.upper()
        end = len(tail)
        for marker in (
            "CERTIFICATE DETAILS",
            "COURSE DETAILS",
            "ACADEMIC DETAILS",
            "DANGEROUS CARGO ENDORSEMENT",
            "TOTAL EXPERIENCE",
        ):
            marker_index = upper_tail.find(marker, 40)
            if marker_index >= 0:
                end = min(end, marker_index)
        return tail[: min(end, 5000)].strip()

    def _extract_seajobs_experience_row_windows(self, section_text):
        lines = [" ".join(line.split()) for line in str(section_text or "").splitlines() if line.strip()]
        row_windows = []
        header_tokens = {
            "SEAMEN EXPERIENCE DETAILS",
            "SIGN IN SIGN OUT",
            "DATE DATE",
        }
        anchor_indices = [
            idx for idx, line in enumerate(lines)
            if re.match(r"^\d{1,2}\s", line)
            and line.upper() not in header_tokens
            and "COMPANY NAME / SHIP TYPE" not in line.upper()
            and not line.upper().startswith("# ")
        ]
        anchor_index_set = set(anchor_indices)

        def is_header(line):
            upper_line = line.upper()
            return (
                upper_line in header_tokens
                or "COMPANY NAME / SHIP TYPE" in upper_line
                or upper_line.startswith("# ")
            )

        def is_row_prefix_candidate(line):
            text = str(line or "").strip()
            if not text:
                return False
            lower = text.lower()
            if "/" in text:
                return True
            if re.search(r"\b\d{1,2}[\s/\-.]*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b", lower):
                return True
            if re.match(r"^(?:master|chief|2nd|second|3rd|third|4th|fourth|5th|fifth|6th|sixth|jr|junior|sr|senior|ab|os|oiler|wiper|bosun|pumpman|fitter)\b", lower):
                return True
            return False

        for anchor_position, idx in enumerate(anchor_indices):
            previous_boundary = (anchor_indices[anchor_position - 1] + 1) if anchor_position > 0 else 0
            next_boundary = anchor_indices[anchor_position + 1] if anchor_position + 1 < len(anchor_indices) else len(lines)

            prefix_candidates = []
            for back_idx in range(idx - 1, previous_boundary - 1, -1):
                candidate_line = lines[back_idx]
                if is_header(candidate_line) or back_idx in anchor_index_set:
                    break
                if is_row_prefix_candidate(candidate_line):
                    prefix_candidates.insert(0, candidate_line)

            suffix_candidates = []
            for forward_idx in range(idx + 1, next_boundary):
                candidate_line = lines[forward_idx]
                if is_header(candidate_line) or forward_idx in anchor_index_set:
                    break
                suffix_candidates.append(candidate_line)

            while len(suffix_candidates) > 1 and is_row_prefix_candidate(suffix_candidates[-1]):
                suffix_candidates.pop()

            anchor_line = lines[idx]
            window = prefix_candidates + [anchor_line] + suffix_candidates

            row_windows.append(window)

        return row_windows

    def _extract_seajobs_experience_row_snippets(self, section_text):
        row_snippets = []
        for window in self._extract_seajobs_experience_row_windows(section_text):
            reconstructed_tokens = self._extract_ordered_date_tokens_from_seajobs_row(window)
            row_snippets.append(" ".join(reconstructed_tokens) if reconstructed_tokens else " ".join(window))

        return row_snippets

    def _extract_rank_from_seajobs_row_window(self, row_lines):
        lines = [" ".join(str(line or "").split()) for line in (row_lines or []) if str(line or "").strip()]
        if not lines:
            return {
                "raw_rank": None,
                "canonical_id": None,
                "department": None,
                "seniority_bucket": None,
                "confidence": None,
                "status": "MISSING",
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        candidate_labels = []
        seen = set()

        def add_candidate(label):
            normalized = " ".join(str(label or "").split()).strip(" ,:-")
            normalized = re.sub(r"^\d{1,2}\s+", "", normalized).strip(" ,:-")
            if not normalized:
                return
            key = normalized.lower()
            if key in seen:
                return
            seen.add(key)
            candidate_labels.append(normalized)

        if len(lines) >= 3:
            prev_tokens = lines[0].split()
            next_tokens = lines[2].split()
            for prev_count in range(1, min(2, len(prev_tokens)) + 1):
                for next_count in range(1, min(2, len(next_tokens)) + 1):
                    add_candidate(" ".join(prev_tokens[:prev_count] + next_tokens[:next_count]))

        for line in lines:
            tokens = line.split()
            for count in range(1, min(3, len(tokens)) + 1):
                add_candidate(" ".join(tokens[:count]))

        for raw_rank in candidate_labels:
            canonical_id, department, seniority_bucket, confidence = self._normalize_rank(raw_rank)
            if canonical_id:
                return {
                    "raw_rank": raw_rank,
                    "canonical_id": canonical_id,
                    "department": department,
                    "seniority_bucket": seniority_bucket,
                    "confidence": confidence,
                    "status": "PARSED",
                    "extraction_method": "seajobs_service_history",
                    "source_label": "seajobs_resume",
                }

        return {
            "raw_rank": None,
            "canonical_id": None,
            "department": None,
            "seniority_bucket": None,
            "confidence": None,
            "status": "MISSING",
            "extraction_method": "seajobs_service_history",
            "source_label": "seajobs_resume",
        }

    def _extract_email_experience_section(self, text):
        content = str(text or "")
        if not content:
            return ""

        start_match = re.search(
            (
                r"\b(?:seamen\s+experience\s+details|sea\s+service(?:\s+experience)?(?:\s+details|\s+record)?|"
                r"sea\s+experience|work\s+experience)\b"
            ),
            content,
            flags=re.IGNORECASE,
        )
        if not start_match:
            return ""

        tail = content[start_match.start():]
        upper_tail = tail.upper()
        end = len(tail)
        for marker in (
            "DOCUMENTS",
            "DOCUMENTS HELD",
            "DOCUMENT DETAILS",
            "COURSES",
            "CERTIFICATES",
            "EDUCATIONAL",
            "ACADEMIC",
            "PERSONAL DETAILS",
            "DECLARATION",
        ):
            marker_index = upper_tail.find(marker, 60)
            if marker_index >= 0:
                end = min(end, marker_index)
        return tail[: min(end, 8000)].strip()

    def _email_line_has_complete_service_dates(self, line):
        date_tokens = self._extract_ordered_date_tokens_from_seajobs_row([str(line or "")])
        if len(date_tokens) < 2:
            return False
        parsed_dates = [self._parse_ordered_date_token(token).get("status") for token in date_tokens[:2]]
        return parsed_dates == ["PARSED", "PARSED"]

    def _email_service_row_has_context_cue(self, row_lines):
        snippet = " ".join(str(line or "") for line in (row_lines or []))
        return bool(re.search(
            r"\b(?:ship|vessel|tanker|carrier|bulk|container|oil|product|lng|lpg|engine|b&w|man|"
            r"wartsila|sulzer|yanmar|rank|officer|engineer|eo|2o|3o)\b",
            snippet,
            flags=re.IGNORECASE,
        ))

    def _email_line_starts_split_service_date_range(self, line):
        line = str(line or "")
        date_tokens = self._extract_ordered_date_tokens_from_seajobs_row([line])
        if len(date_tokens) != 1:
            return False
        parsed_date = self._parse_ordered_date_token(date_tokens[0])
        if parsed_date.get("status") != "PARSED":
            return False
        escaped_token = re.escape(str(date_tokens[0]).strip())
        return bool(re.search(rf"{escaped_token}\s*(?:[-–]\s*|\bto\b)", line, flags=re.IGNORECASE))

    def _email_to_delimited_service_row_window(self, lines, line_index):
        line = lines[line_index] if 0 <= line_index < len(lines) else ""
        date_tokens = self._extract_ordered_date_tokens_from_seajobs_row([line])
        if len(date_tokens) != 1 or self._email_line_starts_split_service_date_range(line):
            return []
        if self._parse_ordered_date_token(date_tokens[0]).get("status") != "PARSED":
            return []

        window = [line]
        saw_to = False
        for next_line in lines[line_index + 1:line_index + 6]:
            next_dates = self._extract_ordered_date_tokens_from_seajobs_row([next_line])
            if next_dates and not saw_to:
                return []
            window.append(next_line)
            if re.search(r"\bto\b", next_line, flags=re.IGNORECASE):
                saw_to = True
            if saw_to and next_dates:
                return window
        return []

    def _extract_email_indexed_service_row_windows(self, lines):
        if not any(re.search(r"#\s*rank\s+tonnage\s+engine\s+in\s+out", line, flags=re.IGNORECASE) for line in lines):
            return []
        anchors = [
            idx for idx, line in enumerate(lines)
            if re.match(r"^\d{1,2}\s", line)
        ]
        windows = []
        for anchor_position, anchor_idx in enumerate(anchors):
            next_anchor = anchors[anchor_position + 1] if anchor_position + 1 < len(anchors) else len(lines)
            window_end = max(anchor_idx + 1, next_anchor - 2) if next_anchor < len(lines) else next_anchor
            window = lines[max(0, anchor_idx - 2):window_end]
            if self._email_service_row_has_context_cue(window):
                windows.append(window)
        return windows

    def _extract_fragmented_email_date_tokens(self, row_lines):
        lines = [" ".join(str(line or "").split()) for line in (row_lines or []) if str(line or "").strip()]
        if not lines:
            return []

        parsed_candidates = []
        for token in self._extract_ordered_date_tokens_from_seajobs_row(lines):
            parsed_fact = self._parse_ordered_date_token(token)
            if parsed_fact.get("status") == "PARSED" and parsed_fact.get("date"):
                parsed_candidates.append((parsed_fact["date"], token))

        day_fragments = []
        for line in lines:
            match = re.search(r"\b(\d{1,2})-\s*$", line)
            if match:
                day_fragments.append(match.group(1))

        month_pattern = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        month_year_fragments = []
        for line_index, line in enumerate(lines):
            for match in re.finditer(rf"{month_pattern}[\s\-.]*(\d{{3,4}})\b", line, flags=re.IGNORECASE):
                month, year = match.groups()
                if len(year) == 3:
                    for next_line in lines[line_index + 1:line_index + 3]:
                        next_digit = re.fullmatch(r"(\d)", next_line.strip())
                        if next_digit:
                            year = f"{year}{next_digit.group(1)}"
                            break
                if len(year) == 4:
                    month_year_fragments.append((month, year))
        for day, (month, year) in zip(day_fragments, month_year_fragments):
            token = f"{day}-{month}-{year}"
            parsed_fact = self._parse_ordered_date_token(token)
            if parsed_fact.get("status") == "PARSED" and parsed_fact.get("date"):
                parsed_candidates.append((parsed_fact["date"], token))

        year_tokens = []
        for line in lines:
            year_tokens.extend(re.findall(r"\b(?:19|20)\d{2}\b", line))
        month_only_fragments = []
        for line in lines:
            if re.search(rf"\d{{1,2}}[\s\-.]+{month_pattern}", line, flags=re.IGNORECASE):
                continue
            match = re.search(rf"\b{month_pattern}-\s*$", line, flags=re.IGNORECASE)
            if match:
                month_only_fragments.append(match.group(1))
        if day_fragments and month_only_fragments and year_tokens:
            token = f"{day_fragments[0]}-{month_only_fragments[0]}-{year_tokens[-1]}"
            parsed_fact = self._parse_ordered_date_token(token)
            if parsed_fact.get("status") == "PARSED" and parsed_fact.get("date"):
                parsed_candidates.append((parsed_fact["date"], token))

        parsed_candidates.sort(key=lambda item: item[0])
        rebuilt_tokens = []
        seen_dates = set()
        for parsed_date, token in parsed_candidates:
            if parsed_date in seen_dates:
                continue
            seen_dates.add(parsed_date)
            rebuilt_tokens.append(token)
            if len(rebuilt_tokens) == 2:
                return rebuilt_tokens
        return []

    def _build_email_experience_row(self, row_lines, row_index):
        date_tokens = self._extract_ordered_date_tokens_from_seajobs_row(row_lines)
        if len(date_tokens) < 2:
            date_tokens = self._extract_fragmented_email_date_tokens(row_lines)
        if len(date_tokens) < 2:
            return None
        sign_in_fact = self._parse_ordered_date_token(date_tokens[0])
        sign_out_fact = self._parse_ordered_date_token(date_tokens[1])
        sign_in_date = sign_in_fact.get("date") if sign_in_fact.get("status") == "PARSED" else None
        sign_out_date = sign_out_fact.get("date") if sign_out_fact.get("status") == "PARSED" else None
        if not sign_in_date or not sign_out_date or sign_out_date < sign_in_date:
            return None

        rank_fact = self._extract_rank_from_email_service_row(row_lines)
        return {
            "row_index": row_index,
            "rank_raw": rank_fact.get("raw_rank"),
            "rank_normalized": rank_fact.get("canonical_id"),
            "sign_in_date": sign_in_date,
            "sign_out_date": sign_out_date,
            "vessel_types": self._extract_row_ship_types_from_seajobs_row(row_lines),
            "engine_types": self._extract_engine_types_from_text(" ".join(row_lines)),
            "snippet": " ".join(row_lines),
        }

    def _extract_rank_from_email_service_row(self, row_lines):
        snippet = " ".join(" ".join(str(line or "").split()) for line in (row_lines or []) if str(line or "").strip())
        rank_patterns = [
            (r"\b2\s*e\s*o\b|\b2eo\b", "2nd engineer"),
            (r"\b3\s*e\s*o\b|\b3eo\b", "3rd engineer"),
            (r"\b4\s*e\s*o\b|\b4eo\b", "4th engineer"),
            (r"\b2\s*o\b|\b2o\b", "2nd officer"),
            (r"\b3\s*o\b|\b3o\b", "3rd officer"),
            (r"\bchief\b.{0,80}\bengineer\b", "chief engineer"),
            (r"\bsecond\b.{0,80}\bengineer\b|\b2nd\b.{0,80}\bengineer\b", "2nd engineer"),
            (r"\bthird\b.{0,80}\bengineer\b|\b3rd\b.{0,80}\bengineer\b", "3rd engineer"),
            (r"\bfourth\b.{0,80}\bengineer\b|\b4th\b.{0,80}\bengineer\b", "4th engineer"),
            (r"\bchief\s+officer\b|\bchief\s+mate\b", "chief officer"),
            (r"\bsecond\b.{0,80}\bofficer\b|\b2nd\b.{0,80}\bofficer\b", "2nd officer"),
            (r"\bthird\b.{0,80}\bofficer\b|\b3rd\b.{0,80}\bofficer\b", "3rd officer"),
            (r"\bdeck\s+cadet\b|\bcadet\b", "deck cadet"),
            (r"\bmaster\b|\bcaptain\b", "master"),
        ]
        for pattern, raw_rank in rank_patterns:
            if not re.search(pattern, snippet, flags=re.IGNORECASE):
                continue
            canonical_id, department, seniority_bucket, confidence = self._normalize_rank(raw_rank)
            if canonical_id:
                return {
                    "raw_rank": raw_rank,
                    "canonical_id": canonical_id,
                    "department": department,
                    "seniority_bucket": seniority_bucket,
                    "confidence": confidence,
                }
        return {
            "raw_rank": None,
            "canonical_id": None,
            "department": None,
            "seniority_bucket": None,
            "confidence": None,
        }

    def _extract_email_experience_rows(self, raw_text):
        section = self._extract_email_experience_section(raw_text)
        if not section:
            return {
                "rows": [],
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "email_date_complete_service_rows",
                "source_label": "email_resume_excluded",
            }

        lines = [" ".join(line.split()) for line in section.splitlines() if line.strip()]
        parsed_rows = []
        for line_index, line in enumerate(lines):
            if not self._email_line_has_complete_service_dates(line):
                continue
            if not self._email_service_row_has_context_cue([line]):
                continue

            window = [line]
            for next_line in lines[line_index + 1:line_index + 3]:
                if self._email_line_has_complete_service_dates(next_line):
                    break
                window.append(next_line)

            parsed_row = self._build_email_experience_row(window, len(parsed_rows) + 1)
            if not parsed_row:
                continue
            parsed_rows.append(parsed_row)

        for line_index, line in enumerate(lines):
            if not self._email_line_starts_split_service_date_range(line):
                continue

            window = [line]
            for next_line in lines[line_index + 1:line_index + 5]:
                if self._email_line_starts_split_service_date_range(next_line):
                    break
                window.append(next_line)
            if len(self._extract_ordered_date_tokens_from_seajobs_row(window)) < 2:
                continue
            if not self._email_service_row_has_context_cue(window):
                continue

            parsed_row = self._build_email_experience_row(window, len(parsed_rows) + 1)
            if parsed_row:
                parsed_rows.append(parsed_row)

        for line_index, _line in enumerate(lines):
            window = self._email_to_delimited_service_row_window(lines, line_index)
            if not window or not self._email_service_row_has_context_cue(window):
                continue
            parsed_row = self._build_email_experience_row(window, len(parsed_rows) + 1)
            if parsed_row:
                parsed_rows.append(parsed_row)

        for window in self._extract_email_indexed_service_row_windows(lines):
            parsed_row = self._build_email_experience_row(window, len(parsed_rows) + 1)
            if parsed_row:
                parsed_rows.append(parsed_row)

        return {
            "rows": parsed_rows,
            "status": "PARSED" if parsed_rows else "SOURCE_EXCLUDED",
            "confidence": 0.75 if parsed_rows else None,
            "extraction_method": "email_date_complete_service_rows",
            "source_label": "email_resume" if parsed_rows else "email_resume_excluded",
        }

    def _extract_seajobs_experience_rows(self, raw_text, original_path=None):
        text = str(raw_text or "")
        if not text.strip():
            return {
                "rows": [],
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": None,
            }

        path_name = Path(original_path).name if original_path else ""
        if path_name.upper().startswith("EMAIL_"):
            return self._extract_email_experience_rows(text)

        upper = text.upper()
        has_seajobs_banner = (
            "NJORDSHIPS MANAGEMENT INDIA PVT LTD" in upper
            or "NJORSHIPS MANAGEMENT INDIA PVT LTD" in upper
        )
        if "SEAMEN EXPERIENCE DETAILS" not in upper or not has_seajobs_banner:
            return {
                "rows": [],
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "non_seajobs_resume_excluded",
            }

        section = self._extract_seajobs_experience_section(text)
        if not section:
            return {
                "rows": [],
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        parsed_rows = []
        for window in self._extract_seajobs_experience_row_windows(section):
            row_index = None
            for line in window:
                match = re.match(r"^(\d{1,2})\s", line)
                if match:
                    row_index = int(match.group(1))
                    break

            rank_fact = self._extract_rank_from_seajobs_row_window(window)
            ordered_tokens = self._extract_ordered_date_tokens_from_seajobs_row(window)
            sign_in_date = None
            sign_out_date = None
            if len(ordered_tokens) >= 2:
                sign_in_fact = self._parse_ordered_date_token(ordered_tokens[0])
                sign_out_fact = self._parse_ordered_date_token(ordered_tokens[1])
                if sign_in_fact.get("status") == "PARSED":
                    sign_in_date = sign_in_fact.get("date")
                if sign_out_fact.get("status") == "PARSED":
                    sign_out_date = sign_out_fact.get("date")

            parsed_rows.append({
                "row_index": row_index,
                "rank_raw": rank_fact.get("raw_rank"),
                "rank_normalized": rank_fact.get("canonical_id"),
                "sign_in_date": sign_in_date,
                "sign_out_date": sign_out_date,
                "vessel_types": self._extract_row_ship_types_from_seajobs_row(window),
                "engine_types": self._extract_engine_types_from_text(" ".join(window)),
                "snippet": " ".join(window),
            })

        return {
            "rows": parsed_rows,
            "status": "PARSED" if parsed_rows else "MISSING",
            "confidence": 0.9 if parsed_rows else None,
            "extraction_method": "seajobs_service_history",
            "source_label": "seajobs_resume",
        }

    def _compute_service_duration_months(self, sign_in_date, sign_out_date):
        if not sign_in_date or not sign_out_date:
            return None
        if sign_out_date < sign_in_date:
            return None
        total_days = (sign_out_date - sign_in_date).days + 1
        if total_days <= 0:
            return None
        return max(1, total_days // 30)

    def _extract_ordered_date_tokens_from_seajobs_row(self, row_lines):
        month_pattern = r'(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)'
        partial_pattern = re.compile(
            rf'\b\d{{1,2}}[\s\/\-.]*{month_pattern}[\s\/\-.]*',
            flags=re.IGNORECASE,
        )
        year_pattern = re.compile(r'\b(?:19|20)\d{2}\b')
        full_token_pattern = re.compile(
            rf'\d{{4}}[\/\-.]\d{{1,2}}[\/\-.]\d{{1,2}}|'
            rf'\d{{1,2}}[\/\-.]\d{{1,2}}[\/\-.]\d{{2,4}}|'
            rf'\d{{1,2}}[\s\/\-.]+{month_pattern}[\s\/\-.]+\d{{4}}|'
            rf'{month_pattern}[\s\/\-.]+\d{{1,2}},?[\s\/\-.]+\d{{4}}',
            flags=re.IGNORECASE,
        )
        normalized_lines = [str(line or "") for line in (row_lines or [])]
        if not normalized_lines:
            return []

        anchor_idx = 0
        for idx, line in enumerate(normalized_lines):
            if re.match(r"^\d{1,2}\s", line):
                anchor_idx = idx
                break

        def _collect_line_parts(line):
            full_matches = []
            full_spans = []
            partial_matches = []
            year_matches = []
            for match in full_token_pattern.finditer(line):
                token = " ".join(match.group(0).strip().split())
                full_matches.append((match.start(), token))
                full_spans.append((match.start(), match.end()))
            for match in partial_pattern.finditer(line):
                start = match.start()
                if any(span_start <= start < span_end for span_start, span_end in full_spans):
                    continue
                partial_matches.append((match.start(), " ".join(match.group(0).strip().split())))
            for match in year_pattern.finditer(line):
                start = match.start()
                if any(span_start <= start < span_end for span_start, span_end in full_spans):
                    continue
                year_matches.append((match.start(), match.group(0)))
            return full_matches, partial_matches, year_matches

        prefix_lines = normalized_lines[:anchor_idx]
        anchor_line = normalized_lines[anchor_idx]
        suffix_lines = normalized_lines[anchor_idx + 1:]

        full_token_candidates = []
        partial_token_candidates = []
        year_token_candidates = []
        for line_idx, line in enumerate(normalized_lines):
            full_matches, partial_matches, year_matches = _collect_line_parts(line)
            for start, token in full_matches:
                full_token_candidates.append((line_idx, start, token))
            for start, token in partial_matches:
                partial_token_candidates.append((line_idx, start, token))
            for start, token in year_matches:
                year_token_candidates.append((line_idx, start, token))

        full_token_candidates.sort(key=lambda item: (item[0], item[1]))
        partial_token_candidates.sort(key=lambda item: (item[0], item[1]))
        year_token_candidates.sort(key=lambda item: (item[0], item[1]))
        ordered_full_tokens = []
        seen = set()
        for _line_idx, _start, token in full_token_candidates:
            if token in seen:
                continue
            seen.add(token)
            ordered_full_tokens.append(token)

        if len(ordered_full_tokens) >= 2:
            return ordered_full_tokens[:2]

        if not ordered_full_tokens and partial_token_candidates and year_token_candidates:
            rebuilt_tokens = []
            partial_values = [token for _line_idx, _start, token in partial_token_candidates]
            year_values = [token for _line_idx, _start, token in year_token_candidates]
            for idx, partial_token in enumerate(partial_values[:2]):
                if len(year_values) == 1:
                    year_token = year_values[0]
                elif idx < len(year_values):
                    year_token = year_values[idx]
                else:
                    year_token = year_values[-1]
                cleaned_partial = re.sub(r'[\s\/\-.]+$', '-', partial_token)
                rebuilt_tokens.append(f"{cleaned_partial}{year_token}")
            if rebuilt_tokens:
                return rebuilt_tokens

        prefix_partials = []
        for line in prefix_lines:
            _, partials, _ = _collect_line_parts(line)
            prefix_partials.extend(partials)
        anchor_partials = _collect_line_parts(anchor_line)[1]
        suffix_years = []
        for line in suffix_lines:
            _, _, years = _collect_line_parts(line)
            suffix_years.extend(years)
        prefix_years = []
        for line in prefix_lines:
            _, _, years = _collect_line_parts(line)
            prefix_years.extend(years)
        anchor_years = _collect_line_parts(anchor_line)[2]

        partial_token = None
        if prefix_partials:
            partial_token = prefix_partials[-1][1]
        elif anchor_partials:
            partial_token = anchor_partials[0][1]

        year_token = None
        if suffix_years:
            year_token = suffix_years[0][1]
        elif prefix_years:
            year_token = prefix_years[-1][1]
        elif anchor_years:
            year_token = anchor_years[0][1]

        rebuilt_sign_in = None
        if partial_token and year_token:
            cleaned_partial = re.sub(r'[\s\/\-.]+$', '-', partial_token)
            rebuilt_sign_in = f"{cleaned_partial}{year_token}"

        if ordered_full_tokens and rebuilt_sign_in:
            return [rebuilt_sign_in, ordered_full_tokens[0]]
        if rebuilt_sign_in:
            return [rebuilt_sign_in]
        return ordered_full_tokens

    def _extract_row_ship_types_from_seajobs_row(self, row_lines):
        snippet = " ".join(" ".join(str(line or "").split()) for line in (row_lines or []) if str(line or "").strip())
        if not snippet:
            return []
        configured_matches = self._extract_configured_ship_types(snippet)
        if configured_matches:
            return configured_matches
        experienced_matches = self._extract_experienced_ship_types_from_text(snippet)
        if experienced_matches:
            return experienced_matches
        return self._extract_fragmented_ship_types_from_seajobs_row(snippet)

    def _extract_fragmented_ship_types_from_seajobs_row(self, snippet):
        normalized_snippet = self._normalize_ship_type(snippet)
        if not normalized_snippet:
            return []

        row_tokens = re.findall(r"[a-z0-9]+", normalized_snippet)
        if len(row_tokens) < 2:
            return []

        candidates = []
        seen = set()
        for label in self._configured_ship_type_labels():
            normalized_label = self._normalize_ship_type(label)
            if normalized_label and normalized_label not in seen:
                candidates.append(normalized_label)
                seen.add(normalized_label)
        if not candidates:
            for canonical, aliases in self._ship_type_aliases().items():
                normalized_canonical = self._normalize_ship_type(canonical)
                if normalized_canonical and normalized_canonical not in seen:
                    candidates.append(normalized_canonical)
                    seen.add(normalized_canonical)
                for alias in aliases:
                    normalized_alias = self._normalize_ship_type(alias)
                    if normalized_alias and normalized_alias not in seen:
                        candidates.append(normalized_alias)
                        seen.add(normalized_alias)

        matches = []
        for label in sorted(candidates, key=len, reverse=True):
            label_tokens = re.findall(r"[a-z0-9]+", label)
            if len(label_tokens) < 2:
                continue
            match_start = self._ordered_token_match_start(row_tokens, label_tokens, max_gap=12)
            if match_start is None:
                continue
            matches.append((match_start, label))

        return [label for _start, label in sorted(matches, key=lambda item: (item[0], -len(item[1])))]

    def _ordered_token_match_start(self, row_tokens, label_tokens, max_gap=6):
        if not row_tokens or not label_tokens:
            return None
        for start_idx, token in enumerate(row_tokens):
            if token != label_tokens[0]:
                continue
            current_idx = start_idx
            matched = True
            for label_token in label_tokens[1:]:
                next_idx = None
                search_end = min(len(row_tokens), current_idx + max_gap + 2)
                for idx in range(current_idx + 1, search_end):
                    if row_tokens[idx] == label_token:
                        next_idx = idx
                        break
                if next_idx is None:
                    matched = False
                    break
                current_idx = next_idx
            if matched:
                return start_idx
        return None

    def _extract_seajobs_company_occurrence_counts(self, section_text):
        lines = [" ".join(line.split()) for line in str(section_text or "").splitlines() if line.strip()]
        counts = Counter()
        for line in lines:
            if "/" not in line:
                continue
            upper = line.upper()
            if "COMPANY NAME / SHIP TYPE" in upper or upper.startswith("# "):
                continue
            prefix = line.split("/", 1)[0]
            normalized = self._normalize_seajobs_company_name(prefix)
            if normalized:
                counts[normalized] += 1
        return counts

    def _extract_last_sign_off_fact_from_text(self, raw_text, original_path=None, reference_date=None):
        text = str(raw_text or "")
        if not text.strip():
            return {
                "last_sign_off_date": None,
                "last_sign_off_months_ago": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": None,
            }

        path_name = Path(original_path).name if original_path else ""
        if path_name.upper().startswith("EMAIL_"):
            return {
                "last_sign_off_date": None,
                "last_sign_off_months_ago": None,
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "email_resume_excluded",
            }

        upper = text.upper()
        has_seajobs_banner = (
            "NJORDSHIPS MANAGEMENT INDIA PVT LTD" in upper
            or "NJORSHIPS MANAGEMENT INDIA PVT LTD" in upper
        )
        if "SEAMEN EXPERIENCE DETAILS" not in upper or not has_seajobs_banner:
            return {
                "last_sign_off_date": None,
                "last_sign_off_months_ago": None,
                "status": "SOURCE_EXCLUDED",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "non_seajobs_resume_excluded",
            }

        section = self._extract_seajobs_experience_section(text)
        if not section:
            return {
                "last_sign_off_date": None,
                "last_sign_off_months_ago": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        row_snippets = self._extract_seajobs_experience_row_snippets(section)
        if not row_snippets:
            row_snippets = [" ".join(line.split()) for line in str(section or "").splitlines() if line.strip()]
        last_dates = []
        for line in row_snippets:
            upper_line = line.upper()
            if "COMPANY NAME / SHIP TYPE" in upper_line or upper_line.startswith("# "):
                continue
            ordered_tokens = self._extract_ordered_date_tokens(line)
            if len(ordered_tokens) < 2:
                continue
            sign_off_fact = self._parse_ordered_date_token(ordered_tokens[1])
            if sign_off_fact.get("status") == "PARSED" and sign_off_fact.get("date"):
                last_dates.append(sign_off_fact.get("date"))

        if not last_dates:
            return {
                "last_sign_off_date": None,
                "last_sign_off_months_ago": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history",
                "source_label": "seajobs_resume",
            }

        reference = reference_date or date.today()
        last_sign_off_date = max(last_dates)
        months_ago = max(0, (reference.year - last_sign_off_date.year) * 12 + (reference.month - last_sign_off_date.month))
        if reference.day < last_sign_off_date.day:
            months_ago = max(0, months_ago - 1)

        return {
            "last_sign_off_date": last_sign_off_date,
            "last_sign_off_months_ago": months_ago,
            "status": "PARSED",
            "confidence": 0.9,
            "extraction_method": "seajobs_service_history",
            "source_label": "seajobs_resume",
        }

    def _extract_current_rank_months_fact_from_text(self, raw_text, original_path=None, experience_rows_fact=None):
        experience_rows_fact = experience_rows_fact or self._extract_seajobs_experience_rows(raw_text, original_path=original_path)
        status = experience_rows_fact.get("status", "MISSING")
        if status != "PARSED":
            return {
                "months_total": None,
                "matched_rows": 0,
                "status": status,
                "confidence": None,
                "extraction_method": "seajobs_service_history_rank_duration_sum",
                "source_label": experience_rows_fact.get("source_label"),
            }

        current_rank_fact = self._extract_rank_fact_from_text(raw_text)
        current_rank_normalized = current_rank_fact.get("canonical_id")
        if not current_rank_normalized:
            return {
                "months_total": None,
                "matched_rows": 0,
                "status": "UNKNOWN",
                "confidence": None,
                "extraction_method": "seajobs_service_history_rank_duration_sum",
                "source_label": "seajobs_resume",
            }

        total_months = 0
        matched_rows = 0
        for row in experience_rows_fact.get("rows") or []:
            if row.get("rank_normalized") != current_rank_normalized:
                continue
            months = self._compute_service_duration_months(row.get("sign_in_date"), row.get("sign_out_date"))
            if months is None:
                continue
            total_months += months
            matched_rows += 1

        if matched_rows == 0:
            return {
                "months_total": None,
                "matched_rows": 0,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history_rank_duration_sum",
                "source_label": "seajobs_resume",
            }

        return {
            "months_total": total_months,
            "matched_rows": matched_rows,
            "status": "PARSED",
            "confidence": 0.9,
            "extraction_method": "seajobs_service_history_rank_duration_sum",
            "source_label": "seajobs_resume",
            "current_rank_normalized": current_rank_normalized,
        }

    def _extract_contract_gap_fact_from_text(self, raw_text, original_path=None, reference_date=None, experience_rows_fact=None):
        experience_rows_fact = experience_rows_fact or self._extract_seajobs_experience_rows(raw_text, original_path=original_path)
        status = experience_rows_fact.get("status", "MISSING")
        if status != "PARSED":
            return {
                "has_gap_over_6_months": None,
                "max_gap_days": None,
                "max_gap_start": None,
                "max_gap_end": None,
                "status": status,
                "confidence": None,
                "extraction_method": "seajobs_service_history_gap_scan",
                "source_label": experience_rows_fact.get("source_label"),
            }

        valid_rows = []
        for row in experience_rows_fact.get("rows") or []:
            sign_in_date = row.get("sign_in_date")
            sign_out_date = row.get("sign_out_date")
            if not sign_in_date or not sign_out_date or sign_out_date < sign_in_date:
                continue
            valid_rows.append(row)

        if len(valid_rows) < 2:
            return {
                "has_gap_over_6_months": None,
                "max_gap_days": None,
                "max_gap_start": None,
                "max_gap_end": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history_gap_scan",
                "source_label": "seajobs_resume",
            }

        reference = reference_date or date.today()
        lookback_cutoff = reference - timedelta(days=5 * 365)
        valid_rows = [
            row for row in valid_rows
            if (
                (row.get("sign_in_date") and row.get("sign_in_date") >= lookback_cutoff)
                or (row.get("sign_out_date") and row.get("sign_out_date") >= lookback_cutoff)
            )
        ]

        if len(valid_rows) < 2:
            return {
                "has_gap_over_6_months": None,
                "max_gap_days": None,
                "max_gap_start": None,
                "max_gap_end": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "seajobs_service_history_gap_scan",
                "source_label": "seajobs_resume",
            }

        valid_rows.sort(key=lambda row: (row.get("sign_in_date"), row.get("sign_out_date")))
        max_gap_days = 0
        max_gap_after_sign_out = None
        max_gap_before_sign_in = None
        for previous_row, next_row in zip(valid_rows, valid_rows[1:]):
            previous_sign_out = previous_row.get("sign_out_date")
            next_sign_in = next_row.get("sign_in_date")
            if not previous_sign_out or not next_sign_in:
                continue
            gap_days = max(0, (next_sign_in - previous_sign_out).days - 1)
            if gap_days > max_gap_days:
                max_gap_days = gap_days
                max_gap_after_sign_out = previous_sign_out
                max_gap_before_sign_in = next_sign_in

        return {
            "has_gap_over_6_months": max_gap_days > 183,
            "max_gap_days": max_gap_days,
            "max_gap_start": max_gap_after_sign_out,
            "max_gap_end": max_gap_before_sign_in,
            "status": "PARSED",
            "confidence": 0.9,
            "extraction_method": "seajobs_service_history_gap_scan",
            "source_label": "seajobs_resume",
            "gap_after_sign_out": max_gap_after_sign_out,
            "gap_before_sign_in": max_gap_before_sign_in,
        }

    def _normalize_seajobs_company_name(self, raw_company):
        text = " ".join(str(raw_company or "").split())
        if not text:
            return ""

        text = re.sub(r"^\d+\s+", "", text)
        rank_aliases = sorted(self.RANK_ALIAS_TABLE.keys(), key=len, reverse=True)
        stripped = True
        while stripped and text:
            stripped = False
            for alias in rank_aliases:
                alias_pattern = rf"^{re.escape(alias)}\b[\s:.-]*"
                updated = re.sub(alias_pattern, "", text, flags=re.IGNORECASE).strip()
                if updated != text:
                    text = updated
                    stripped = True
                    break

        lowered = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        tokens = [token for token in lowered.split() if token]
        if not tokens:
            return ""

        legal_suffixes = {"ltd", "limited", "pvt", "private", "llp", "co", "company"}
        while tokens and tokens[-1] in legal_suffixes:
            tokens.pop()
        if not tokens:
            return ""

        normalized = "".join(tokens)
        for suffix in (
            "limited",
            "private",
            "company",
            "ltd",
            "pvt",
            "llp",
            "co",
        ):
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
                normalized = normalized[: -len(suffix)]
                break
        for suffix in (
            "shipmanagement",
            "management",
            "shipmgmt",
            "shipping",
            "services",
            "service",
            "maritime",
            "marine",
            "lines",
            "line",
        ):
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
                normalized = normalized[: -len(suffix)]
                break

        normalized = normalized.strip()
        if len(normalized) < 3:
            return ""
        return normalized

    def _extract_experienced_ship_types_from_text(self, raw_text):
        text = self._normalize_ship_type(raw_text)
        if not text:
            return []
        configured_matches = self._extract_configured_ship_types(text)
        matched = list(configured_matches)
        for canonical, aliases in self._experience_keyword_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                if normalized_alias and re.search(rf'\b{re.escape(normalized_alias)}\b', text):
                    matched.append(canonical)
                    break
        if matched:
            return list(dict.fromkeys(matched))
        # Configured labels returned nothing; fall back to hardcoded alias table.
        if not self._configured_ship_type_labels():
            print("[WARN] _extract_experienced_ship_types_from_text: no configured ship-type labels found; "
                  "using hardcoded fallback aliases. Add [ShipTypes] ship_type_options to config.ini.")
        matched = []
        for canonical, aliases in self._ship_type_aliases().items():
            for alias in aliases:
                normalized_alias = self._normalize_ship_type(alias)
                if normalized_alias and re.search(rf'\b{re.escape(normalized_alias)}\b', text):
                    matched.append(canonical)
                    break
        return sorted(set(matched))

    def _extract_date_fact_from_snippet(self, text):
        snippet = str(text or "")
        if not snippet:
            return {"date": None, "status": "MISSING"}
        if re.search(r'(?<!\d)-?0001\b', snippet):
            return {"date": None, "status": "INVALID"}

        if re.search(r'issue\s+date.*expiry\s+date', snippet, flags=re.IGNORECASE):
            date_tokens = re.findall(
                r'\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\s\/\-.]+[A-Za-z]{3,9}[\s\/\-.]+\d{2,4}|[A-Za-z]{3,9}[\s\/\-.]+\d{1,2},?[\s\/\-.]+\d{2,4}',
                snippet,
                flags=re.IGNORECASE,
            )
            if len(date_tokens) >= 2:
                parsed = self._extract_date_fact_from_snippet(date_tokens[1])
                if parsed.get("status") != "MISSING":
                    return parsed

        date_patterns = [
            r'(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})',
            r'(\d{1,2})[\s\/\-.]+([A-Za-z]{3,9})[\s\/\-.]+(\d{2,4})',
            r'([A-Za-z]{3,9})[\s\/\-.]+(\d{1,2}),?[\s\/\-.]+(\d{2,4})',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, snippet, flags=re.IGNORECASE)
            if match:
                parsed = self._build_date_from_match(match.groups(), allow_future=True)
                if parsed:
                    return {"date": parsed, "status": "PARSED"}

        if re.search(r'\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})\b', snippet):
            return {"date": None, "status": "AMBIGUOUS_NUMERIC"}
        return {"date": None, "status": "MISSING"}

    def _extract_ordered_date_tokens(self, text):
        snippet = str(text or "")
        if not snippet:
            return []
        month_pattern = r'(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)'
        return re.findall(
            rf'\d{{4}}[\/\-.]\d{{1,2}}[\/\-.]\d{{1,2}}|\d{{1,2}}[\/\-.]\d{{1,2}}[\/\-.]\d{{2,4}}|\d{{1,2}}[\s\/\-.]+{month_pattern}[\s\/\-.]+\d{{2,4}}|{month_pattern}[\s\/\-.]+\d{{1,2}},?[\s\/\-.]+\d{{2,4}}',
            snippet,
            flags=re.IGNORECASE,
        )

    def _parse_day_first_numeric_date_token(self, token):
        token = str(token or "").strip()
        match = re.fullmatch(r'(\d{1,2})\s*[\/\-.]\s*(\d{1,2})\s*[\/\-.]\s*(\d{2,4})', token)
        if not match:
            return None
        return self._build_date_from_match(match.groups(), allow_future=True)

    def _parse_ordered_date_token(self, token):
        parsed_numeric = self._parse_day_first_numeric_date_token(token)
        if parsed_numeric:
            return {"date": parsed_numeric, "status": "PARSED"}
        return self._extract_date_fact_from_snippet(token)

    def _extract_availability_fact_from_text(self, raw_text, reference_date=None):
        text = str(raw_text or "")
        if not text:
            return {
                "availability_date": None,
                "availability_end_date": None,
                "availability_status": "MISSING",
                "confidence": None,
                "extraction_method": "availability_details_window",
                "source_label": None,
            }

        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        snippets = []
        for idx, line in enumerate(lines):
            if re.search(r"availability details|from date\s*-\s*till date", line, flags=re.IGNORECASE):
                snippets.append(" ".join(lines[idx:idx + 4]))

        if not snippets:
            for match in re.finditer(r"availability details|from date\s*-\s*till date", text, flags=re.IGNORECASE):
                snippets.append(text[match.start():match.start() + 260])

        if not snippets:
            return {
                "availability_date": None,
                "availability_end_date": None,
                "availability_status": "MISSING",
                "confidence": None,
                "extraction_method": "availability_details_window",
                "source_label": None,
            }

        today = reference_date or date.today()
        for snippet in snippets:
            scoped_snippet = snippet
            from_till_match = re.search(r"from date\s*-\s*till date", scoped_snippet, flags=re.IGNORECASE)
            if from_till_match:
                scoped_snippet = scoped_snippet[from_till_match.start():]
            ordered_tokens = self._extract_ordered_date_tokens(scoped_snippet)
            if not ordered_tokens:
                continue
            start_fact = self._parse_ordered_date_token(ordered_tokens[0])
            end_fact = self._parse_ordered_date_token(ordered_tokens[1]) if len(ordered_tokens) >= 2 else {"date": None, "status": "MISSING"}

            statuses = {start_fact.get("status"), end_fact.get("status")}
            if "PARSED" not in statuses and ("AMBIGUOUS_NUMERIC" in statuses or "INVALID" in statuses):
                return {
                    "availability_date": None,
                    "availability_end_date": None,
                    "availability_status": "AMBIGUOUS_NUMERIC" if "AMBIGUOUS_NUMERIC" in statuses else "INVALID",
                    "confidence": 0.75,
                    "extraction_method": "availability_details_window",
                    "source_label": "availability_details",
                }

            start_date = start_fact.get("date")
            if not start_date:
                continue
            end_date = end_fact.get("date")
            status = None
            if start_date <= today and (end_date is None or today <= end_date):
                status = "immediately"
            return {
                "availability_date": start_date,
                "availability_end_date": end_date,
                "availability_status": status or "PARSED",
                "confidence": 0.9 if end_date else 0.85,
                "extraction_method": "availability_details_window",
                "source_label": "availability_details",
            }

        return {
            "availability_date": None,
            "availability_end_date": None,
            "availability_status": "MISSING",
            "confidence": None,
            "extraction_method": "availability_details_window",
            "source_label": "availability_details",
        }

    def _normalize_resume_visa_snippet(self, snippet):
        normalized = str(snippet or "")
        if not normalized:
            return normalized
        replacements = [
            (r"\bu\s*\.?\s*s\s*\.?\s*a\s*\.?\s*visa\b", "US Visa"),
        ]
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        return normalized

    def _extract_dates_before_maritime_crew_visa_label(self, snippet):
        text = str(snippet or "")
        matches = list(re.finditer(r'(?:online\s+)?maritime\s+crew\s+visa\b', text, flags=re.IGNORECASE))
        if not matches:
            return None
        label_match = matches[-1]
        prefix = text[max(0, label_match.start() - 160):label_match.start()]
        ordered_tokens = self._extract_ordered_date_tokens(prefix)
        if len(ordered_tokens) < 2:
            return None
        return {
            "issue_token": ordered_tokens[-2],
            "expiry_token": ordered_tokens[-1],
        }

    def _extract_us_visa_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        if not text:
            return {
                "status": "MISSING",
                "visa_type": None,
                "expiry_date": None,
                "expiry_status": "MISSING",
                "visa_records": [],
            }

        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        snippets = []
        for idx, line in enumerate(lines):
            if re.search(
                r"\bvisa\b|\bc1\s*/\s*d\b|\bc1\s+visa\b|\bd\s+visa\b|\bb1\s*/\s*b2\b|\bmcv\b|\bschengen\b",
                line,
                flags=re.IGNORECASE,
            ):
                combined = " ".join(lines[idx:idx + 4])
                snippets.append(combined)

        if not snippets:
            for match in re.finditer(r"\bvisa\b|\bc1\s*/\s*d\b|\bb1\s*/\s*b2\b|\bmcv\b|\bschengen\b", text, flags=re.IGNORECASE):
                snippets.append(text[max(0, match.start() - 30):match.start() + 220])

        no_visa_patterns = [r"[:\-]\s*other\b", r"[:\-]\s*n/?a\b", r"[:\-]\s*none\b", r"\bvisa\s*[:\-]?\s*other\b"]
        visa_records = []
        saw_no_visa = False

        for snippet in snippets:
            normalized_snippet = self._normalize_resume_visa_snippet(snippet)
            visa_def = self._extract_specific_visa_type_from_prompt(normalized_snippet)
            if visa_def:
                nearby_dates = None
                if visa_def.get("canonical") == "MCV (Australia)":
                    nearby_dates = self._extract_dates_before_maritime_crew_visa_label(normalized_snippet)
                    if not nearby_dates:
                        nearby_dates = self._extract_dates_before_maritime_crew_visa_label(text)
                expiry_snippet = normalized_snippet
                expiry_label = re.search(r"(?:valid\s+until)", normalized_snippet, flags=re.IGNORECASE)
                if expiry_label:
                    expiry_snippet = normalized_snippet[expiry_label.start():]
                else:
                    visa_anchor = re.search(
                        r"\bvisa\b|\bc1\s*/\s*d\b|\bc1\s+visa\b|\bd\s+visa\b|\bb1\s*/\s*b2\b|\bmcv\b|\bschengen\b",
                        normalized_snippet,
                        flags=re.IGNORECASE,
                    )
                    if visa_anchor:
                        expiry_snippet = normalized_snippet[visa_anchor.start():]
                if nearby_dates:
                    parsed = self._parse_day_first_numeric_date_token(nearby_dates["expiry_token"])
                    expiry_fact = {"date": parsed, "status": "PARSED"} if parsed else {"date": None, "status": "MISSING"}
                else:
                    expiry_fact = self._extract_date_fact_from_snippet(expiry_snippet)
                if expiry_fact.get("status") == "AMBIGUOUS_NUMERIC":
                    ordered_tokens = self._extract_ordered_date_tokens(expiry_snippet)
                    if len(ordered_tokens) >= 2:
                        parsed = self._parse_day_first_numeric_date_token(ordered_tokens[1])
                        if parsed:
                            expiry_fact = {"date": parsed, "status": "PARSED"}
                visa_records.append({
                    "status": "PARSED",
                    "visa_type": visa_def["canonical"],
                    "visa_group": visa_def["group"],
                    "expiry_date": expiry_fact.get("date"),
                    "expiry_status": expiry_fact.get("status", "MISSING"),
                })
                continue

            lowered_snippet = normalized_snippet.lower()
            if any(re.search(no_visa_pattern, lowered_snippet, flags=re.IGNORECASE) for no_visa_pattern in no_visa_patterns):
                saw_no_visa = True

        deduped_records = []
        seen = set()
        for record in visa_records:
            key = (
                record.get("visa_type"),
                record.get("expiry_date").isoformat() if record.get("expiry_date") else None,
                record.get("expiry_status"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped_records.append(record)

        if deduped_records:
            primary_record = deduped_records[0]
            return {
                "status": "PARSED",
                "visa_type": primary_record.get("visa_type"),
                "expiry_date": primary_record.get("expiry_date"),
                "expiry_status": primary_record.get("expiry_status"),
                "visa_records": deduped_records,
            }

        if saw_no_visa:
            return {
                "status": "PARSED_NO_VISA",
                "visa_type": None,
                "expiry_date": None,
                "expiry_status": "MISSING",
                "visa_records": [],
            }

        return {
            "status": "MISSING",
            "visa_type": None,
            "expiry_date": None,
            "expiry_status": "MISSING",
            "visa_records": [],
        }

    def _extract_passport_expiry_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        if not text:
            return {
                "expiry_date": None,
                "expiry_status": "MISSING",
                "confidence": None,
                "extraction_method": "labeled_field_regex",
                "source_label": None,
            }

        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        snippets = []
        for idx, line in enumerate(lines):
            if re.search(r"passport", line, flags=re.IGNORECASE):
                snippets.append(" ".join(lines[idx:idx + 3]))

        if not snippets:
            for match in re.finditer(r"passport", text, flags=re.IGNORECASE):
                snippets.append(text[max(0, match.start() - 30):match.start() + 180])

        if not snippets:
            return {
                "expiry_date": None,
                "expiry_status": "MISSING",
                "confidence": None,
                "extraction_method": "labeled_field_regex",
                "source_label": None,
            }

        for snippet in snippets:
            expiry_fact = self._extract_date_fact_from_snippet(snippet)
            if expiry_fact.get("status") == "AMBIGUOUS_NUMERIC":
                passport_match = re.search(r"passport\b", snippet, flags=re.IGNORECASE)
                scoped_snippet = snippet[passport_match.start():] if passport_match else snippet
                ordered_tokens = self._extract_ordered_date_tokens(scoped_snippet)
                if len(ordered_tokens) >= 2:
                    parsed = self._parse_day_first_numeric_date_token(ordered_tokens[1])
                    if parsed:
                        expiry_fact = {"date": parsed, "status": "PARSED"}
            if expiry_fact.get("status") in {"PARSED", "AMBIGUOUS_NUMERIC", "INVALID"}:
                return {
                    "expiry_date": expiry_fact.get("date"),
                    "expiry_status": expiry_fact.get("status"),
                    "confidence": 0.9 if expiry_fact.get("status") == "PARSED" else 0.75,
                    "extraction_method": "labeled_field_regex",
                    "source_label": "passport",
                }

        return {
            "expiry_date": None,
            "expiry_status": "MISSING",
            "confidence": None,
            "extraction_method": "labeled_field_regex",
            "source_label": "passport",
        }

    def _extract_logistics_from_text(self, raw_text, reference_date=None):
        today = reference_date or date.today()
        passport_fact = self._extract_passport_expiry_fact_from_text(raw_text)
        visa_fact = self._extract_us_visa_fact_from_text(raw_text)
        availability_fact = self._extract_availability_fact_from_text(raw_text, reference_date=today)

        passport_expiry = passport_fact.get("expiry_date")
        passport_expiry_status = passport_fact.get("expiry_status", "MISSING")
        passport_valid = None
        if passport_expiry_status == "PARSED" and passport_expiry:
            passport_valid = passport_expiry >= today

        valid_visa_records = []
        for record in (visa_fact.get("visa_records") or []):
            expiry_date = record.get("expiry_date")
            expiry_status = record.get("expiry_status")
            if expiry_status == "PARSED" and expiry_date and expiry_date >= today:
                valid_visa_records.append(record)

        if valid_visa_records:
            primary_visa = valid_visa_records[0]
            us_visa_valid = True
            us_visa_status = primary_visa.get("visa_type")
            us_visa_expiry_date = primary_visa.get("expiry_date")
        elif visa_fact.get("status") == "PARSED_NO_VISA":
            us_visa_valid = False
            us_visa_status = None
            us_visa_expiry_date = None
        elif visa_fact.get("visa_records"):
            us_visa_valid = False
            us_visa_status = (visa_fact.get("visa_records")[0] or {}).get("visa_type")
            us_visa_expiry_date = None
        else:
            us_visa_valid = None
            us_visa_status = None
            us_visa_expiry_date = None

        return {
            "passport_expiry_date": passport_expiry,
            "passport_expiry_status": passport_expiry_status,
            "passport_valid": passport_valid,
            "us_visa_valid": us_visa_valid,
            "us_visa_status": us_visa_status,
            "us_visa_expiry_date": us_visa_expiry_date,
            "availability_date": availability_fact.get("availability_date"),
            "availability_end_date": availability_fact.get("availability_end_date"),
            "availability_status": availability_fact.get("availability_status"),
            "passport_fact": passport_fact,
            "visa_fact": visa_fact,
            "availability_fact": availability_fact,
        }

    def _extract_coc_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        if not text:
            return {
                "status": "MISSING",
                "grade": None,
                "expiry_date": None,
                "expiry_status": "MISSING",
                "confidence": None,
                "extraction_method": "labeled_field_regex",
                "source_label": None,
            }

        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        snippets = []
        coc_label_pattern = r"\bc\s*\.?\s*o\s*\.?\s*c\b|certificate\s+of\s+competency|highest\s+license\s+held"
        table_grade_patterns = [
            r"master\s*\((?:fg|ncv)\)",
            r"master\s+f\.?g\b|master\s+fg\b",
            r"chief\s+officer\s*\((?:fg|ncv)\)",
            r"chief\s+mate\s*\((?:fg|ncv)\)",
            r"first\s+mate\s*\((?:fg|ncv)\)",
            r"chief\s+mate\s+fg|first\s+mate\s+fg",
            r"second\s+mate\s*\((?:fg|ncv)\)|second\s+officer\s*\((?:fg|ncv)\)|2nd\s+officer\s*\((?:fg|ncv)\)",
            r"second\s+mate\s+fg|2nd\s+mate\s+fg",
            r"third\s+officer\s*\((?:fg|ncv)\)|3rd\s+officer\s*\((?:fg|ncv)\)",
            r"third\s+mate\s+fg|3rd\s+mate\s+fg",
            r"chief\s+engineer\s*\((?:fg|ncv)\)",
            r"second\s+engineer\s*\((?:fg|ncv)\)|2nd\s+engineer\s*\((?:fg|ncv)\)",
            r"third\s+engineer\s*\((?:fg|ncv)\)|3rd\s+engineer\s*\((?:fg|ncv)\)",
            r"fourth\s+engineer\s*\((?:fg|ncv)\)|4th\s+engineer\s*\((?:fg|ncv)\)",
            r"meo\s+cl(?:ass)?[-\s]*(?:i{1,3}|iv|1|2|3|4)\b",
            r"meo\s+class\s+i\s*(?:\(\s*motor\s*\))?\b",
            r"meo\s+class\s+ii\s*(?:\(\s*motor\s*\))?\b",
            r"meo\s+class\s+iv\b",
        ]
        date_token_pattern = (
            r"\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2}"
            r"|\d{1,2}[\s\/\-.]+[A-Za-z]{3,9}[\s\/\-.]+\d{2,4}"
            r"|[A-Za-z]{3,9}[\s\/\-.]+\d{1,2},?[\s\/\-.]+\d{2,4}"
        )
        for idx, line in enumerate(lines):
            if re.search(coc_label_pattern, line, flags=re.IGNORECASE):
                snippets.append(" ".join(lines[idx:idx + 4]))
                continue
            if (
                any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in table_grade_patterns)
                and len(re.findall(date_token_pattern, line, flags=re.IGNORECASE)) >= 2
            ):
                context_start = max(0, idx - 1)
                snippets.append(" ".join(lines[context_start:idx + 1]))

        if not snippets:
            for match in re.finditer(coc_label_pattern, text, flags=re.IGNORECASE):
                snippets.append(text[max(0, match.start() - 30):match.start() + 220])

        if not snippets:
            return {
                "status": "MISSING",
                "grade": None,
                "expiry_date": None,
                "expiry_status": "MISSING",
                "confidence": None,
                "extraction_method": "labeled_field_regex",
                "source_label": None,
            }

        grade_patterns = [
            (r"meo\s+cl(?:ass)?[-\s]*4\b|meo\s+cl(?:ass)?[-\s]*iv\b", "4th_engineer"),
            (r"meo\s+cl(?:ass)?[-\s]*2\b|meo\s+cl(?:ass)?[-\s]*ii\b", "2nd_engineer"),
            (r"meo\s+cl(?:ass)?[-\s]*1\b|meo\s+cl(?:ass)?[-\s]*i\b", "chief_engineer"),
            (r"meo\s+class\s+i\s*(?:\(\s*motor\s*\))?\b", "chief_engineer"),
            (r"meo\s+class\s+ii\s*(?:\(\s*motor\s*\))?\b", "2nd_engineer"),
            (r"meo\s+class\s+iv\b", "4th_engineer"),
            (r"chief\s+engineer", "chief_engineer"),
            (r"second\s+engineer|2nd\s+engineer", "2nd_engineer"),
            (r"third\s+engineer|3rd\s+engineer", "3rd_engineer"),
            (r"fourth\s+engineer|4th\s+engineer", "4th_engineer"),
            (r"master\s+f\.?g\b|master\s+fg\b|master", "master"),
            (r"first\s+mate\s+fg|chief\s+mate\s+fg|first\s+mate|chief\s+mate|chief\s+officer", "chief_officer"),
            (r"second\s+mate\s+fg|2nd\s+mate\s+fg|second\s+mate|2nd\s+officer|second\s+officer", "2nd_officer"),
            (r"third\s+mate\s+fg|3rd\s+mate\s+fg|third\s+mate|3rd\s+officer|third\s+officer", "3rd_officer"),
        ]

        for snippet in snippets:
            normalized_snippet = snippet.lower()
            grade_scan_snippet = normalized_snippet
            for cue_pattern in [r"highest\s+license\s+held", r"c\s*\.?\s*o\s*\.?\s*c\s+held"]:
                cue_match = re.search(cue_pattern, grade_scan_snippet)
                if cue_match:
                    grade_scan_snippet = grade_scan_snippet[cue_match.start():cue_match.start() + 120]
                    break
            grade = None
            for pattern, canonical_grade in grade_patterns:
                if re.search(pattern, grade_scan_snippet):
                    grade = canonical_grade
                    break

            date_tokens = re.findall(date_token_pattern, snippet, flags=re.IGNORECASE)
            if len(date_tokens) >= 2:
                expiry_fact = self._extract_date_fact_from_snippet(date_tokens[1])
            else:
                expiry_fact = self._extract_date_fact_from_snippet(snippet)

            if expiry_fact.get("status") == "PARSED":
                return {
                    "status": "PARSED",
                    "grade": grade,
                    "expiry_date": expiry_fact.get("date"),
                    "expiry_status": "PARSED",
                    "confidence": 0.9,
                    "extraction_method": "labeled_field_regex",
                    "source_label": "coc",
                }

            if expiry_fact.get("status") in {"AMBIGUOUS_NUMERIC", "INVALID"}:
                return {
                    "status": "PARSED",
                    "grade": grade,
                    "expiry_date": None,
                    "expiry_status": expiry_fact.get("status"),
                    "confidence": 0.75,
                    "extraction_method": "labeled_field_regex",
                    "source_label": "coc",
                }

            return {
                "status": "PARSED",
                "grade": grade,
                "expiry_date": None,
                "expiry_status": "MISSING",
                "confidence": 0.7,
                "extraction_method": "labeled_field_regex",
                "source_label": "coc",
            }

        return {
            "status": "MISSING",
            "grade": None,
            "expiry_date": None,
            "expiry_status": "MISSING",
            "confidence": None,
            "extraction_method": "labeled_field_regex",
            "source_label": None,
        }

    def _extract_certificate_state(self, raw_text, aliases):
        text = str(raw_text or "")
        if not text.strip():
            return "unknown"

        alias_patterns = [re.escape(alias) for alias in aliases if alias]
        if not alias_patterns:
            return "unknown"

        combined_pattern = r"(?:%s)" % "|".join(alias_patterns)
        matches = list(re.finditer(combined_pattern, text, flags=re.IGNORECASE))
        if not matches:
            return "unknown"

        for match in matches:
            snippet = text[max(0, match.start() - 80):match.start() + 220]
            local_window = text[max(0, match.start() - 20):min(len(text), match.end() + 80)]
            post_alias_window = text[match.end():min(len(text), match.end() + 80)]
            lowered = snippet.lower()
            lowered_local = local_window.lower()
            lowered_before = text[max(0, match.start() - 20):match.start()].lower()
            if re.search(r"\b(?:not held|none|absent)\b", lowered):
                return "absent"
            if re.search(r"\bno\s*$", lowered_before):
                return "absent"
            if re.search(r"\b(expired|lapsed|out of date)\b", lowered_local):
                return "expired"
            if re.search(r"\b(pending|in progress|applied for)\b", lowered):
                return "unknown"
            if re.search(r"\b(?:cop|certificate of proficiency|proficiency certificate)\b", lowered_local):
                return "present"

            # Dates should only drive certificate validity when the alias carries
            # a nearby certificate-local cue. This prevents expiry dates from
            # later endorsement rows from being attached to dense course lists.
            if re.search(
                r"\b(valid until|valid till|validity|expires|expires on|expiry date|exp date|exp\b)\b",
                lowered_local,
            ):
                expiry_fact = self._extract_date_fact_from_snippet(post_alias_window)
                if expiry_fact.get("status") == "MISSING":
                    expiry_fact = self._extract_date_fact_from_snippet(local_window)
                if expiry_fact.get("status") == "PARSED":
                    if expiry_fact.get("date") and expiry_fact["date"] < date.today():
                        return "expired"
                    return "present"

            if re.search(r"\b(held|valid|completed|certificate|course|courses|training|endorsement|familiarization|familiarisation|support|management)\b", lowered):
                return "present"

        return "unknown"

    def _certificate_has_pending_cue(self, raw_text, aliases):
        text = str(raw_text or "")
        if not text.strip():
            return False
        alias_patterns = [re.escape(alias) for alias in aliases if alias]
        if not alias_patterns:
            return False
        combined_pattern = r"(?:%s)" % "|".join(alias_patterns)
        for match in re.finditer(combined_pattern, text, flags=re.IGNORECASE):
            snippet = text[max(0, match.start() - 40):match.start() + 220].lower()
            if re.search(r"\b(pending|in progress|applied for)\b", snippet):
                return True
        return False

    def _extract_stcw_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        certificate_aliases = {
            "pst": ["pst", "p.s.t", "personal survival techniques", "personal survival technique"],
            "fpff": ["fpff", "f.p.f.f", "fire prevention and fire fighting", "fire prevention & fire fighting"],
            "efa": ["efa", "e.f.a", "elementary first aid"],
            "pssr": [
                "pssr",
                "p.s.s.r",
                "personal safety and social responsibilities",
                "personal safety and social responsibility",
                "personal safety & social responsibilities",
                "personal safety & social responsibility",
            ],
        }

        certificate_states = {
            cert_id: self._extract_certificate_state(text, aliases)
            for cert_id, aliases in certificate_aliases.items()
        }

        alias_presence = {
            cert_id: any(re.search(re.escape(alias), text, flags=re.IGNORECASE) for alias in aliases)
            for cert_id, aliases in certificate_aliases.items()
        }
        pending_cues = {
            cert_id: self._certificate_has_pending_cue(text, aliases)
            for cert_id, aliases in certificate_aliases.items()
        }

        # Resume corpora often list all four STCW basic courses in a compact
        # certificate block without dates or explicit "valid" wording. When all
        # four aliases are present and none has explicit negative evidence, treat
        # that dense list pattern as positive presence evidence instead of routing
        # the entire block to UNKNOWN.
        if (
            all(alias_presence.values())
            and not any(state in {"expired", "absent"} for state in certificate_states.values())
            and not any(pending_cues.values())
        ):
            certificate_states = {
                cert_id: ("present" if alias_presence.get(cert_id) and state == "unknown" else state)
                for cert_id, state in certificate_states.items()
            }

        state_values = list(certificate_states.values())
        if all(state == "present" for state in state_values):
            stcw_basic_all_valid = True
        elif any(state in {"expired", "absent"} for state in state_values):
            stcw_basic_all_valid = False
        else:
            stcw_basic_all_valid = None

        return {
            "certificates": certificate_states,
            "stcw_basic_all_valid": stcw_basic_all_valid,
            "confidence": 0.9 if any(state != "unknown" for state in state_values) else None,
            "status": "PARSED" if any(state != "unknown" for state in state_values) else "MISSING",
            "extraction_method": "keyword_regex",
            "source_label": "stcw_basic",
        }

    def _extract_endorsements_from_text(self, raw_text):
        text = str(raw_text or "")
        endorsement_aliases = {
            "igf_advanced_cop": [
                "advanced igf cop",
                "advanced igf endorsement",
                "advanced igf certificate",
                "advanced igf certificate of proficiency",
                "igf advanced cop",
                "igf advanced endorsement",
                "igf advanced certificate",
                "certificate of proficiency in advanced training for ships subject to the igf code",
                "certificate of proficiency advanced training for ships subject to the igf code",
                "advanced training for ships subject to the igf code",
                "advanced training for service on ships subject to the igf code",
                "advanced training for ships using fuels covered by the igf code",
            ],
            "igf_basic_cop": [
                "basic igf cop",
                "basic igf certificate",
                "basic igf certificate of proficiency",
                "igf basic cop",
                "igf basic certificate",
                "certificate of proficiency in basic training for ships subject to the igf code",
                "certificate of proficiency basic training for ships subject to the igf code",
                "basic training for ships subject to the igf code",
                "basic training for service on ships subject to the igf code",
                "basic training for ships using fuels covered by the igf code",
            ],
            "tanker_oil": [
                "oil tanker endorsement",
                "oil tanker dce",
                "oil tanker dc",
                "oil dc",
                "oil tanker support",
                "oil tanker management",
            ],
            "tanker_oil_basic_cop": [
                "basic oil tanker cop",
                "basic oil tanker certificate",
                "oil tanker basic",
                "oil tanker support",
                "oil tanker dc support",
                "oil tanker dce support",
            ],
            "tanker_oil_advanced_cop": [
                "advanced oil tanker cop",
                "advanced oil tanker certificate",
                "oil tanker advanced",
                "oil tanker management",
                "oil tanker dc management",
                "oil tanker dce management",
            ],
            "tanker_chemical": [
                "chemical tanker endorsement",
                "chemical tanker dce",
                "chemical tanker dc",
                "chemical dc",
                "chemical tanker support",
                "chemical tanker management",
            ],
            "tanker_chemical_basic_cop": [
                "basic chemical tanker cop",
                "basic chemical tanker certificate",
                "chemical tanker basic",
                "chemical tanker support",
                "chemical tanker dc support",
                "chemical tanker dce support",
            ],
            "tanker_chemical_advanced_cop": [
                "advanced chemical tanker cop",
                "advanced chemical tanker certificate",
                "chemical tanker advanced",
                "chemical tanker management",
                "chemical tanker dc management",
                "chemical tanker dce management",
            ],
            "tanker_gas": [
                "gas tanker endorsement",
                "gas tanker dce",
                "gas tanker dc",
                "gas dc",
                "gas tanker support",
                "gas tanker management",
                "lpg carrier support",
                "lpg carrier management",
                "lng carrier support",
                "lng carrier management",
                "liquefied gas tanker support",
                "liquefied gas tanker management",
            ],
            "tanker_gas_basic_cop": [
                "basic gas tanker cop",
                "basic gas tanker certificate",
                "basic liquefied gas tanker cop",
                "gas tanker basic",
                "gas tanker support",
                "gas tanker dc support",
                "gas tanker dce support",
                "lpg carrier support",
                "lng carrier support",
                "liquefied gas tanker support",
            ],
            "tanker_gas_advanced_cop": [
                "advanced gas tanker cop",
                "advanced gas tanker certificate",
                "advanced liquefied gas tanker cop",
                "gas tanker advanced",
                "gas tanker management",
                "gas tanker dc management",
                "gas tanker dce management",
                "lpg carrier management",
                "lng carrier management",
                "liquefied gas tanker management",
            ],
            "cert_ecdis": [
                "ecdis",
                "electronic chart display",
                "electronic chart display and information system",
            ],
            "cert_arpa": [
                "arpa",
                "automatic radar plotting aid",
                "automatic radar plotting aids",
            ],
            "cert_brm_btm": [
                "brm",
                "btm",
                "bridge resource management",
                "bridge team management",
            ],
            "cert_erm": [
                "erm",
                "errm",
                "engine resource management",
                "engine room resource management",
            ],
            "cert_pscrb": [
                "pscrb",
                "proficiency in survival craft",
                "proficiency in survival craft and rescue boat",
                "proficiency in survival craft and rescue boats",
                "survival craft and rescue boat",
                "survival craft and rescue boats",
            ],
            "cert_aff": [
                "aff",
                "advanced fire fighting",
                "advance fire fighting",
            ],
            "cert_mfa": [
                "mfa",
                "medical first aid",
            ],
            "cert_medical_care": [
                "medical care",
                "medical care certificate",
                "medicare",
            ],
            "cert_sso": [
                "sso",
                "ship security officer",
                "ship security officer certificate",
            ],
            "dp_operational": ["dpo", "dp operator"],
            "gmdss": ["gmdss"],
        }
        states = {
            endorsement_id: self._extract_certificate_state(text, aliases)
            for endorsement_id, aliases in endorsement_aliases.items()
        }
        compact = " ".join(text.split())
        dce_match = re.search(r"\bDangerous\s+Cargo\s+Endorsement\b.{0,700}", compact, flags=re.IGNORECASE)
        if dce_match:
            dce_section = dce_match.group(0)
            dce_presence_patterns = {
                "tanker_oil": r"\boil\s+tanker\s+(?:support|management|basic|advanced|dc|dce)\b",
                "tanker_oil_basic_cop": r"\boil\s+tanker\s+(?:support|basic|dc\s+support|dce\s+support)\b",
                "tanker_oil_advanced_cop": r"\boil\s+tanker\s+(?:management|advanced|dc\s+management|dce\s+management)\b",
                "tanker_chemical": r"\bchemical\s+tanker\s+(?:support|management|basic|advanced|dc|dce)\b",
                "tanker_chemical_basic_cop": r"\bchemical\s+tanker\s+(?:support|basic|dc\s+support|dce\s+support)\b",
                "tanker_chemical_advanced_cop": r"\bchemical\s+tanker\s+(?:management|advanced|dc\s+management|dce\s+management)\b",
                "tanker_gas": r"\b(?:gas|lpg|lng|liquefied\s+gas)\s+(?:carrier|tanker)\s+(?:support|management|basic|advanced|dc|dce)\b|\bgas\s+tanker\s+(?:support|management|basic|advanced|dc|dce)\b",
                "tanker_gas_basic_cop": r"\b(?:gas|lpg|lng|liquefied\s+gas)\s+(?:carrier|tanker)\s+(?:support|basic|dc\s+support|dce\s+support)\b|\bgas\s+tanker\s+(?:support|basic|dc\s+support|dce\s+support)\b",
                "tanker_gas_advanced_cop": r"\b(?:gas|lpg|lng|liquefied\s+gas)\s+(?:carrier|tanker)\s+(?:management|advanced|dc\s+management|dce\s+management)\b|\bgas\s+tanker\s+(?:management|advanced|dc\s+management|dce\s+management)\b",
            }
            for endorsement_id, pattern in dce_presence_patterns.items():
                if states.get(endorsement_id) == "unknown" and not re.search(pattern, dce_section, flags=re.IGNORECASE):
                    states[endorsement_id] = "absent"
        course_heading_pattern = (
            r"\bCourse\s+Details\b"
            r"|\bCourses\s+And\s+Certificates\b"
            r"|\bDetails\s*Of\s*Courses\s*&\s*Certificates\s*For\s*Officers\b"
            r"|\bDetailsOfCourses&CertificatesForOfficers\b"
            r"|\bSTCW\s+Cert\.?\s+Details\b"
            r"|\bSTCW\s+Courses\b"
            r"|\bSTCW\s+And\s+Other\s+Certificates\b"
            r"|\bCourses\s+Certificate\s+No\.?\b"
            r"|\bCourses\s*&\s*Certificates?\b"
            r"|\bCertificates\s+Details\b"
        )
        course_match = re.search(rf"(?:{course_heading_pattern}).{{0,3500}}", compact, flags=re.IGNORECASE)
        if course_match:
            course_section = course_match.group(0)
            course_presence_patterns = {
                "igf_advanced_cop": r"\badvanced\s+igf\s+(?:cop|endorsement|certificate)\b|\bigf\s+advanced\s+(?:cop|endorsement|certificate)\b|\badvanced\s+training\b.{0,120}\bigf\s+code\b",
                "igf_basic_cop": r"\bbasic\s+igf\s+(?:cop|endorsement|certificate)\b|\bigf\s+basic\s+(?:cop|endorsement|certificate)\b|\bbasic\s+training\b.{0,120}\bigf\s+code\b",
                "cert_ecdis": r"\becdis\b|\belectronic\s+chart\s+display\b",
                "cert_arpa": r"\barpa\b|\bautomatic\s+radar\s+plotting\s+aid\b",
                "cert_brm_btm": r"\bbrm\b|\bbtm\b|\bbridge\s+(?:resource|team)\s+management\b",
                "cert_erm": r"\berrm\b|\berm\b|\bengine(?:\s+room)?\s+resource(?:\s*.{0,40}\s*management|\s+management)\b",
                "cert_pscrb": r"\bpscrb\b|\bproficiency\s*in\s*survival\s*craft(?:\s*/?\s*r\.?\s*b\.?)?\b|\bsurvival\s*craft\s*(?:and|&)\s*rescue\s*boats?\b",
                "cert_aff": r"\baff\b|\badvanced?\s+fire\s+fighting\b",
                "cert_mfa": r"\bmfa\b|\bmedical\s+first\s+aid\b",
                "cert_medical_care": r"\bmedical\s+care\b|\bmedicare\b",
                "cert_sso": r"\bsso\b|\bship\s+security\s+officer\b",
                "gmdss": r"\bgmdss\b",
            }
            for cert_id, pattern in course_presence_patterns.items():
                if states.get(cert_id) == "unknown":
                    states[cert_id] = (
                        "present"
                        if re.search(pattern, course_section, flags=re.IGNORECASE)
                        else "absent"
                    )
        return states

    def _parse_dob_from_text(self, raw_text):
        dob_fact = self._extract_dob_fact_from_text(raw_text)
        return dob_fact.get("dob")

    def _build_fact_meta(self, value, confidence=None, extraction_method="", status="", source_label="", context=None):
        return {
            "value": value,
            "confidence": confidence,
            "extraction_method": str(extraction_method or ""),
            "status": str(status or ""),
            "source_label": str(source_label or ""),
            "context": context or {},
        }

    def _extract_dob_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        if not text:
            return {"dob": None, "status": "MISSING", "confidence": None, "extraction_method": "label_scan", "source_label": ""}

        label_pattern = r'(?:date\s*/?\s*place\s*of\s*birth|place\s+date\s+of\s+birth|date\s+of\s+birth|dob|d\.o\.b\.?)'
        date_patterns = [
            r'(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})',
            r'(\d{1,2})[\s\/\-.]+([A-Za-z]{3,9})[\s\/\-.]+(\d{2,4})',
            r'(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s\/\-.]+([A-Za-z]{3,9})[\s\/\-.]+(\d{2,4})',
            r'([A-Za-z]{3,9})[\s\/\-.]+(\d{1,2}),?[\s\/\-.]+(\d{2,4})',
        ]
        numeric_date_pattern = r'\b(\d{1,2})[\s\/\-.]+(\d{1,2})[\s\/\-.]+(\d{2,4})\b'

        # Prefer DOB values that appear directly after an explicit DOB label.
        for label_match in re.finditer(label_pattern, text, flags=re.IGNORECASE):
            snippet = text[label_match.start():label_match.start() + 120]
            source_label = label_match.group(0)
            for pattern in date_patterns:
                match = re.search(pattern, snippet, flags=re.IGNORECASE)
                if match:
                    parsed = self._build_date_from_match(match.groups())
                    if parsed and 1940 <= parsed.year <= date.today().year:
                        return {
                            "dob": parsed,
                            "status": "PARSED",
                            "confidence": 0.99,
                            "extraction_method": "label_scan",
                            "source_label": source_label,
                        }

            numeric_match = re.search(numeric_date_pattern, snippet)
            if numeric_match:
                parsed = self._parse_day_first_numeric_date_token(numeric_match.group(0))
                if parsed and 1940 <= parsed.year <= date.today().year:
                    return {
                        "dob": parsed,
                        "status": "PARSED",
                        "confidence": 0.99,
                        "extraction_method": "label_scan",
                        "source_label": source_label,
                    }
                return {
                    "dob": None,
                    "status": "AMBIGUOUS_NUMERIC",
                    "confidence": None,
                    "extraction_method": "label_scan",
                    "source_label": source_label,
                }

        # Generic unlabeled fallback is intentionally conservative. If the resume
        # does not expose a DOB label, it is safer to return UNKNOWN than to grab
        # an arbitrary expiry/employment date and compute a wrong age.
        return {"dob": None, "status": "MISSING", "confidence": None, "extraction_method": "label_scan", "source_label": ""}

    def _extract_stated_age_fact_from_text(self, raw_text):
        text = str(raw_text or "")
        if not text:
            return {
                "age": None,
                "status": "MISSING",
                "confidence": None,
                "extraction_method": "label_scan",
                "source_label": "",
            }

        # Words in the 40 characters before an "age" match that indicate the
        # value is NOT the candidate's age (vessel age, engine age, etc.).
        _DISQUALIFYING_PREFIXES = (
            "vessel", "ship", "document", "course", "sea", "charter", "engine",
            "crew age", "boat", "aircraft",
        )

        label_patterns = [
            r'(?:^|\b)(age)\s*[:\-]?\s*(\d{1,3})\b',
            r'(?:^|\b)(aged)\s+(\d{1,3})\b',
        ]
        for pattern in label_patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                # Check the 40 characters before the match for disqualifying context.
                pre_start = max(0, match.start() - 40)
                pre_text = text[pre_start:match.start()].lower()
                if any(prefix in pre_text for prefix in _DISQUALIFYING_PREFIXES):
                    continue
                try:
                    return {
                        "age": int(match.group(2)),
                        "status": "PARSED",
                        "confidence": 0.95,
                        "extraction_method": "label_scan",
                        "source_label": match.group(1),
                    }
                except Exception as exc:
                    print(f"[WARN] _extract_stated_age_fact_from_text: failed to parse match '{match.group(0)}': {exc}")
                    continue

        return {
            "age": None,
            "status": "MISSING",
            "confidence": None,
            "extraction_method": "label_scan",
            "source_label": "",
        }

    def _build_date_from_match(self, parts, allow_future=False):
        if not parts or len(parts) != 3:
            return None

        month_lookup = {
            "jan": 1, "january": 1,
            "feb": 2, "february": 2,
            "mar": 3, "march": 3,
            "apr": 4, "april": 4,
            "may": 5,
            "jun": 6, "june": 6,
            "jul": 7, "july": 7,
            "aug": 8, "august": 8,
            "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10,
            "nov": 11, "november": 11,
            "dec": 12, "december": 12,
        }

        a, b, c = [str(p).strip() for p in parts]
        try:
            if len(a) == 4 and a.isdigit():
                year = int(a)
                month = int(b)
                day = int(c)
            elif a.isalpha():
                month = month_lookup.get(a.lower())
                day = int(b)
                year = int(c)
            elif b.isalpha():
                day = int(a)
                month = month_lookup.get(b.lower())
                year = int(c)
            else:
                day = int(a)
                month = int(b)
                year = int(c)
            if year < 100:
                year += 1900 if year >= 40 else 2000
            if not month:
                return None
            parsed = date(year, month, day)
            if not allow_future and parsed > date.today():
                return None
            return parsed
        except Exception:
            return None

    def _calculate_age(self, dob_value, reference_date=None):
        if not dob_value:
            return None
        today = reference_date or date.today()
        years = today.year - dob_value.year
        if (today.month, today.day) < (dob_value.month, dob_value.day):
            years -= 1
        return years

    def _validate_age_value(self, age_value):
        if age_value is None:
            return None
        if age_value < 14:
            return {
                "decision": "UNKNOWN",
                "reason_code": "INVALID_AGE_TOO_LOW",
                "message": f"Computed age {age_value} is below the supported minimum of 14 years.",
            }
        if age_value > 100:
            return {
                "decision": "UNKNOWN",
                "reason_code": "INVALID_AGE_TOO_HIGH",
                "message": f"Computed age {age_value} is above the supported maximum of 100 years.",
            }
        return None

    def _check_age_conflict(self, computed_age, stated_age_fact):
        stated_age = stated_age_fact.get("age")
        if computed_age is None or stated_age is None:
            return None
        # Ignore stated age values outside the plausible candidate age range.
        # A value like 0, 5, or 150 is almost certainly an extraction artefact
        # (e.g. "age of vessel: 5"), not the candidate's real age.
        if not (14 <= int(stated_age) <= 80):
            return None
        if abs(int(computed_age) - int(stated_age)) > 2:
            return {
                "decision": "UNKNOWN",
                "reason_code": "DATA_CONFLICT",
                "message": (
                    f"Computed age {computed_age} conflicts with explicitly stated age {stated_age} "
                    f"by more than 2 years."
                ),
            }
        return None

    def _resolve_candidate_age(self, chunks, original_path=None, text_cache=None):
        text_cache = text_cache if text_cache is not None else {}
        source_text = ""
        cache_key = str(original_path) if original_path else None
        extraction_error = ""

        if cache_key:
            source_text = text_cache.get(cache_key, "")
            if not source_text:
                try:
                    source_text = self.pdf_processor.extract_text(str(original_path)) or ""
                except Exception as exc:
                    extraction_error = f"{type(exc).__name__}: {exc}"
                    source_text = ""
                text_cache[cache_key] = source_text

        if not source_text:
            source_text = "\n".join(
                str((chunk.get('metadata') or {}).get('raw_text', ''))
                for chunk in (chunks or [])
            )

        dob_fact = self._extract_dob_fact_from_text(source_text)
        stated_age_fact = self._extract_stated_age_fact_from_text(source_text)
        dob_value = dob_fact.get("dob")
        age = self._calculate_age(dob_value)
        return {
            "dob": dob_value,
            "age": age,
            "dob_parse_status": dob_fact.get("status", "MISSING"),
            "dob_confidence": dob_fact.get("confidence"),
            "dob_extraction_method": dob_fact.get("extraction_method", ""),
            "dob_source_label": dob_fact.get("source_label", ""),
            "stated_age": stated_age_fact.get("age"),
            "stated_age_status": stated_age_fact.get("status", "MISSING"),
            "stated_age_confidence": stated_age_fact.get("confidence"),
            "stated_age_extraction_method": stated_age_fact.get("extraction_method", ""),
            "stated_age_source_label": stated_age_fact.get("source_label", ""),
            "source_text_extraction_error": extraction_error,
        }

    def _build_candidate_facts(self, filename, rank, chunks, original_path=None, text_cache=None, folder_metadata=None):
        age_info = self._resolve_candidate_age(chunks, original_path=original_path, text_cache=text_cache)
        source_text = ""
        if original_path and text_cache is not None:
            source_text = text_cache.get(str(original_path), "")
        if not source_text:
            source_text = "\n".join(
                str((chunk.get('metadata') or {}).get('raw_text', ''))
                for chunk in (chunks or [])
            )
        experienced_ship_types = self._extract_experienced_ship_types_from_text(source_text)
        experienced_engine_types = self._extract_engine_types_from_text(source_text)
        logistics_fact = self._extract_logistics_from_text(source_text)
        metadata_entry = {}
        if folder_metadata:
            metadata_entry = folder_metadata.get(filename) or {}
        applied_ship_types = metadata_entry.get("applied_ship_types")
        if not isinstance(applied_ship_types, list):
            applied_ship_types = []
        current_rank_fact = self._extract_rank_fact_from_text(source_text)
        experience_rows_fact = self._extract_seajobs_experience_rows(source_text, original_path=original_path)
        same_company_fact = self._extract_same_company_contract_count_fact_from_text(source_text, original_path=original_path)
        last_sign_off_fact = self._extract_last_sign_off_fact_from_text(source_text, original_path=original_path)
        current_rank_months_fact = self._extract_current_rank_months_fact_from_text(source_text, original_path=original_path, experience_rows_fact=experience_rows_fact)
        contract_gap_fact = self._extract_contract_gap_fact_from_text(source_text, original_path=original_path, experience_rows_fact=experience_rows_fact)
        coc_fact = self._extract_coc_fact_from_text(source_text)
        stcw_fact = self._extract_stcw_fact_from_text(source_text)
        endorsement_facts = self._extract_endorsements_from_text(source_text)
        applied_rank_raw = str(rank or "").strip() or None
        applied_rank_normalized, applied_department, applied_seniority_bucket, applied_rank_confidence = self._normalize_rank(applied_rank_raw)
        return {
            "candidate_id": filename,
            "facts_version": self.FACTS_VERSION,
            "rank_folder": rank,
            "identity": {
                "full_name": None,
                "nationality": None,
            },
            "role": {
                "current_rank_raw": current_rank_fact.get("raw_rank"),
                "current_rank_normalized": current_rank_fact.get("canonical_id"),
                "applied_rank_raw": applied_rank_raw,
                "applied_rank_normalized": applied_rank_normalized,
                "department": current_rank_fact.get("department") or applied_department,
                "seniority_bucket": current_rank_fact.get("seniority_bucket") or applied_seniority_bucket,
            },
            "certifications": {
                "coc": {
                    "grade": coc_fact.get("grade"),
                    "expiry_date": coc_fact.get("expiry_date").isoformat() if coc_fact.get("expiry_date") else None,
                    "expiry_status": coc_fact.get("expiry_status"),
                    "status": coc_fact.get("status"),
                },
                "stcw_basic_all_valid": stcw_fact.get("stcw_basic_all_valid"),
                "endorsements": endorsement_facts,
            },
            "logistics": {
                "passport_expiry_date": logistics_fact.get("passport_expiry_date").isoformat() if logistics_fact.get("passport_expiry_date") else None,
                "passport_expiry_status": logistics_fact.get("passport_expiry_status"),
                "passport_valid": logistics_fact.get("passport_valid"),
                "us_visa_valid": logistics_fact.get("us_visa_valid"),
                "us_visa_status": logistics_fact.get("us_visa_status"),
                "us_visa_expiry_date": logistics_fact.get("us_visa_expiry_date").isoformat() if logistics_fact.get("us_visa_expiry_date") else None,
                "availability_date": logistics_fact.get("availability_date").isoformat() if logistics_fact.get("availability_date") else None,
                "availability_end_date": logistics_fact.get("availability_end_date").isoformat() if logistics_fact.get("availability_end_date") else None,
                "availability_status": logistics_fact.get("availability_status"),
                "salary_expectation_usd": None,
            },
            "personal": {
                "dob": age_info.get("dob"),
                "dob_parse_status": age_info.get("dob_parse_status"),
                "stated_age": age_info.get("stated_age"),
                "stated_age_status": age_info.get("stated_age_status"),
            },
            "application": {
                "applied_ship_types": applied_ship_types,
            },
            "experience": {
                "vessel_types": experienced_ship_types,
                "engine_types": experienced_engine_types,
                "last_sign_off_date": last_sign_off_fact.get("last_sign_off_date").isoformat() if last_sign_off_fact.get("last_sign_off_date") else None,
                "last_sign_off_months_ago": last_sign_off_fact.get("last_sign_off_months_ago"),
                "service_rows": experience_rows_fact.get("rows") or [],
            },
            "travel": {
                "us_visa_type": logistics_fact.get("visa_fact", {}).get("visa_type"),
                "us_visa_expiry_date": logistics_fact.get("visa_fact", {}).get("expiry_date"),
                "us_visa_status": logistics_fact.get("visa_fact", {}).get("status"),
                "us_visa_expiry_status": logistics_fact.get("visa_fact", {}).get("expiry_status"),
                "visa_records": logistics_fact.get("visa_fact", {}).get("visa_records") or [],
                "visa_types": [record.get("visa_type") for record in (logistics_fact.get("visa_fact", {}).get("visa_records") or []) if record.get("visa_type")],
            },
                "derived": {
                    "age_years": age_info.get("age"),
                    "age_is_cached": True,
                    "same_company_contract_count_max": same_company_fact.get("count"),
                    "current_rank_months_total": current_rank_months_fact.get("months_total"),
                    "has_contract_gap_over_6_months": contract_gap_fact.get("has_gap_over_6_months"),
                    "max_contract_gap_days": contract_gap_fact.get("max_gap_days"),
                    "max_contract_gap_start": contract_gap_fact.get("max_gap_start").isoformat() if contract_gap_fact.get("max_gap_start") else None,
                    "max_contract_gap_end": contract_gap_fact.get("max_gap_end").isoformat() if contract_gap_fact.get("max_gap_end") else None,
                },
            "fact_meta": {
                "personal.dob": self._build_fact_meta(
                    age_info.get("dob").isoformat() if age_info.get("dob") else None,
                    confidence=age_info.get("dob_confidence"),
                    extraction_method=age_info.get("dob_extraction_method"),
                    status=age_info.get("dob_parse_status"),
                    source_label=age_info.get("dob_source_label"),
                    context={
                        "field": "personal.dob",
                        "source_text_extraction_error": age_info.get("source_text_extraction_error", ""),
                    },
                ),
                "personal.stated_age": self._build_fact_meta(
                    age_info.get("stated_age"),
                    confidence=age_info.get("stated_age_confidence"),
                    extraction_method=age_info.get("stated_age_extraction_method"),
                    status=age_info.get("stated_age_status"),
                    source_label=age_info.get("stated_age_source_label"),
                    context={"field": "personal.stated_age"},
                ),
                "derived.age_years": self._build_fact_meta(
                    age_info.get("age"),
                    confidence=age_info.get("dob_confidence"),
                    extraction_method="derived_from_dob",
                    status="PARSED" if age_info.get("age") is not None else "MISSING",
                    source_label=age_info.get("dob_source_label"),
                    context={
                        "field": "derived.age_years",
                        "derived_from": "personal.dob",
                        "source_text_extraction_error": age_info.get("source_text_extraction_error", ""),
                    },
                ),
                "derived.same_company_contract_count_max": self._build_fact_meta(
                    same_company_fact.get("count"),
                    confidence=same_company_fact.get("confidence"),
                    extraction_method=same_company_fact.get("extraction_method", ""),
                    status=same_company_fact.get("status", "MISSING"),
                    source_label=same_company_fact.get("source_label"),
                    context={
                        "field": "derived.same_company_contract_count_max",
                        "derived_from": "experience.seajobs_service_history",
                        "source_scope": "seajobs_only",
                    },
                ),
                "derived.current_rank_months_total": self._build_fact_meta(
                    current_rank_months_fact.get("months_total"),
                    confidence=current_rank_months_fact.get("confidence"),
                    extraction_method=current_rank_months_fact.get("extraction_method", ""),
                    status=current_rank_months_fact.get("status", "MISSING"),
                    source_label=current_rank_months_fact.get("source_label"),
                    context={
                        "field": "derived.current_rank_months_total",
                        "current_rank_normalized": current_rank_months_fact.get("current_rank_normalized"),
                        "matched_rows": current_rank_months_fact.get("matched_rows"),
                        "source_scope": "seajobs_only",
                    },
                ),
                "derived.has_contract_gap_over_6_months": self._build_fact_meta(
                    contract_gap_fact.get("has_gap_over_6_months"),
                    confidence=contract_gap_fact.get("confidence"),
                    extraction_method=contract_gap_fact.get("extraction_method", ""),
                    status=contract_gap_fact.get("status", "MISSING"),
                    source_label=contract_gap_fact.get("source_label"),
                    context={
                        "field": "derived.has_contract_gap_over_6_months",
                        "threshold_days": 183,
                        "max_contract_gap_days": contract_gap_fact.get("max_gap_days"),
                        "lookback_years": 5,
                        "max_contract_gap_start": contract_gap_fact.get("max_gap_start").isoformat() if contract_gap_fact.get("max_gap_start") else None,
                        "max_contract_gap_end": contract_gap_fact.get("max_gap_end").isoformat() if contract_gap_fact.get("max_gap_end") else None,
                        "source_scope": "seajobs_only",
                    },
                ),
                "experience.last_sign_off_date": self._build_fact_meta(
                    last_sign_off_fact.get("last_sign_off_date").isoformat() if last_sign_off_fact.get("last_sign_off_date") else None,
                    confidence=last_sign_off_fact.get("confidence"),
                    extraction_method=last_sign_off_fact.get("extraction_method", ""),
                    status=last_sign_off_fact.get("status", "MISSING"),
                    source_label=last_sign_off_fact.get("source_label"),
                    context={
                        "field": "experience.last_sign_off_date",
                        "last_sign_off_months_ago": last_sign_off_fact.get("last_sign_off_months_ago"),
                        "source_scope": "seajobs_only",
                    },
                ),
                "application.applied_ship_types": self._build_fact_meta(
                    applied_ship_types,
                    confidence=1.0 if applied_ship_types else None,
                    extraction_method="download_manifest",
                    status="PARSED" if applied_ship_types else "MISSING",
                    source_label="manifest.applied_ship_types",
                    context={"field": "application.applied_ship_types"},
                ),
                "experience.vessel_types": self._build_fact_meta(
                    experienced_ship_types,
                    confidence=0.8 if experienced_ship_types else None,
                    extraction_method="resume_keyword_scan",
                    status="PARSED" if experienced_ship_types else "MISSING",
                    source_label="resume_text",
                    context={"field": "experience.vessel_types"},
                ),
                "experience.engine_types": self._build_fact_meta(
                    experienced_engine_types,
                    confidence=0.8 if experienced_engine_types else None,
                    extraction_method="resume_keyword_scan",
                    status="PARSED" if experienced_engine_types else "MISSING",
                    source_label="resume_text",
                    context={"field": "experience.engine_types"},
                ),
                "experience.service_rows": self._build_fact_meta(
                    len(experience_rows_fact.get("rows") or []),
                    confidence=experience_rows_fact.get("confidence"),
                    extraction_method=experience_rows_fact.get("extraction_method", ""),
                    status=experience_rows_fact.get("status", "MISSING"),
                    source_label=experience_rows_fact.get("source_label"),
                    context={"field": "experience.service_rows"},
                ),
                "travel.visa_records": self._build_fact_meta(
                    [record.get("visa_type") for record in (logistics_fact.get("visa_fact", {}).get("visa_records") or [])],
                    confidence=0.9 if logistics_fact.get("visa_fact", {}).get("visa_records") else None,
                    extraction_method="resume_visa_scan",
                    status=logistics_fact.get("visa_fact", {}).get("status", "MISSING"),
                    source_label="resume_text",
                    context={"field": "travel.visa_records"},
                ),
                "role.current_rank_normalized": self._build_fact_meta(
                    current_rank_fact.get("canonical_id"),
                    confidence=current_rank_fact.get("confidence"),
                    extraction_method=current_rank_fact.get("extraction_method", ""),
                    status=current_rank_fact.get("status", "MISSING"),
                    source_label=current_rank_fact.get("source_label"),
                    context={"field": "role.current_rank_normalized"},
                ),
                "role.applied_rank_normalized": self._build_fact_meta(
                    applied_rank_normalized,
                    confidence=applied_rank_confidence,
                    extraction_method="rank_folder_alias_lookup",
                    status="PARSED" if applied_rank_normalized else "UNKNOWN",
                    source_label="rank_folder",
                    context={"field": "role.applied_rank_normalized"},
                ),
                "certifications.coc": self._build_fact_meta(
                    coc_fact.get("status"),
                    confidence=coc_fact.get("confidence"),
                    extraction_method=coc_fact.get("extraction_method", ""),
                    status=coc_fact.get("status", "MISSING"),
                    source_label=coc_fact.get("source_label"),
                    context={"field": "certifications.coc"},
                ),
                "certifications.stcw_basic_all_valid": self._build_fact_meta(
                    stcw_fact.get("stcw_basic_all_valid"),
                    confidence=stcw_fact.get("confidence"),
                    extraction_method=stcw_fact.get("extraction_method", ""),
                    status=stcw_fact.get("status", "MISSING"),
                    source_label=stcw_fact.get("source_label"),
                    context={"field": "certifications.stcw_basic_all_valid"},
                ),
                "certifications.endorsements": self._build_fact_meta(
                    endorsement_facts,
                    confidence=0.9 if any(state != "unknown" for state in endorsement_facts.values()) else None,
                    extraction_method="keyword_regex",
                    status="PARSED" if any(state != "unknown" for state in endorsement_facts.values()) else "MISSING",
                    source_label="resume_text",
                    context={"field": "certifications.endorsements"},
                ),
                "logistics.passport_expiry_date": self._build_fact_meta(
                    logistics_fact.get("passport_expiry_date").isoformat() if logistics_fact.get("passport_expiry_date") else None,
                    confidence=logistics_fact.get("passport_fact", {}).get("confidence"),
                    extraction_method=logistics_fact.get("passport_fact", {}).get("extraction_method", ""),
                    status=logistics_fact.get("passport_expiry_status", "MISSING"),
                    source_label=logistics_fact.get("passport_fact", {}).get("source_label"),
                    context={"field": "logistics.passport_expiry_date"},
                ),
                "logistics.availability_date": self._build_fact_meta(
                    logistics_fact.get("availability_date").isoformat() if logistics_fact.get("availability_date") else None,
                    confidence=logistics_fact.get("availability_fact", {}).get("confidence"),
                    extraction_method=logistics_fact.get("availability_fact", {}).get("extraction_method", ""),
                    status=logistics_fact.get("availability_status", "MISSING"),
                    source_label=logistics_fact.get("availability_fact", {}).get("source_label"),
                    context={
                        "field": "logistics.availability_date",
                        "availability_end_date": (
                            logistics_fact.get("availability_end_date").isoformat()
                            if logistics_fact.get("availability_end_date")
                            else None
                        ),
                    },
                ),
            },
        }

    def _age_constraint_reason(self, constraint):
        if not constraint:
            return ""
        min_age = constraint.get("min_age")
        max_age = constraint.get("max_age")
        if min_age is not None and max_age is not None:
            return f"between {min_age} and {max_age} years old"
        if min_age is not None:
            return f"at least {min_age} years old"
        if max_age is not None:
            return f"at most {max_age} years old"
        return ""

    def _age_within_constraint(self, age_value, constraint):
        if age_value is None or not constraint:
            return None
        min_age = constraint.get("min_age")
        max_age = constraint.get("max_age")
        if min_age is not None and age_value < min_age:
            return False
        if max_age is not None and age_value > max_age:
            return False
        return True

    def _base_rule_result(
        self,
        decision,
        reason_code,
        message,
        actual_value=None,
        expected_value=None,
        confidence=None,
        unknown_reason=None,
    ):
        return {
            "decision": decision,
            "reason_code": reason_code,
            "message": message,
            "actual_value": actual_value,
            "expected_value": expected_value,
            "confidence": confidence,
            "unknown_reason": unknown_reason if decision == "UNKNOWN" else None,
        }

    def _derive_document_quality_hint(self, candidate_facts, evidence_review_reasons):
        fact_meta = (candidate_facts or {}).get("fact_meta") or {}
        key_paths = [
            "personal.dob",
            "travel.visa_records",
            "role.current_rank_normalized",
            "certifications.coc",
            "certifications.stcw_basic_all_valid",
            "logistics.passport_expiry_date",
        ]
        statuses = [
            str((fact_meta.get(path) or {}).get("status") or "").strip().upper()
            for path in key_paths
        ]
        statuses = [status for status in statuses if status]
        if not statuses:
            return None
        if any(status in {"AMBIGUOUS_NUMERIC", "INVALID"} for status in statuses):
            return "usable_but_noisy"
        if evidence_review_reasons and all(status == "MISSING" for status in statuses):
            return "sparse"
        return None

    def _build_default_search_insights(self, candidate_facts):
        derived = (candidate_facts or {}).get("derived") or {}
        fact_meta = (candidate_facts or {}).get("fact_meta") or {}
        insights = {}

        current_rank_months_meta = fact_meta.get("derived.current_rank_months_total") or {}
        current_rank_months = derived.get("current_rank_months_total")
        if current_rank_months_meta.get("status") == "PARSED" and current_rank_months is not None:
            insights["current_rank_months_total"] = current_rank_months

        contract_gap_meta = fact_meta.get("derived.has_contract_gap_over_6_months") or {}
        has_gap = derived.get("has_contract_gap_over_6_months")
        max_gap_days = derived.get("max_contract_gap_days")
        max_gap_start = derived.get("max_contract_gap_start")
        max_gap_end = derived.get("max_contract_gap_end")
        if contract_gap_meta.get("status") == "PARSED" and has_gap is not None:
            insights["has_contract_gap_over_6_months"] = bool(has_gap)
            insights["max_contract_gap_days"] = max_gap_days
            insights["max_contract_gap_start"] = max_gap_start
            insights["max_contract_gap_end"] = max_gap_end

        return insights

    def _derive_evidence_review_metadata(self, hard_filter_result, candidate_facts=None):
        results = list((hard_filter_result or {}).get("results") or [])
        unknown_reason_types = list(dict.fromkeys(
            reason.get("unknown_reason")
            for reason in results
            if reason.get("unknown_reason")
        ))

        if "FACTUAL_UNKNOWN" in unknown_reason_types:
            review_path_type = "factual_unknown"
            evidence_review_state = "insufficient_evidence"
        elif "VERSION_MISMATCH_UNKNOWN" in unknown_reason_types:
            review_path_type = "version_mismatch_unknown"
            evidence_review_state = "partial_evidence"
        else:
            review_path_type = "none"
            evidence_review_state = "sufficient_evidence"

        reason_map = {
            "AGE_MISSING": "age_evidence_missing",
            "AGE_DOB_AMBIGUOUS_FORMAT": "age_evidence_ambiguous",
            "US_VISA_MISSING": "visa_evidence_missing",
            "US_VISA_EXPIRY_MISSING": "visa_evidence_missing",
            "RANK_UNKNOWN": "rank_evidence_incomplete",
            "RANK_CONFIDENCE_LOW": "rank_evidence_incomplete",
            "COC_UNKNOWN": "coc_evidence_incomplete",
            "COC_CONFIDENCE_LOW": "coc_evidence_incomplete",
            "STCW_BASIC_UNKNOWN": "stcw_evidence_incomplete",
            "APPLIED_SHIP_TYPE_MISSING": "document_sparse",
            "EXPERIENCE_SHIP_TYPE_MISSING": "document_sparse",
        }
        evidence_review_reasons = []
        for result in results:
            reason_code = str(result.get("reason_code") or "").strip()
            mapped_reason = reason_map.get(reason_code)
            if mapped_reason and mapped_reason not in evidence_review_reasons:
                evidence_review_reasons.append(mapped_reason)

        if "VERSION_MISMATCH_UNKNOWN" in unknown_reason_types and "version_mismatch_partial_evaluation" not in evidence_review_reasons:
            evidence_review_reasons.append("version_mismatch_partial_evaluation")

        document_quality_hint = self._derive_document_quality_hint(candidate_facts, evidence_review_reasons)

        return {
            "review_path_type": review_path_type,
            "evidence_review_state": evidence_review_state,
            "evidence_review_reasons": evidence_review_reasons,
            "document_quality_hint": document_quality_hint,
        }

    def _evaluate_rule(self, operator, actual_value, expected_value):
        """
        Shared predicate evaluator for deterministic hard-filter rules.

        Returns True (pass), False (fail), or raises ValueError for unsupported
        operators. Never returns None — callers must guard against missing values
        before calling this method.
        """
        if actual_value is None:
            raise ValueError(
                f"_evaluate_rule called with actual_value=None for operator '{operator}'. "
                "Callers must handle missing facts before invoking the evaluator."
            )
        if operator == "between_inclusive":
            min_value = expected_value.get("min")
            max_value = expected_value.get("max")
            if min_value is not None and actual_value < min_value:
                return False
            if max_value is not None and actual_value > max_value:
                return False
            return True
        if operator == "gte":
            return actual_value >= expected_value
        if operator == "lte":
            return actual_value <= expected_value
        if operator == "eq":
            return actual_value == expected_value
        if operator == "contains_any":
            actual_values = set(actual_value or [])
            expected_values = set(expected_value or [])
            return bool(actual_values & expected_values)
        if operator == "contains_all":
            actual_values = set(actual_value or [])
            expected_values = set(expected_value or [])
            return expected_values.issubset(actual_values)
        raise ValueError(f"Unsupported rule operator: '{operator}'")

    def _evaluate_age_rule(self, candidate_facts, constraint):
        if not constraint:
            return self._base_rule_result(
                "PASS",
                "AGE_RULE_NOT_REQUESTED",
                "No age filter requested.",
                actual_value=None,
                expected_value=constraint,
                confidence=None,
            )

        dob_value = ((candidate_facts.get("personal") or {}).get("dob"))
        dob_parse_status = ((candidate_facts.get("personal") or {}).get("dob_parse_status"))
        stated_age = ((candidate_facts.get("personal") or {}).get("stated_age"))
        stated_age_status = ((candidate_facts.get("personal") or {}).get("stated_age_status"))
        age_value = ((candidate_facts.get("derived") or {}).get("age_years"))
        if dob_value is None or age_value is None:
            if dob_parse_status == "AMBIGUOUS_NUMERIC":
                return self._base_rule_result(
                "UNKNOWN",
                "AGE_DOB_AMBIGUOUS_FORMAT",
                (
                    f"DOB was present but in an ambiguous numeric format, so age was left unknown for "
                    f"requested age filter {self._age_constraint_reason(constraint)}."
                ),
                actual_value=None,
                expected_value=constraint,
                confidence=None,
                unknown_reason="FACTUAL_UNKNOWN",
            )
            return self._base_rule_result(
                "UNKNOWN",
                "AGE_MISSING",
                f"Could not determine DOB/age for requested age filter {self._age_constraint_reason(constraint)}.",
                actual_value=None,
                expected_value=constraint,
                confidence=None,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        age_validity = self._validate_age_value(age_value)
        if age_validity:
            return self._base_rule_result(
                age_validity["decision"],
                age_validity["reason_code"],
                age_validity["message"],
                actual_value=age_value,
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("derived.age_years") or {}).get("confidence"),
                unknown_reason="FACTUAL_UNKNOWN" if age_validity["decision"] == "UNKNOWN" else None,
            )

        stated_age_fact = {
            "age": stated_age,
            "status": stated_age_status,
        }
        age_conflict = self._check_age_conflict(age_value, stated_age_fact)
        if age_conflict:
            return self._base_rule_result(
                age_conflict["decision"],
                age_conflict["reason_code"],
                age_conflict["message"],
                actual_value={"computed_age": age_value, "stated_age": stated_age},
                expected_value=constraint,
                confidence=None,
                unknown_reason="FACTUAL_UNKNOWN" if age_conflict["decision"] == "UNKNOWN" else None,
            )

        is_match = self._evaluate_rule(
            "between_inclusive",
            age_value,
            {"min": constraint.get("min_age"), "max": constraint.get("max_age")},
        )
        if is_match:
            return self._base_rule_result(
                "PASS",
                "AGE_IN_RANGE",
                (
                    f"Computed age {age_value} from DOB {dob_value.isoformat()}, which is "
                    f"{self._age_constraint_reason(constraint)}."
                ),
                actual_value=age_value,
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("derived.age_years") or {}).get("confidence"),
            )

        return self._base_rule_result(
            "FAIL",
            "AGE_OUT_OF_RANGE",
            (
                f"Computed age {age_value} from DOB {dob_value.isoformat()}, which is not "
                f"{self._age_constraint_reason(constraint)}."
            ),
            actual_value=age_value,
            expected_value=constraint,
            confidence=((candidate_facts.get("fact_meta") or {}).get("derived.age_years") or {}).get("confidence"),
        )

    def _evaluate_us_visa_rule(self, candidate_facts, constraint, reference_date=None):
        if not constraint:
            return self._base_rule_result(
                "PASS",
                "US_VISA_RULE_NOT_REQUESTED",
                "No visa filter requested.",
                actual_value=None,
                expected_value=constraint,
                confidence=None,
            )

        travel = candidate_facts.get("travel") or {}
        visa_status = travel.get("us_visa_status")
        visa_records = travel.get("visa_records") or []
        accepted_types = constraint.get("accepted_types") or []
        requested_label = constraint.get("requested_label") or "valid visa"
        minimum_months_remaining = constraint.get("minimum_months_remaining")
        today = reference_date or date.today()

        if constraint.get("supported") is False:
            return self._base_rule_result(
                "UNKNOWN",
                "VISA_FILTER_UNSUPPORTED",
                f"Requested filter '{requested_label}' is not yet supported by the deterministic visa parser.",
                actual_value=sorted(set(travel.get("visa_types") or [])),
                expected_value=constraint,
                confidence=None,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if visa_status == "PARSED_NO_VISA":
            return self._base_rule_result(
                "FAIL",
                "US_VISA_NOT_PRESENT",
                f"Resume indicates no visa matching requested filter '{requested_label}'.",
                actual_value=[],
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            )

        if visa_status == "MISSING" or not visa_records:
            return self._base_rule_result(
                "UNKNOWN",
                "US_VISA_MISSING",
                f"Could not determine visa evidence for requested filter '{requested_label}'.",
                actual_value=[],
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                unknown_reason="FACTUAL_UNKNOWN",
            )

        matching_records = [record for record in visa_records if not accepted_types or record.get("visa_type") in accepted_types]
        if not matching_records:
            seen_types = sorted(set(travel.get("visa_types") or []))
            if seen_types:
                return self._base_rule_result(
                    "FAIL",
                    "US_VISA_TYPE_MISMATCH",
                    (
                        f"Resume shows visa types {', '.join(seen_types)}, which do not satisfy requested filter "
                        f"'{requested_label}'."
                    ),
                    actual_value=seen_types,
                    expected_value=constraint,
                    confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                )
            return self._base_rule_result(
                "UNKNOWN",
                "US_VISA_MISSING",
                f"Could not determine visa evidence for requested filter '{requested_label}'.",
                actual_value=[],
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                unknown_reason="FACTUAL_UNKNOWN",
            )

        invalid_records = []
        unknown_records = []
        expired_records = []
        valid_records = []

        for record in matching_records:
            visa_type = record.get("visa_type")
            expiry_date = record.get("expiry_date")
            expiry_status = record.get("expiry_status")

            if expiry_status == "INVALID":
                invalid_records.append(visa_type)
                continue
            if expiry_status == "AMBIGUOUS_NUMERIC":
                unknown_records.append(f"{visa_type} has ambiguous expiry format")
                continue
            if not expiry_date:
                unknown_records.append(f"{visa_type} is present but expiry date is missing")
                continue
            if expiry_date < today:
                expired_records.append((visa_type, expiry_date))
                continue
            valid_records.append((visa_type, expiry_date))

        if valid_records:
            threshold_satisfied = []
            if minimum_months_remaining is not None:
                for visa_type, expiry_date in valid_records:
                    months_remaining = self._months_remaining_from_date(expiry_date, today)
                    if months_remaining is not None and months_remaining >= minimum_months_remaining:
                        threshold_satisfied.append((visa_type, expiry_date, months_remaining))
                if threshold_satisfied:
                    visa_type, expiry_date, months_remaining = threshold_satisfied[0]
                    return self._base_rule_result(
                        "PASS",
                        "US_VISA_VALID",
                        f"Visa {visa_type} is valid until {expiry_date.isoformat()} for requested filter '{requested_label}'.",
                        actual_value={
                            "visa_type": visa_type,
                            "expiry_date": expiry_date.isoformat(),
                            "months_remaining": months_remaining,
                        },
                        expected_value=constraint,
                        confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                    )

                visa_type, expiry_date = max(valid_records, key=lambda item: item[1])
                months_remaining = self._months_remaining_from_date(expiry_date, today)
                return self._base_rule_result(
                    "FAIL",
                    "US_VISA_VALIDITY_WINDOW_TOO_SHORT",
                    (
                        f"Visa {visa_type} is valid until {expiry_date.isoformat()}, but only has "
                        f"{months_remaining} month(s) remaining for requested filter '{requested_label}'."
                    ),
                    actual_value={
                        "visa_type": visa_type,
                        "expiry_date": expiry_date.isoformat(),
                        "months_remaining": months_remaining,
                    },
                    expected_value=constraint,
                    confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                )

            visa_type, expiry_date = valid_records[0]
            return self._base_rule_result(
                "PASS",
                "US_VISA_VALID",
                f"Visa {visa_type} is valid until {expiry_date.isoformat()} for requested filter '{requested_label}'.",
                actual_value={"visa_type": visa_type, "expiry_date": expiry_date.isoformat()},
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            )

        if expired_records:
            visa_type, expiry_date = expired_records[0]
            return self._base_rule_result(
                "FAIL",
                "US_VISA_EXPIRED",
                f"Visa {visa_type} expired on {expiry_date.isoformat()}.",
                actual_value={"visa_type": visa_type, "expiry_date": expiry_date.isoformat()},
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            )

        if invalid_records:
            return self._base_rule_result(
                "FAIL",
                "US_VISA_EXPIRY_INVALID",
                f"Visa {invalid_records[0]} has an invalid expiry date value.",
                actual_value={"visa_type": invalid_records[0], "expiry_date": None},
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            )

        if unknown_records:
            return self._base_rule_result(
                "UNKNOWN",
                "US_VISA_EXPIRY_MISSING",
                unknown_records[0] + ".",
                actual_value={"visa_type": matching_records[0].get("visa_type"), "expiry_date": None},
                expected_value=constraint,
                confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
                unknown_reason="FACTUAL_UNKNOWN",
            )

        return self._base_rule_result(
            "UNKNOWN",
            "US_VISA_MISSING",
            f"Could not determine visa evidence for requested filter '{requested_label}'.",
            actual_value=[],
            expected_value=constraint,
            confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            unknown_reason="FACTUAL_UNKNOWN",
        )

    def _evaluate_passport_validity_rule(self, candidate_facts, constraint, reference_date=None):
        logistics = candidate_facts.get("logistics") or {}
        confidence = ((candidate_facts.get("fact_meta") or {}).get("logistics.passport_expiry_date") or {}).get("confidence")
        requested_label = (constraint or {}).get("requested_label") or "valid passport"
        minimum_months_remaining = (constraint or {}).get("minimum_months_remaining")
        today = reference_date or date.today()

        passport_expiry_raw = logistics.get("passport_expiry_date")
        passport_expiry_status = logistics.get("passport_expiry_status")
        passport_expiry = None
        if passport_expiry_raw:
            try:
                passport_expiry = date.fromisoformat(str(passport_expiry_raw))
            except Exception:
                passport_expiry = None

        if confidence is not None and confidence < 0.85:
            return self._base_rule_result(
                "UNKNOWN",
                "PASSPORT_CONFIDENCE_LOW",
                "Passport validity evidence confidence is below the hard-filter threshold.",
                actual_value={"expiry_date": str(passport_expiry_raw) if passport_expiry_raw else None},
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if passport_expiry_status == "PARSED" and passport_expiry:
            if passport_expiry >= today:
                months_remaining = self._months_remaining_from_date(passport_expiry, today)
                if minimum_months_remaining is not None and months_remaining is not None and months_remaining < minimum_months_remaining:
                    return self._base_rule_result(
                        "FAIL",
                        "PASSPORT_VALIDITY_WINDOW_TOO_SHORT",
                        (
                            f"Passport is valid until {passport_expiry.isoformat()}, but only has "
                            f"{months_remaining} month(s) remaining for requested filter '{requested_label}'."
                        ),
                        actual_value={
                            "expiry_date": passport_expiry.isoformat(),
                            "months_remaining": months_remaining,
                        },
                        expected_value=constraint,
                        confidence=confidence,
                    )
                return self._base_rule_result(
                    "PASS",
                    "PASSPORT_VALID",
                    f"Passport is valid until {passport_expiry.isoformat()} for requested filter '{requested_label}'.",
                    actual_value={
                        "expiry_date": passport_expiry.isoformat(),
                        "months_remaining": months_remaining,
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )
            return self._base_rule_result(
                "FAIL",
                "PASSPORT_EXPIRED",
                f"Passport expired on {passport_expiry.isoformat()}.",
                actual_value={"expiry_date": passport_expiry.isoformat()},
                expected_value=constraint,
                confidence=confidence,
            )

        if passport_expiry_status == "INVALID":
            return self._base_rule_result(
                "UNKNOWN",
                "PASSPORT_EXPIRY_INVALID",
                "Passport evidence is present but the expiry date value is invalid.",
                actual_value={"expiry_date": str(passport_expiry_raw) if passport_expiry_raw else None},
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if passport_expiry_status == "AMBIGUOUS_NUMERIC":
            return self._base_rule_result(
                "UNKNOWN",
                "PASSPORT_EXPIRY_AMBIGUOUS",
                "Passport evidence is present but the expiry date format is ambiguous.",
                actual_value={"expiry_date": str(passport_expiry_raw) if passport_expiry_raw else None},
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        return self._base_rule_result(
            "UNKNOWN",
            "PASSPORT_MISSING",
            f"Could not determine passport validity evidence for requested filter '{requested_label}'.",
            actual_value={"expiry_date": None},
            expected_value=constraint,
            confidence=confidence,
            unknown_reason="FACTUAL_UNKNOWN",
        )

    def _evaluate_availability_rule(self, candidate_facts, constraint, reference_date=None):
        logistics = candidate_facts.get("logistics") or {}
        confidence = ((candidate_facts.get("fact_meta") or {}).get("logistics.availability_date") or {}).get("confidence")
        value_type = (constraint or {}).get("value_type")
        today = reference_date or date.today()

        availability_date_raw = logistics.get("availability_date")
        availability_end_raw = logistics.get("availability_end_date")
        availability_status = logistics.get("availability_status")

        availability_date = None
        if availability_date_raw:
            try:
                availability_date = date.fromisoformat(str(availability_date_raw))
            except Exception:
                availability_date = None
        availability_end_date = None
        if availability_end_raw:
            try:
                availability_end_date = date.fromisoformat(str(availability_end_raw))
            except Exception:
                availability_end_date = None

        if confidence is not None and confidence < 0.85:
            return self._base_rule_result(
                "UNKNOWN",
                "AVAILABILITY_CONFIDENCE_LOW",
                "Availability evidence confidence is below the hard-filter threshold.",
                actual_value={"availability_date": str(availability_date_raw) if availability_date_raw else None},
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if availability_status in {"MISSING", "AMBIGUOUS_NUMERIC", "INVALID"} or availability_date is None:
            return self._base_rule_result(
                "UNKNOWN",
                "AVAILABILITY_MISSING",
                "Could not determine candidate availability evidence reliably.",
                actual_value={
                    "availability_date": str(availability_date_raw) if availability_date_raw else None,
                    "availability_end_date": str(availability_end_raw) if availability_end_raw else None,
                },
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if value_type == "status" and (constraint or {}).get("status") == "immediately":
            if availability_date <= today and (availability_end_date is None or today <= availability_end_date):
                return self._base_rule_result(
                    "PASS",
                    "AVAILABILITY_IMMEDIATE",
                    "Candidate availability window includes today.",
                    actual_value={
                        "availability_date": availability_date.isoformat(),
                        "availability_end_date": availability_end_date.isoformat() if availability_end_date else None,
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )
            return self._base_rule_result(
                "FAIL",
                "AVAILABILITY_NOT_IMMEDIATE",
                "Candidate is not immediately available based on the stated availability window.",
                actual_value={
                    "availability_date": availability_date.isoformat(),
                    "availability_end_date": availability_end_date.isoformat() if availability_end_date else None,
                },
                expected_value=constraint,
                confidence=confidence,
            )

        target_date = None
        if value_type == "date":
            requested_date = (constraint or {}).get("available_from_date")
            if requested_date:
                try:
                    target_date = date.fromisoformat(str(requested_date))
                except Exception:
                    target_date = None
        elif value_type == "relative_phrase":
            relative_days = (constraint or {}).get("relative_days")
            if isinstance(relative_days, int):
                target_date = today + timedelta(days=relative_days)

        if target_date is None:
            return self._base_rule_result(
                "UNKNOWN",
                "AVAILABILITY_CONSTRAINT_UNSUPPORTED",
                "Availability filter could not be normalized into an evaluable date.",
                actual_value={
                    "availability_date": availability_date.isoformat(),
                    "availability_end_date": availability_end_date.isoformat() if availability_end_date else None,
                },
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if availability_date <= target_date and (availability_end_date is None or target_date <= availability_end_date):
            return self._base_rule_result(
                "PASS",
                "AVAILABILITY_WINDOW_MATCH",
                f"Candidate availability window covers {target_date.isoformat()}.",
                actual_value={
                    "availability_date": availability_date.isoformat(),
                    "availability_end_date": availability_end_date.isoformat() if availability_end_date else None,
                },
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "AVAILABILITY_WINDOW_MISMATCH",
            f"Candidate availability window does not cover {target_date.isoformat()}.",
            actual_value={
                "availability_date": availability_date.isoformat(),
                "availability_end_date": availability_end_date.isoformat() if availability_end_date else None,
            },
            expected_value=constraint,
            confidence=confidence,
        )

    def _evaluate_rank_rule(self, candidate_facts, constraint):
        role = candidate_facts.get("role") or {}
        actual_rank = role.get("applied_rank_normalized")
        confidence = ((candidate_facts.get("fact_meta") or {}).get("role.applied_rank_normalized") or {}).get("confidence")
        expected_ranks = (constraint or {}).get("applied_rank_normalized") or []

        if actual_rank is None:
            return self._base_rule_result(
                "UNKNOWN",
                "RANK_UNKNOWN",
                "Could not determine normalized applied rank for rank filter evaluation.",
                actual_value=None,
                expected_value=expected_ranks,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if confidence is not None and confidence < 0.85:
            return self._base_rule_result(
                "UNKNOWN",
                "RANK_CONFIDENCE_LOW",
                "Normalized applied rank confidence is below the hard-filter threshold.",
                actual_value=actual_rank,
                expected_value=expected_ranks,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if self._evaluate_rule("contains_any", [actual_rank], expected_ranks):
            return self._base_rule_result(
                "PASS",
                "RANK_MATCH",
                f"Candidate normalized rank '{actual_rank}' matches the requested rank set.",
                actual_value=actual_rank,
                expected_value=expected_ranks,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "RANK_MISMATCH",
            f"Candidate normalized rank '{actual_rank}' does not match the requested rank set.",
            actual_value=actual_rank,
            expected_value=expected_ranks,
            confidence=confidence,
        )

    def _evaluate_coc_document_gate(self, candidate_facts, constraint, reference_date=None):
        coc = (candidate_facts.get("certifications") or {}).get("coc") or {}
        confidence = ((candidate_facts.get("fact_meta") or {}).get("certifications.coc") or {}).get("confidence")
        today = reference_date or date.today()
        expiry_status = coc.get("expiry_status")
        expiry_date_raw = coc.get("expiry_date")
        grade = coc.get("grade")

        expiry_date = None
        if expiry_date_raw:
            try:
                expiry_date = date.fromisoformat(expiry_date_raw)
            except Exception:
                expiry_date = None

        if confidence is not None and confidence < 0.85:
            return self._base_rule_result(
                "UNKNOWN",
                "COC_CONFIDENCE_LOW",
                "COC evidence confidence is below the hard-filter threshold.",
                actual_value={"grade": grade, "expiry_date": expiry_date_raw},
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if coc.get("status") == "MISSING" and grade is None and expiry_date is None:
            return self._base_rule_result(
                "FAIL",
                "COC_ABSENT",
                "No certificate of competency evidence was found.",
                actual_value={"grade": None, "expiry_date": None},
                expected_value=constraint,
                confidence=confidence,
            )

        if expiry_status == "PARSED" and expiry_date:
            if expiry_date >= today:
                return self._base_rule_result(
                    "PASS",
                    "COC_VALID",
                    f"Certificate of competency is valid until {expiry_date.isoformat()}.",
                    actual_value={"grade": grade, "expiry_date": expiry_date.isoformat()},
                    expected_value=constraint,
                    confidence=confidence,
                )
            return self._base_rule_result(
                "FAIL",
                "COC_EXPIRED",
                f"Certificate of competency expired on {expiry_date.isoformat()}.",
                actual_value={"grade": grade, "expiry_date": expiry_date.isoformat()},
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "UNKNOWN",
            "COC_UNKNOWN",
            "Certificate of competency evidence is present but expiry could not be determined reliably.",
            actual_value={"grade": grade, "expiry_date": expiry_date_raw},
            expected_value=constraint,
            confidence=confidence,
            unknown_reason="FACTUAL_UNKNOWN",
        )

    def _evaluate_coc_grade_rule(self, candidate_facts, constraint):
        coc = (candidate_facts.get("certifications") or {}).get("coc") or {}
        confidence = ((candidate_facts.get("fact_meta") or {}).get("certifications.coc") or {}).get("confidence")
        actual_grade = coc.get("grade")
        expected_grades = (constraint or {}).get("required_grades") or []

        if confidence is not None and confidence < 0.85:
            return self._base_rule_result(
                "UNKNOWN",
                "COC_GRADE_CONFIDENCE_LOW",
                "COC grade evidence confidence is below the hard-filter threshold.",
                actual_value=actual_grade,
                expected_value=expected_grades,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if actual_grade is None:
            return self._base_rule_result(
                "UNKNOWN",
                "COC_GRADE_MISSING",
                "Could not determine certificate of competency grade for this candidate.",
                actual_value=None,
                expected_value=expected_grades,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if self._evaluate_rule("contains_any", [actual_grade], expected_grades):
            return self._base_rule_result(
                "PASS",
                "COC_GRADE_MATCH",
                f"Candidate COC grade '{actual_grade}' matches the requested grade filter.",
                actual_value=actual_grade,
                expected_value=expected_grades,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "COC_GRADE_MISMATCH",
            f"Candidate COC grade '{actual_grade}' does not match the requested grade filter.",
            actual_value=actual_grade,
            expected_value=expected_grades,
            confidence=confidence,
        )

    def _evaluate_stcw_basic_rule(self, candidate_facts, constraint):
        certifications = candidate_facts.get("certifications") or {}
        actual_value = certifications.get("stcw_basic_all_valid")
        confidence = ((candidate_facts.get("fact_meta") or {}).get("certifications.stcw_basic_all_valid") or {}).get("confidence")

        if actual_value is None or (confidence is not None and confidence < 0.80):
            return self._base_rule_result(
                "UNKNOWN",
                "STCW_BASIC_UNKNOWN",
                "STCW basic validity could not be determined with sufficient confidence.",
                actual_value=actual_value,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if actual_value is True:
            return self._base_rule_result(
                "PASS",
                "STCW_BASIC_VALID",
                "All four STCW basic certificates were confirmed valid.",
                actual_value=actual_value,
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "STCW_BASIC_INVALID",
            "One or more STCW basic certificates are absent or expired.",
            actual_value=actual_value,
            expected_value=constraint,
            confidence=confidence,
        )

    def _endorsement_display_label(self, endorsement_id):
        labels = {
            "igf_advanced_cop": "advanced IGF CoP",
            "igf_basic_cop": "basic IGF CoP",
            "tanker_oil": "oil tanker endorsement",
            "tanker_oil_basic_cop": "basic oil tanker CoP",
            "tanker_oil_advanced_cop": "advanced oil tanker CoP",
            "tanker_chemical": "chemical tanker endorsement",
            "tanker_chemical_basic_cop": "basic chemical tanker CoP",
            "tanker_chemical_advanced_cop": "advanced chemical tanker CoP",
            "tanker_gas": "gas tanker endorsement",
            "tanker_gas_basic_cop": "basic gas tanker CoP",
            "tanker_gas_advanced_cop": "advanced gas tanker CoP",
            "cert_ecdis": "ECDIS",
            "cert_arpa": "ARPA",
            "cert_brm_btm": "BRM/BTM",
            "cert_erm": "ERM",
            "cert_pscrb": "PSCRB",
            "cert_aff": "AFF",
            "cert_mfa": "MFA",
            "cert_medical_care": "Medical Care",
            "cert_sso": "SSO",
            "dp_operational": "DPO",
            "gmdss": "GMDSS",
        }
        return labels.get(str(endorsement_id or ""), str(endorsement_id or ""))

    def _evaluate_endorsement_rule(self, candidate_facts, constraint):
        certifications = candidate_facts.get("certifications") or {}
        endorsements = certifications.get("endorsements") or {}
        confidence = ((candidate_facts.get("fact_meta") or {}).get("certifications.endorsements") or {}).get("confidence")
        required_endorsements = (constraint or {}).get("endorsements_required") or []

        if not required_endorsements:
            return self._base_rule_result(
                "PASS",
                "ENDORSEMENT_RULE_NOT_REQUESTED",
                "No endorsement filter requested.",
                actual_value={},
                expected_value=constraint,
                confidence=confidence,
            )

        actual_states = {endorsement_id: endorsements.get(endorsement_id, "unknown") for endorsement_id in required_endorsements}
        required_labels = [self._endorsement_display_label(endorsement_id) for endorsement_id in required_endorsements]
        actual_value = {
            "states": actual_states,
            "labels": {
                endorsement_id: self._endorsement_display_label(endorsement_id)
                for endorsement_id in required_endorsements
            },
        }

        if any(state == "unknown" for state in actual_states.values()):
            unknown_labels = [
                self._endorsement_display_label(endorsement_id)
                for endorsement_id, state in actual_states.items()
                if state == "unknown"
            ]
            return self._base_rule_result(
                "UNKNOWN",
                "ENDORSEMENT_UNKNOWN",
                f"Required endorsement evidence could not be determined: {', '.join(unknown_labels)}.",
                actual_value=actual_value,
                expected_value=required_endorsements,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if any(state in {"expired", "absent"} for state in actual_states.values()):
            missing_labels = [
                f"{self._endorsement_display_label(endorsement_id)} ({state})"
                for endorsement_id, state in actual_states.items()
                if state in {"expired", "absent"}
            ]
            return self._base_rule_result(
                "FAIL",
                "ENDORSEMENT_MISSING_OR_EXPIRED",
                f"Required endorsements are absent or expired: {', '.join(missing_labels)}.",
                actual_value=actual_value,
                expected_value=required_endorsements,
                confidence=confidence,
            )

        return self._base_rule_result(
            "PASS",
            "ENDORSEMENT_VALID",
            f"Required endorsements present and valid: {', '.join(required_labels)}.",
            actual_value=actual_value,
            expected_value=required_endorsements,
            confidence=confidence,
        )

    def _evaluate_recency_rule(self, candidate_facts, constraint):
        experience = candidate_facts.get("experience") or {}
        fact_meta = (candidate_facts.get("fact_meta") or {}).get("experience.last_sign_off_date") or {}
        last_sign_off_months_ago = experience.get("last_sign_off_months_ago")
        last_sign_off_date = experience.get("last_sign_off_date")
        max_months = int((constraint or {}).get("max_months_since_sign_off") or 0)
        status = str(fact_meta.get("status") or "MISSING")
        confidence = fact_meta.get("confidence")

        if status == "SOURCE_EXCLUDED":
            return self._base_rule_result(
                "UNKNOWN",
                "RECENCY_SOURCE_EXCLUDED",
                "Last sign-off recency is currently supported only for SeaJobs-style resumes.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if last_sign_off_months_ago is None or last_sign_off_date is None:
            return self._base_rule_result(
                "UNKNOWN",
                "RECENCY_MISSING",
                "Could not determine last sign-off recency from the selected resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if last_sign_off_months_ago <= max_months:
            return self._base_rule_result(
                "PASS",
                "RECENCY_MATCH",
                f"Candidate last signed off on {last_sign_off_date}, which is within the last {max_months} month(s).",
                actual_value={
                    "last_sign_off_date": last_sign_off_date,
                    "last_sign_off_months_ago": last_sign_off_months_ago,
                },
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "RECENCY_MISMATCH",
            f"Candidate last signed off on {last_sign_off_date}, which is more than {max_months} month(s) ago.",
            actual_value={
                "last_sign_off_date": last_sign_off_date,
                "last_sign_off_months_ago": last_sign_off_months_ago,
            },
            expected_value=constraint,
            confidence=confidence,
        )

    def _evaluate_company_continuity_rule(self, candidate_facts, constraint):
        derived = candidate_facts.get("derived") or {}
        fact_meta = (candidate_facts.get("fact_meta") or {}).get("derived.same_company_contract_count_max") or {}
        actual_count = derived.get("same_company_contract_count_max")
        minimum = int((constraint or {}).get("min_same_company_contract_count") or 0)
        status = str(fact_meta.get("status") or "MISSING")
        confidence = fact_meta.get("confidence")

        if status == "SOURCE_EXCLUDED":
            return self._base_rule_result(
                "UNKNOWN",
                "COMPANY_CONTINUITY_SOURCE_EXCLUDED",
                "Same-company contract count is currently supported only for SeaJobs-style resumes.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if actual_count is None:
            return self._base_rule_result(
                "UNKNOWN",
                "COMPANY_CONTINUITY_MISSING",
                "Could not determine same-company contract history from the selected resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if actual_count >= minimum:
            return self._base_rule_result(
                "PASS",
                "COMPANY_CONTINUITY_MATCH",
                f"Candidate has {actual_count} contract(s) with the same company, meeting the minimum {minimum}.",
                actual_value=actual_count,
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "COMPANY_CONTINUITY_INSUFFICIENT",
            f"Candidate has {actual_count} contract(s) with the same company, below the required minimum {minimum}.",
            actual_value=actual_count,
            expected_value=constraint,
            confidence=confidence,
        )

    def _evaluate_recent_contract_vessel_experience_rule(self, candidate_facts, constraint):
        experience = candidate_facts.get("experience") or {}
        fact_meta = (candidate_facts.get("fact_meta") or {}).get("experience.service_rows") or {}
        service_rows = experience.get("service_rows") or []
        status = str(fact_meta.get("status") or "MISSING")
        confidence = fact_meta.get("confidence")

        if status == "SOURCE_EXCLUDED":
            return self._base_rule_result(
                "UNKNOWN",
                "RECENT_CONTRACT_VESSEL_SOURCE_EXCLUDED",
                "Recent contract vessel experience needs contract service rows that can be parsed safely from the resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        requested_ship_type = self._normalize_ship_type((constraint or {}).get("vessel_type"))
        min_months = int((constraint or {}).get("min_months") or 0)
        lookback_contracts = int((constraint or {}).get("lookback_contracts") or 0)
        if not requested_ship_type or min_months < 0 or lookback_contracts <= 0:
            return self._base_rule_result(
                "UNKNOWN",
                "RECENT_CONTRACT_VESSEL_CONSTRAINT_INVALID",
                "Recent contract vessel experience constraint is incomplete.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        valid_rows = []
        for row in service_rows:
            sign_in_date = row.get("sign_in_date")
            sign_out_date = row.get("sign_out_date")
            if not sign_in_date or not sign_out_date or sign_out_date < sign_in_date:
                continue
            row_ship_types = [self._normalize_ship_type(value) for value in (row.get("vessel_types") or []) if value]
            valid_rows.append({
                "sign_in_date": sign_in_date,
                "sign_out_date": sign_out_date,
                "vessel_types": row_ship_types,
            })

        if not valid_rows:
            return self._base_rule_result(
                "UNKNOWN",
                "RECENT_CONTRACT_VESSEL_MISSING",
                "Could not determine recent contract vessel experience from the selected resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        valid_rows.sort(key=lambda row: (row.get("sign_out_date"), row.get("sign_in_date")), reverse=True)
        recent_rows = valid_rows[:lookback_contracts]

        expected_ship_types = set(self._ship_type_expected_values(requested_ship_type))
        matched_months = 0
        matched_contracts = 0
        parsed_ship_type_rows = 0
        for row in recent_rows:
            row_ship_types = set(row.get("vessel_types") or [])
            if row_ship_types:
                parsed_ship_type_rows += 1
            if not (row_ship_types & expected_ship_types):
                continue
            months = self._compute_service_duration_months(row.get("sign_in_date"), row.get("sign_out_date"))
            if months is None:
                continue
            matched_months += months
            matched_contracts += 1

        if parsed_ship_type_rows == 0:
            return self._base_rule_result(
                "UNKNOWN",
                "RECENT_CONTRACT_VESSEL_UNPARSED",
                "Could not determine vessel types for the candidate's recent contracts.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if min_months == 0 and matched_contracts > 0:
            return self._base_rule_result(
                "PASS",
                "RECENT_CONTRACT_VESSEL_MATCH",
                (
                    f"Candidate has '{requested_ship_type}' experience in {matched_contracts} of "
                    f"the last {lookback_contracts} contract(s)."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(recent_rows),
                },
                expected_value=constraint,
                confidence=confidence,
            )

        if min_months > 0 and matched_months >= min_months:
            return self._base_rule_result(
                "PASS",
                "RECENT_CONTRACT_VESSEL_MATCH",
                (
                    f"Candidate has {matched_months} month(s) on '{requested_ship_type}' across "
                    f"the last {lookback_contracts} contract(s)."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(recent_rows),
                },
                expected_value=constraint,
                confidence=confidence,
            )

        failure_message = (
            f"Candidate has no '{requested_ship_type}' contract(s) in the last {lookback_contracts} contract(s)."
            if min_months == 0
            else (
                f"Candidate has only {matched_months} month(s) on '{requested_ship_type}' across "
                f"the last {lookback_contracts} contract(s), below the required {min_months}."
            )
        )
        return self._base_rule_result(
            "FAIL",
            "RECENT_CONTRACT_VESSEL_INSUFFICIENT",
            failure_message,
            actual_value={
                "matched_months": matched_months,
                "matched_contracts": matched_contracts,
                "evaluated_contracts": len(recent_rows),
            },
            expected_value=constraint,
            confidence=confidence,
        )

    def _evaluate_engine_experience_rule(self, candidate_facts, constraint):
        requested_engine_type = self._normalize_engine_type((constraint or {}).get("engine_type")).replace(" ", "_")
        expected_engine_types = [
            self._normalize_engine_type(value).replace(" ", "_")
            for value in ((constraint or {}).get("expected_values") or self._engine_type_expected_values(requested_engine_type))
            if value
        ]
        experience = candidate_facts.get("experience") or {}
        experienced_engine_types = [
            self._normalize_engine_type(value).replace(" ", "_")
            for value in (experience.get("engine_types") or [])
            if value
        ]
        experienced_engine_types = list(dict.fromkeys(experienced_engine_types))
        fact_meta = (candidate_facts.get("fact_meta") or {}).get("experience.engine_types") or {}
        confidence = fact_meta.get("confidence")

        if not requested_engine_type or not expected_engine_types:
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_EXPERIENCE_CONSTRAINT_INVALID",
                "Engine experience constraint is incomplete.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        min_months = int((constraint or {}).get("min_months") or 0)
        lookback_contracts = int((constraint or {}).get("lookback_contracts") or 0)
        recent_contract_match_mode = str((constraint or {}).get("recent_contract_match_mode") or "any").strip().lower()
        if min_months > 0 or lookback_contracts > 0:
            service_rows = experience.get("service_rows") or []
            valid_rows = []
            for row in service_rows:
                sign_in_date = row.get("sign_in_date")
                sign_out_date = row.get("sign_out_date")
                if not sign_in_date or not sign_out_date or sign_out_date < sign_in_date:
                    continue
                row_engine_types = [
                    self._normalize_engine_type(value).replace(" ", "_")
                    for value in (row.get("engine_types") or [])
                    if value
                ]
                valid_rows.append({
                    "sign_in_date": sign_in_date,
                    "sign_out_date": sign_out_date,
                    "engine_types": row_engine_types,
                })

            if not valid_rows:
                return self._base_rule_result(
                    "UNKNOWN",
                    "ENGINE_EXPERIENCE_ROWS_MISSING",
                    "Could not determine contract-level engine experience from the selected resume.",
                    actual_value=None,
                    expected_value=constraint,
                    confidence=confidence,
                    unknown_reason="FACTUAL_UNKNOWN",
                )

            valid_rows.sort(key=lambda row: (row.get("sign_out_date"), row.get("sign_in_date")), reverse=True)
            evaluated_rows = valid_rows[:lookback_contracts] if lookback_contracts > 0 else valid_rows
            matched_months = 0
            matched_contracts = 0
            parsed_engine_rows = 0
            for row in evaluated_rows:
                row_engine_types = set(row.get("engine_types") or [])
                if row_engine_types:
                    parsed_engine_rows += 1
                if not (row_engine_types & set(expected_engine_types)):
                    continue
                months = self._compute_service_duration_months(row.get("sign_in_date"), row.get("sign_out_date"))
                if months is None:
                    continue
                matched_months += months
                matched_contracts += 1

            if parsed_engine_rows == 0:
                return self._base_rule_result(
                    "FAIL",
                    "ENGINE_EXPERIENCE_MISMATCH",
                    f"Candidate has parsed service rows but no engine evidence matching '{requested_engine_type}'.",
                    actual_value={
                        "matched_months": 0,
                        "matched_contracts": 0,
                        "evaluated_contracts": len(evaluated_rows),
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )

            if lookback_contracts > 0 and min_months == 0 and recent_contract_match_mode == "all":
                if len(evaluated_rows) >= lookback_contracts and matched_contracts == lookback_contracts:
                    return self._base_rule_result(
                        "PASS",
                        "ENGINE_EXPERIENCE_MATCH",
                        (
                            f"Candidate has '{requested_engine_type}' experience in all "
                            f"{lookback_contracts} recent contract(s)."
                        ),
                        actual_value={
                            "matched_months": matched_months,
                            "matched_contracts": matched_contracts,
                            "evaluated_contracts": len(evaluated_rows),
                            "required_contracts": lookback_contracts,
                            "recent_contract_match_mode": recent_contract_match_mode,
                        },
                        expected_value=constraint,
                        confidence=confidence,
                    )
                return self._base_rule_result(
                    "FAIL",
                    "ENGINE_EXPERIENCE_INSUFFICIENT",
                    (
                        f"Candidate has '{requested_engine_type}' experience in "
                        f"{matched_contracts} of the recent {lookback_contracts} contract(s); "
                        f"all {lookback_contracts} were required."
                    ),
                    actual_value={
                        "matched_months": matched_months,
                        "matched_contracts": matched_contracts,
                        "evaluated_contracts": len(evaluated_rows),
                        "required_contracts": lookback_contracts,
                        "recent_contract_match_mode": recent_contract_match_mode,
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )

            if min_months == 0 and matched_contracts > 0:
                return self._base_rule_result(
                    "PASS",
                    "ENGINE_EXPERIENCE_MATCH",
                    (
                        f"Candidate has '{requested_engine_type}' experience in "
                        f"{matched_contracts} contract(s)."
                    ),
                    actual_value={
                        "matched_months": matched_months,
                        "matched_contracts": matched_contracts,
                        "evaluated_contracts": len(evaluated_rows),
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )

            if min_months > 0 and matched_months >= min_months:
                return self._base_rule_result(
                    "PASS",
                    "ENGINE_EXPERIENCE_MATCH",
                    (
                        f"Candidate has {matched_months} month(s) with "
                        f"'{requested_engine_type}'."
                    ),
                    actual_value={
                        "matched_months": matched_months,
                        "matched_contracts": matched_contracts,
                        "evaluated_contracts": len(evaluated_rows),
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )

            return self._base_rule_result(
                "FAIL",
                "ENGINE_EXPERIENCE_INSUFFICIENT",
                (
                    f"Candidate has only {matched_months} month(s) with "
                    f"'{requested_engine_type}', below the required {min_months}."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(evaluated_rows),
                },
                expected_value=constraint,
                confidence=confidence,
            )

        if not experienced_engine_types:
            service_rows = experience.get("service_rows") or []
            row_engine_types = []
            parsed_service_rows = 0
            for row in service_rows:
                if row.get("engine_types"):
                    parsed_service_rows += 1
                for engine_type in row.get("engine_types") or []:
                    normalized_engine_type = self._normalize_engine_type(engine_type).replace(" ", "_")
                    if normalized_engine_type:
                        row_engine_types.append(normalized_engine_type)
            row_engine_types = list(dict.fromkeys(row_engine_types))
            if row_engine_types:
                if set(row_engine_types) & set(expected_engine_types):
                    return self._base_rule_result(
                        "PASS",
                        "ENGINE_EXPERIENCE_MATCH",
                        f"Candidate SeaJobs rows show experience with '{requested_engine_type}'.",
                        actual_value=row_engine_types,
                        expected_value=expected_engine_types,
                        confidence=confidence,
                    )
                return self._base_rule_result(
                    "FAIL",
                    "ENGINE_EXPERIENCE_MISMATCH",
                    (
                        f"Candidate engine experience {row_engine_types} does not match requested "
                        f"filter '{requested_engine_type}'."
                    ),
                    actual_value=row_engine_types,
                    expected_value=expected_engine_types,
                    confidence=confidence,
                )
            service_rows_status = str(((candidate_facts.get("fact_meta") or {}).get("experience.service_rows") or {}).get("status") or "")
            if service_rows and service_rows_status == "PARSED":
                return self._base_rule_result(
                    "FAIL",
                    "ENGINE_EXPERIENCE_MISMATCH",
                    (
                        f"Candidate has parsed service rows but no engine evidence matching "
                        f"'{requested_engine_type}'."
                    ),
                    actual_value=[],
                    expected_value=expected_engine_types,
                    confidence=confidence,
                )
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_EXPERIENCE_MISSING",
                f"Could not determine engine experience for requested filter '{requested_engine_type}'.",
                actual_value=[],
                expected_value=expected_engine_types,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if set(experienced_engine_types) & set(expected_engine_types):
            return self._base_rule_result(
                "PASS",
                "ENGINE_EXPERIENCE_MATCH",
                f"Candidate resume shows experience with '{requested_engine_type}'.",
                actual_value=experienced_engine_types,
                expected_value=expected_engine_types,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "ENGINE_EXPERIENCE_MISMATCH",
            (
                f"Candidate engine experience {experienced_engine_types} does not match requested "
                f"filter '{requested_engine_type}'."
            ),
            actual_value=experienced_engine_types,
            expected_value=expected_engine_types,
            confidence=confidence,
        )

    def _evaluate_engine_vessel_experience_rule(self, candidate_facts, constraint):
        experience = candidate_facts.get("experience") or {}
        fact_meta = (candidate_facts.get("fact_meta") or {}).get("experience.service_rows") or {}
        service_rows = experience.get("service_rows") or []
        status = str(fact_meta.get("status") or "MISSING")
        confidence = fact_meta.get("confidence")

        if status == "SOURCE_EXCLUDED":
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_VESSEL_EXPERIENCE_SOURCE_EXCLUDED",
                "Engine/vessel row matching needs contract service rows that can be parsed safely from the resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        requested_engine_type = self._normalize_engine_type((constraint or {}).get("engine_type")).replace(" ", "_")
        requested_vessel_type = self._normalize_ship_type((constraint or {}).get("vessel_type"))
        expected_engine_types = {
            self._normalize_engine_type(value).replace(" ", "_")
            for value in ((constraint or {}).get("expected_engine_values") or self._engine_type_expected_values(requested_engine_type))
            if value
        }
        expected_vessel_types = {
            self._normalize_ship_type(value)
            for value in ((constraint or {}).get("expected_vessel_values") or self._ship_type_expected_values(requested_vessel_type))
            if value
        }
        min_months = int((constraint or {}).get("min_months") or 0)
        lookback_contracts = int((constraint or {}).get("lookback_contracts") or 0)
        recent_contract_match_mode = str((constraint or {}).get("recent_contract_match_mode") or "any").strip().lower()

        if not requested_engine_type or not requested_vessel_type or not expected_engine_types or not expected_vessel_types:
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_VESSEL_EXPERIENCE_CONSTRAINT_INVALID",
                "Combined engine/vessel experience constraint is incomplete.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        valid_rows = []
        for row in service_rows:
            sign_in_date = row.get("sign_in_date")
            sign_out_date = row.get("sign_out_date")
            if not sign_in_date or not sign_out_date or sign_out_date < sign_in_date:
                continue
            valid_rows.append({
                "sign_in_date": sign_in_date,
                "sign_out_date": sign_out_date,
                "engine_types": [
                    self._normalize_engine_type(value).replace(" ", "_")
                    for value in (row.get("engine_types") or [])
                    if value
                ],
                "vessel_types": [
                    self._normalize_ship_type(value)
                    for value in (row.get("vessel_types") or [])
                    if value
                ],
            })

        if not valid_rows:
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_VESSEL_EXPERIENCE_ROWS_MISSING",
                "Could not determine contract-level engine/vessel experience from the selected resume.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        valid_rows.sort(key=lambda row: (row.get("sign_out_date"), row.get("sign_in_date")), reverse=True)
        evaluated_rows = valid_rows[:lookback_contracts] if lookback_contracts > 0 else valid_rows
        matched_months = 0
        matched_contracts = 0
        parsed_rows = 0
        for row in evaluated_rows:
            row_engine_types = set(row.get("engine_types") or [])
            row_vessel_types = set(row.get("vessel_types") or [])
            if row_engine_types or row_vessel_types:
                parsed_rows += 1
            if not (row_engine_types & expected_engine_types and row_vessel_types & expected_vessel_types):
                continue
            months = self._compute_service_duration_months(row.get("sign_in_date"), row.get("sign_out_date"))
            if months is None:
                continue
            matched_months += months
            matched_contracts += 1

        if parsed_rows == 0:
            return self._base_rule_result(
                "UNKNOWN",
                "ENGINE_VESSEL_EXPERIENCE_UNPARSED",
                "Could not determine engine or vessel types for the candidate's contracts.",
                actual_value=None,
                expected_value=constraint,
                confidence=confidence,
                unknown_reason="FACTUAL_UNKNOWN",
            )

        if lookback_contracts > 0 and min_months == 0 and recent_contract_match_mode == "all":
            if len(evaluated_rows) >= lookback_contracts and matched_contracts == lookback_contracts:
                return self._base_rule_result(
                    "PASS",
                    "ENGINE_VESSEL_EXPERIENCE_MATCH",
                    (
                        f"Candidate has '{requested_engine_type}' on '{requested_vessel_type}' "
                        f"in all {lookback_contracts} recent contract(s)."
                    ),
                    actual_value={
                        "matched_months": matched_months,
                        "matched_contracts": matched_contracts,
                        "evaluated_contracts": len(evaluated_rows),
                        "required_contracts": lookback_contracts,
                        "recent_contract_match_mode": recent_contract_match_mode,
                    },
                    expected_value=constraint,
                    confidence=confidence,
                )
            return self._base_rule_result(
                "FAIL",
                "ENGINE_VESSEL_EXPERIENCE_INSUFFICIENT",
                (
                    f"Candidate has '{requested_engine_type}' on '{requested_vessel_type}' in "
                    f"{matched_contracts} of the recent {lookback_contracts} contract(s); "
                    f"all {lookback_contracts} were required."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(evaluated_rows),
                    "required_contracts": lookback_contracts,
                    "recent_contract_match_mode": recent_contract_match_mode,
                },
                expected_value=constraint,
                confidence=confidence,
            )

        if min_months == 0 and matched_contracts > 0:
            return self._base_rule_result(
                "PASS",
                "ENGINE_VESSEL_EXPERIENCE_MATCH",
                (
                    f"Candidate has '{requested_engine_type}' on '{requested_vessel_type}' "
                    f"in {matched_contracts} contract(s)."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(evaluated_rows),
                },
                expected_value=constraint,
                confidence=confidence,
            )

        if min_months > 0 and matched_months >= min_months:
            return self._base_rule_result(
                "PASS",
                "ENGINE_VESSEL_EXPERIENCE_MATCH",
                (
                    f"Candidate has {matched_months} month(s) with '{requested_engine_type}' "
                    f"on '{requested_vessel_type}'."
                ),
                actual_value={
                    "matched_months": matched_months,
                    "matched_contracts": matched_contracts,
                    "evaluated_contracts": len(evaluated_rows),
                },
                expected_value=constraint,
                confidence=confidence,
            )

        return self._base_rule_result(
            "FAIL",
            "ENGINE_VESSEL_EXPERIENCE_INSUFFICIENT",
            (
                f"Candidate has only {matched_months} month(s) with '{requested_engine_type}' "
                f"on '{requested_vessel_type}', below the required {min_months}."
            ),
            actual_value={
                "matched_months": matched_months,
                "matched_contracts": matched_contracts,
                "evaluated_contracts": len(evaluated_rows),
            },
            expected_value=constraint,
            confidence=confidence,
        )

    def _evaluate_hard_filters(self, candidate_facts, job_constraints):
        hard_constraints = (job_constraints or {}).get("hard_constraints") or {}
        applied_constraints = set((job_constraints or {}).get("applied_constraints") or [])
        results = []
        facts_version = str(candidate_facts.get("facts_version") or self.FACTS_VERSION)
        candidate_id = str((candidate_facts or {}).get("candidate_id") or "").strip()
        activated_rules = []
        hard_filter_debug = os.getenv("NJORDHR_DEBUG_HARD_FILTERS", "").strip().lower() in {"1", "true", "yes", "on"}

        if hard_filter_debug:
            print(
                "[FILTERS] "
                f"candidate_id={candidate_id or '-'} "
                f"facts_version={facts_version} "
                f"applied_constraints={sorted(applied_constraints)} "
                f"hard_constraint_keys={sorted(hard_constraints.keys())}"
            )

        age_constraint = hard_constraints.get("age_years")
        if "age_range" in applied_constraints and age_constraint:
            activated_rules.append("age_range")
            results.append(self._evaluate_age_rule(candidate_facts, age_constraint))

        visa_constraint = hard_constraints.get("us_visa")
        if "us_visa" in applied_constraints and visa_constraint:
            activated_rules.append("us_visa")
            results.append(self._evaluate_us_visa_rule(candidate_facts, visa_constraint))

        passport_constraint = hard_constraints.get("passport_validity")
        if "passport_validity" in applied_constraints and passport_constraint:
            activated_rules.append("passport_validity")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "PASSPORT_RULE_REQUIRES_V2_FACTS",
                    "Passport validity requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=passport_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_passport_validity_rule(candidate_facts, passport_constraint))

        availability_constraint = hard_constraints.get("availability")
        if "availability" in applied_constraints and availability_constraint:
            activated_rules.append("availability")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "AVAILABILITY_RULE_REQUIRES_V2_FACTS",
                    "Availability evaluation requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=availability_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_availability_rule(candidate_facts, availability_constraint))

        rank_constraint = hard_constraints.get("rank")
        if "rank_match" in applied_constraints and rank_constraint:
            activated_rules.append("rank_match")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "RANK_RULE_REQUIRES_V2_FACTS",
                    "Rank match requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=rank_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_rank_rule(candidate_facts, rank_constraint))

        coc_constraint = (hard_constraints.get("certifications") or {}) if isinstance(hard_constraints.get("certifications"), dict) else {}
        if "coc_document_gate" in applied_constraints:
            activated_rules.append("coc_document_gate")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "COC_RULE_REQUIRES_V2_FACTS",
                    "COC validation requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=coc_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_coc_document_gate(candidate_facts, coc_constraint))

        coc_grade_constraint = hard_constraints.get("coc_grade")
        if "coc_grade_match" in applied_constraints and coc_grade_constraint:
            activated_rules.append("coc_grade_match")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "COC_GRADE_RULE_REQUIRES_V2_FACTS",
                    "COC grade matching requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=coc_grade_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_coc_grade_rule(candidate_facts, coc_grade_constraint))

        stcw_constraint = hard_constraints.get("stcw_basic")
        if "stcw_basic" in applied_constraints and stcw_constraint:
            activated_rules.append("stcw_basic")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "STCW_BASIC_RULE_REQUIRES_V2_FACTS",
                    "STCW basic validation requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=stcw_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_stcw_basic_rule(candidate_facts, stcw_constraint))

        cert_constraint = (hard_constraints.get("certifications") or {}) if isinstance(hard_constraints.get("certifications"), dict) else {}
        if "stcw_endorsement" in applied_constraints:
            activated_rules.append("stcw_endorsement")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "ENDORSEMENT_RULE_REQUIRES_V2_FACTS",
                    "Endorsement validation requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=cert_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_endorsement_rule(candidate_facts, cert_constraint))

        company_continuity_constraint = hard_constraints.get("company_continuity")
        if "company_continuity" in applied_constraints and company_continuity_constraint:
            activated_rules.append("company_continuity")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "COMPANY_CONTINUITY_RULE_REQUIRES_V2_FACTS",
                    "Same-company contract continuity requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=company_continuity_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_company_continuity_rule(candidate_facts, company_continuity_constraint))

        recency_constraint = hard_constraints.get("recency")
        if "recency" in applied_constraints and recency_constraint:
            activated_rules.append("recency")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "RECENCY_RULE_REQUIRES_V2_FACTS",
                    "Last sign-off recency requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=recency_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_recency_rule(candidate_facts, recency_constraint))

        engine_vessel_experience_constraint = hard_constraints.get("engine_vessel_experience")
        if "engine_vessel_experience" in applied_constraints and engine_vessel_experience_constraint:
            activated_rules.append("engine_vessel_experience")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "ENGINE_VESSEL_EXPERIENCE_RULE_REQUIRES_V2_FACTS",
                    "Combined engine/vessel experience requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=engine_vessel_experience_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_engine_vessel_experience_rule(candidate_facts, engine_vessel_experience_constraint))

        recent_contract_vessel_experience_constraint = hard_constraints.get("recent_contract_vessel_experience")
        if "recent_contract_vessel_experience" in applied_constraints and recent_contract_vessel_experience_constraint:
            activated_rules.append("recent_contract_vessel_experience")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "RECENT_CONTRACT_VESSEL_RULE_REQUIRES_V2_FACTS",
                    "Recent contract vessel experience requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=recent_contract_vessel_experience_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_recent_contract_vessel_experience_rule(candidate_facts, recent_contract_vessel_experience_constraint))

        engine_experience_constraint = hard_constraints.get("engine_experience")
        if "engine_experience" in applied_constraints and engine_experience_constraint:
            activated_rules.append("engine_experience")
            if facts_version == "1.1":
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "ENGINE_EXPERIENCE_RULE_REQUIRES_V2_FACTS",
                    "Engine experience requires v2.0 facts; candidate is still on v1.1 facts.",
                    actual_value=None,
                    expected_value=engine_experience_constraint,
                    confidence=None,
                    unknown_reason="VERSION_MISMATCH_UNKNOWN",
                ))
            else:
                results.append(self._evaluate_engine_experience_rule(candidate_facts, engine_experience_constraint))

        ship_type_constraint = hard_constraints.get("applied_ship_type")
        if "applied_ship_type" in applied_constraints and ship_type_constraint:
            activated_rules.append("applied_ship_type")
            requested_ship_type = self._normalize_ship_type(ship_type_constraint)
            candidate_ship_types = [
                self._normalize_ship_type(value)
                for value in ((candidate_facts.get("application") or {}).get("applied_ship_types") or [])
            ]
            candidate_ship_types = [value for value in candidate_ship_types if value]
            if not candidate_ship_types:
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "APPLIED_SHIP_TYPE_MISSING",
                    f"Could not determine applied ship type for requested filter '{ship_type_constraint}'.",
                    actual_value=[],
                    expected_value=[requested_ship_type] if requested_ship_type else [],
                    confidence=((candidate_facts.get("fact_meta") or {}).get("application.applied_ship_types") or {}).get("confidence"),
                    unknown_reason="FACTUAL_UNKNOWN",
                ))
            elif self._evaluate_rule("contains_any", candidate_ship_types, [requested_ship_type]):
                results.append(self._base_rule_result(
                    "PASS",
                    "APPLIED_SHIP_TYPE_MATCH",
                    f"Candidate applied ship type matches '{ship_type_constraint}'.",
                    actual_value=candidate_ship_types,
                    expected_value=[requested_ship_type] if requested_ship_type else [],
                    confidence=((candidate_facts.get("fact_meta") or {}).get("application.applied_ship_types") or {}).get("confidence"),
                ))
            else:
                results.append(self._base_rule_result(
                    "FAIL",
                    "APPLIED_SHIP_TYPE_MISMATCH",
                    (
                        f"Candidate applied ship type {candidate_ship_types} does not match requested "
                        f"filter '{ship_type_constraint}'."
                    ),
                    actual_value=candidate_ship_types,
                    expected_value=[requested_ship_type] if requested_ship_type else [],
                    confidence=((candidate_facts.get("fact_meta") or {}).get("application.applied_ship_types") or {}).get("confidence"),
                ))

        experience_ship_type_constraint = hard_constraints.get("experience_ship_type")
        if "experience_ship_type" in applied_constraints and experience_ship_type_constraint:
            activated_rules.append("experience_ship_type")
            requested_ship_type = self._normalize_ship_type(experience_ship_type_constraint)
            expected_ship_types = self._ship_type_expected_values(requested_ship_type)
            experienced_ship_types = [
                self._normalize_ship_type(value)
                for value in ((candidate_facts.get("experience") or {}).get("vessel_types") or [])
            ]
            experienced_ship_types = [value for value in experienced_ship_types if value]
            if not experienced_ship_types:
                results.append(self._base_rule_result(
                    "UNKNOWN",
                    "EXPERIENCE_SHIP_TYPE_MISSING",
                    f"Could not determine experienced ship type for requested filter '{experience_ship_type_constraint}'.",
                    actual_value=[],
                    expected_value=expected_ship_types,
                    confidence=((candidate_facts.get("fact_meta") or {}).get("experience.vessel_types") or {}).get("confidence"),
                    unknown_reason="FACTUAL_UNKNOWN",
                ))
            elif self._evaluate_rule("contains_any", experienced_ship_types, expected_ship_types):
                results.append(self._base_rule_result(
                    "PASS",
                    "EXPERIENCE_SHIP_TYPE_MATCH",
                    f"Candidate resume shows experience on '{experience_ship_type_constraint}'.",
                    actual_value=experienced_ship_types,
                    expected_value=expected_ship_types,
                    confidence=((candidate_facts.get("fact_meta") or {}).get("experience.vessel_types") or {}).get("confidence"),
                ))
            else:
                results.append(self._base_rule_result(
                    "FAIL",
                    "EXPERIENCE_SHIP_TYPE_MISMATCH",
                    (
                        f"Candidate experienced ship types {experienced_ship_types} do not match requested "
                        f"filter '{experience_ship_type_constraint}'."
                    ),
                    actual_value=experienced_ship_types,
                    expected_value=expected_ship_types,
                    confidence=((candidate_facts.get("fact_meta") or {}).get("experience.vessel_types") or {}).get("confidence"),
                ))

        if hard_filter_debug:
            print(
                "[FILTERS] "
                f"candidate_id={candidate_id or '-'} "
                f"activated_rules={activated_rules}"
            )

        # Final hard-filter precedence is load-bearing:
        # FAIL overrides UNKNOWN, and UNKNOWN overrides PASS. This ensures
        # conclusive disqualifiers win, while unresolved evidence still blocks
        # deterministic promotion into a PASS result.
        if any(result["decision"] == "FAIL" for result in results):
            final_decision = "FAIL"
        elif any(result["decision"] == "UNKNOWN" for result in results):
            final_decision = "UNKNOWN"
        else:
            final_decision = "PASS"

        return {
            "decision": final_decision,
            "results": results,
            "evaluation_date_used": date.today().isoformat(),
            "facts_version": facts_version,
        }

    def _ingest_folder(self, folder_path, rank):
        print(f"[{rank}] Starting ingestion scan...")
        pdf_paths = self._iter_pdf_files(folder_path)
        files_to_process = [p for p in pdf_paths if self._ingest_needs_processing(str(p), p.stat().st_mtime)]
        
        if not files_to_process:
            # If local registry is up-to-date but vector namespace is empty
            # (e.g., switched to a new dimension-specific index), force reindex.
            vector_count = self.vector_db.namespace_vector_count(rank)
            if pdf_paths and vector_count == 0:
                print(f"[{rank}] Registry is up to date but vector index is empty. Reindexing all {len(pdf_paths)} files.")
                files_to_process = pdf_paths
            else:
                print(f"[{rank}] Index is up to date.")
                yield {
                    "type": "indexing_complete",
                    "current": 0,
                    "total": 0,
                    "message": "Index is up to date."
                }
                return

        print(f"[{rank}] Found {len(files_to_process)} new/updated files to index.")
        total_files = len(files_to_process)
        processed_files = 0
        yield {
            "type": "indexing_start",
            "current": 0,
            "total": total_files,
            "message": f"Indexing {total_files} file(s)..."
        }
        embedding_failures = 0
        for path in files_to_process:
            print(f"  -> Processing: {path.name}")
            text = self.pdf_processor.extract_text(str(path))
            if not text or len(text.strip()) < 100:
                print("    - SKIPPED: Not enough text extracted.")
                processed_files += 1
                yield {
                    "type": "indexing_progress",
                    "current": processed_files,
                    "total": total_files,
                    "message": f"Skipping {path.name} (insufficient text)"
                }
                continue
            
            resume_id = self.registry.generate_resume_id(str(path))
            chunks = self.prepper.chunk_text(
                text,
                resume_id,
                rank,
                filename=path.name,
                source_path=str(path),
            )
            embeddings = self.prepper.get_embeddings([c['text'] for c in chunks])

            if embeddings:
                embedding_failures = 0
                self.vector_db.upsert_chunks(chunks, embeddings, rank)
                print(f"    - Indexed {len(chunks)} chunks.")
                self._ingest_mark_processed(str(path), path.stat().st_mtime, resume_id)
                processed_files += 1
                yield {
                    "type": "indexing_progress",
                    "current": processed_files,
                    "total": total_files,
                    "message": f"Indexed {path.name}"
                }
            else:
                embedding_failures += 1
                print("    - SKIPPED: Embedding generation failed.")
                processed_files += 1
                yield {
                    "type": "indexing_progress",
                    "current": processed_files,
                    "total": total_files,
                    "message": f"Skipped {path.name} (embedding failed)"
                }
                if embedding_failures >= 3:
                    error_hint = self.prepper.last_error or "No details available."
                    raise RuntimeError(
                        f"Embedding repeatedly failed while indexing. Last error: {error_hint}"
                    )
        yield {
            "type": "indexing_complete",
            "current": processed_files,
            "total": total_files,
            "message": "Indexing complete."
        }

    def _parse_compound_query(self, user_prompt):
        """
        Detect and parse compound queries into sub-queries and logic operators.
        Returns: (is_compound, operator, sub_queries)
        """
        prompt_text = str(user_prompt or "")
        prompt_lower = prompt_text.lower()
        protected_prompt = prompt_text
        protected_lower = prompt_lower

        age_range_patterns = [
            r'between\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'between\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'with\s*in\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+range\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'ages?\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'aged?\s+\d{1,2}\s*(?:-|to|and)\s*\d{1,2}',
        ]
        for pattern in age_range_patterns:
            protected_prompt = re.sub(pattern, lambda m: m.group(0).replace(" and ", " __RANGE_AND__ "), protected_prompt, flags=re.IGNORECASE)
            protected_lower = protected_prompt.lower()
        
        # Check for AND logic
        if ' and ' in protected_lower:
            sub_queries = [q.strip().replace("__RANGE_AND__", "and") for q in protected_prompt.split(' and ')]
            return (True, 'AND', sub_queries)
        
        # Check for OR logic  
        elif ' or ' in protected_lower:
            sub_queries = [q.strip().replace("__RANGE_AND__", "and") for q in protected_prompt.split(' or ')]
            return (True, 'OR', sub_queries)
        
        # Simple query
        return (False, None, [user_prompt])

    def _retrieve_for_subquery(self, sub_query, rank, top_k=60):
        """
        Retrieve chunks for a single sub-query.
        Returns: dict of {resume_id: [chunks]}
        """
        print(f"[SUBQUERY] Searching for: '{sub_query}'")
        
        # Generate embedding for this sub-query
        query_embedding = self.prepper.get_embeddings([sub_query])
        if not query_embedding:
            print("[SUBQUERY] Embedding unavailable, falling back to keyword retrieval.")
            return self._retrieve_candidates_keyword_fallback(rank, sub_query, top_k=top_k)
        
        # Search vector database
        search_results = self.vector_db.query(query_embedding[0], rank, top_k=top_k)
        
        # Group by resume_id
        results_by_resume = {}
        for match in search_results:
            if match['score'] >= self.config.min_similarity_score:
                resume_id = match['metadata']['resume_id']
                if resume_id not in results_by_resume:
                    results_by_resume[resume_id] = []
                results_by_resume[resume_id].append(match)
        
        print(f"[SUBQUERY] Found {len(results_by_resume)} candidate resumes")
        return results_by_resume

    def _extract_query_terms(self, user_prompt):
        """Extract useful keyword terms for lexical fallback retrieval."""
        stop_words = {
            "a", "an", "and", "or", "the", "is", "are", "to", "of", "for", "with",
            "in", "on", "by", "from", "having", "has", "have", "valid"
        }
        terms = re.findall(r"[a-zA-Z0-9/+.-]{2,}", user_prompt.lower())
        return [t for t in terms if t not in stop_words]

    def _retrieve_candidates_keyword_fallback(self, rank, user_prompt, top_k=50):
        """
        Fallback retrieval when embeddings are unavailable.
        Builds pseudo-chunks from local PDFs based on keyword overlap.
        """
        target_folder = rank_folder_path(self.config.download_root, rank)
        if not target_folder.exists():
            return {}

        query_terms = self._extract_query_terms(user_prompt)
        if not query_terms:
            query_terms = [user_prompt.lower().strip()]

        ranked = []
        for pdf_path in self._iter_pdf_files(target_folder):
            try:
                text = self.pdf_processor.extract_text(str(pdf_path))
            except Exception:
                continue
            if not text:
                continue

            text_l = text.lower()
            hits = sum(1 for t in query_terms if t and t in text_l)
            if hits == 0:
                continue

            score = hits / max(1, len(query_terms))
            ranked.append((score, pdf_path, text))

        ranked.sort(key=lambda x: x[0], reverse=True)
        top_ranked = ranked[:top_k]

        candidates = {}
        for idx, (score, pdf_path, text) in enumerate(top_ranked):
            resume_id = self.registry.get_resume_id(str(pdf_path))
            chunk_text = text[:self.LLM_CONTEXT_TEXT_CHAR_LIMIT]
            pseudo_chunk = {
                "id": f"fallback-{resume_id}-{idx}",
                "score": score,
                "metadata": {
                    "resume_id": resume_id,
                    "rank": rank,
                    "filename": pdf_path.name,
                    "source_path": str(pdf_path),
                    "raw_text": chunk_text
                }
            }
            candidates.setdefault(resume_id, []).append(pseudo_chunk)

        print(f"[FALLBACK] Keyword retrieval found {len(candidates)} candidate resumes")
        return candidates

    def _merge_compound_results(self, operator, subquery_results):
        """
        Merge results from multiple sub-queries based on operator (AND/OR).
        
        AND: Only resumes appearing in ALL sub-queries
        OR: Resumes appearing in ANY sub-query
        
        Returns: dict of {resume_id: [all_chunks_for_that_resume]}
        """
        if not subquery_results:
            return {}
        
        # Get resume IDs from each sub-query
        resume_sets = [set(results.keys()) for results in subquery_results]
        
        if operator == 'AND':
            # Intersection: Only resumes in ALL sub-queries
            candidate_resumes = set.intersection(*resume_sets) if resume_sets else set()
            print(f"[MERGE AND] {len(candidate_resumes)} resumes match ALL conditions")
        else:  # OR
            # Union: Resumes in ANY sub-query
            candidate_resumes = set.union(*resume_sets) if resume_sets else set()
            print(f"[MERGE OR] {len(candidate_resumes)} resumes match ANY condition")
        
        # Collect all unique chunks for each candidate resume
        merged_candidates = {}
        for resume_id in candidate_resumes:
            merged_candidates[resume_id] = []
            seen_chunk_ids = set()
            
            # Gather chunks from all sub-queries for this resume
            for results in subquery_results:
                if resume_id in results:
                    for chunk in results[resume_id]:
                        chunk_id = chunk['id']
                        if chunk_id not in seen_chunk_ids:
                            merged_candidates[resume_id].append(chunk)
                            seen_chunk_ids.add(chunk_id)
        
        return merged_candidates

    def _llm_compound_note_from_prompt(self, prompt):
        original_prompt = str(prompt or "").splitlines()[0].strip()
        if not original_prompt:
            return ""
        is_compound, operator, _sub_queries = self._parse_compound_query(original_prompt)
        if is_compound and operator == 'AND':
            return '[Note: This is a compound query - ALL conditions must be satisfied]'
        return ""

    def _build_llm_reasoning_prompt(self, prompt, retrieved_chunks, past_feedback, applied_constraints=None):
        context = "\n---\n".join([chunk['metadata']['raw_text'] for chunk in retrieved_chunks])

        feedback_context = ""
        if past_feedback:
            feedback_context = "\n\nPast User Corrections (learn from these):\n"
            for fb in past_feedback:
                filename, llm_dec, user_dec, llm_reason, user_notes = fb
                feedback_context += f"- File: {filename}\n"
                feedback_context += f"  LLM said: {llm_dec} ({llm_reason})\n"
                feedback_context += f"  User corrected: {user_dec}"
                if user_notes:
                    feedback_context += f" - Note: {user_notes}"
                feedback_context += "\n"

        compound_note = self._llm_compound_note_from_prompt(prompt)
        applied_constraint_set = set(applied_constraints or [])

        sections = []
        if "us_visa" in applied_constraint_set:
            sections.append(
                """1. **VISA VALIDATION - VERY STRICT:**
   - Supported visa evidence includes: "US Visa", "C1/D (USA)", "C1 (USA)", "D (USA)", "B1/B2 (USA)", "Australia Entry visa", "MCV (Australia)", and "Schengen"
   - If a visa field shows "Other", blank, "N/A", or "None" -> This means NO VALID VISA (mark FALSE)
   - If a supported visa type is present -> Check expiry date
   - If expiry date is before the current date -> EXPIRED (mark FALSE)
   - If expiry date shows year "0001" or "-0001" -> INVALID (mark FALSE)
   - ONLY mark TRUE if there's a specific supported visa type with valid future expiry
   - NEVER substitute one country's visa for another country's visa
   - A certificate, CoC, license, flag endorsement, or document issued by a country is NOT a visa or right-to-work unless the resume explicitly says so"""
            )

        sections.append(
            """2. COMPOUND REQUIREMENTS:
   - "A and B" requires BOTH A AND B
   - "A or B" requires EITHER A OR B (or both)
   - If ANY required condition (in AND queries) is missing -> mark FALSE"""
        )

        sections.append(
            '''3. TERMINOLOGY FLEXIBILITY:
   - "Oil tanker" matches: "crude oil tanker", "product tanker", "VLCC", "oil/chem tanker"
   - "Bulk carrier" matches: "dry cargo vessel", "handy size", "capesize", "panamax"'''
        )
        if "us_visa" in applied_constraint_set:
            sections[-1] = (
                '''3. TERMINOLOGY FLEXIBILITY:
   - "US visa" matches: "C1/D visa", "C1 visa", "D visa", "B1/B2 visa", "American visa", "US work authorization"
   - "Australia visa" matches: "Australia Entry visa", "MCV (Australia)"
   - "Schengen visa" matches: "Schengen"
   - "UK visa" does NOT match US visa, Schengen visa, or UK-issued certificates
   - "Oil tanker" matches: "crude oil tanker", "product tanker", "VLCC", "oil/chem tanker"
   - "Bulk carrier" matches: "dry cargo vessel", "handy size", "capesize", "panamax"'''
            )

        sections.append(
            """4. CONFIDENCE SCORING:
   - 0.9-1.0: Perfect match, all requirements clearly met with strong evidence
   - 0.7-0.89: Good match, requirements met but some minor ambiguity
   - 0.5-0.69: Uncertain, some requirements unclear or borderline (FLAG FOR REVIEW)
   - 0.0-0.49: Poor match, requirements not met or very unclear

   **Flag as "uncertain" if confidence < 0.7 - user will review these**"""
        )

        reason_clarity = """5. REASON CLARITY:
   - Quantify experience (years, vessel types)
   - State what's missing if no-match
   - Use natural language recruiters understand"""
        if "us_visa" in applied_constraint_set:
            reason_clarity = """5. REASON CLARITY:
   - Be specific about visa status (type, expiry date)
   - Quantify experience (years, vessel types)
   - State what's missing if no-match
   - Use natural language recruiters understand"""
        sections.append(reason_clarity)

        examples = [
            """{"is_match": true, "reason": "Candidate has 12 years as Chief Engineer on oil tankers including VLCCs", "confidence": 0.95}""",
            """{"is_match": false, "reason": "Candidate has bulk carrier background but no clear oil tanker experience", "confidence": 0.3}""",
            """{"is_match": true, "reason": "Candidate has relevant vessel experience, but the exact tenure requirement is only partially evidenced", "confidence": 0.65}""",
        ]
        if "us_visa" in applied_constraint_set:
            examples = [
                """{"is_match": true, "reason": "Candidate has valid US C1/D visa expiring 2028 and 12 years as Chief Engineer on oil tankers including VLCCs", "confidence": 0.95}""",
                """{"is_match": false, "reason": "US visa field shows 'Other' (not a valid US visa type), though candidate has extensive oil tanker experience", "confidence": 0.3}""",
                """{"is_match": true, "reason": "Valid US B1/B2 visa until 2027, but only 3 years oil tanker experience (requirement was 5+)", "confidence": 0.65}""",
                """{"is_match": false, "reason": "US visa expired in 2023, candidate needs valid current visa", "confidence": 0.2}""",
            ]

        instruction_block = "\n\n".join(sections)
        example_block = "\n\n".join(examples)

        return f"""
Analyze the resume context below to determine if it matches the user's requirements.

User Requirements: "{prompt}"
{compound_note}

Resume Context:
{context}
{feedback_context}

Instructions:

{instruction_block}

Respond ONLY with valid JSON (no markdown):
{{"is_match": boolean, "reason": "Clear specific explanation", "confidence": 0.0-1.0}}

Examples of GOOD responses:

{example_block}
"""

    def _reason_with_llm(self, prompt, retrieved_chunks, past_feedback):
        """Enhanced LLM reasoning with confidence scoring and past feedback"""
        applied_constraints = getattr(self, "_llm_applied_constraints", [])
        reasoning_prompt = self._build_llm_reasoning_prompt(
            prompt,
            retrieved_chunks,
            past_feedback,
            applied_constraints=applied_constraints,
        )
        
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.reasoning_model}:generateContent"
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.config.gemini_api_key}
        payload = {"contents": [{"parts": [{"text": reasoning_prompt}]}]}
        
        try:
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=self.LLM_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result_text = response.json()['candidates'][0]['content']['parts'][0]['text']
            
            # Clean markdown
            result_text = result_text.strip()
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.startswith('```'):
                result_text = result_text[3:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                # Ensure confidence exists
                if 'confidence' not in parsed:
                    parsed['confidence'] = 0.5
                return parsed
        except Exception as e:
            print(f"[ERROR] LLM reasoning failed: {e}")
        
        return {"is_match": False, "reason": "Failed to get conclusive answer from AI.", "confidence": 0.0}

    def run_analysis_stream(self, rank, user_prompt, applied_ship_type=None, experienced_ship_type=None):
        """Streaming analysis with multi-stage retrieval for compound queries"""
        yield {"type": "status", "message": "Initializing analysis..."}
        
        try:
            target_folder = rank_folder_path(self.config.download_root, rank)
            
            if not target_folder.exists():
                yield {"type": "error", "message": f"Rank folder for '{rank}' not found."}
                return

            yield {"type": "status", "message": "Scanning for new files..."}
            for ingest_event in self._ingest_folder(str(target_folder), rank):
                yield ingest_event

            job_constraints = self._extract_job_constraints(user_prompt, rank=rank)
            if str(applied_ship_type or "").strip():
                job_constraints.setdefault("hard_constraints", {})["applied_ship_type"] = str(applied_ship_type).strip()
                if "applied_ship_type" not in job_constraints.setdefault("applied_constraints", []):
                    job_constraints["applied_constraints"].append("applied_ship_type")
            if str(experienced_ship_type or "").strip():
                job_constraints.setdefault("hard_constraints", {})["experience_ship_type"] = str(experienced_ship_type).strip()
                if "experience_ship_type" not in job_constraints.setdefault("applied_constraints", []):
                    job_constraints["applied_constraints"].append("experience_ship_type")
            has_semantic_intent = self._has_semantic_intent(user_prompt, job_constraints)
            has_actionable_constraints = bool(
                job_constraints.get("applied_constraints")
                or str(applied_ship_type or "").strip()
                or str(experienced_ship_type or "").strip()
            )
            structured_only_prompt = self._is_structured_only_prompt(
                user_prompt,
                job_constraints=job_constraints,
                has_semantic_intent=has_semantic_intent,
            )
            folder_metadata = self._rank_manifest_metadata(target_folder)

            if not has_actionable_constraints and not has_semantic_intent:
                graceful_message = (
                    "Search could not run because the prompt did not contain supported hard constraints or recognizable semantic intent."
                )
                if job_constraints.get("unapplied_constraints"):
                    friendly_labels = {
                        "min_sea_service": "minimum sea service",
                        "vessel_type": "vessel type",
                        "availability": "availability",
                        "stcw_endorsement": "STCW endorsement",
                        "company_continuity": "same-company contract continuity",
                    }
                    unapplied_labels = [
                        friendly_labels.get(constraint_id, constraint_id)
                        for constraint_id in job_constraints.get("unapplied_constraints", [])
                    ]
                    graceful_message = (
                        "Search could not run because the prompt only contained recognized but currently unsupported constraints: "
                        + ", ".join(unapplied_labels)
                        + "."
                    )
                yield {
                    "type": "graceful_failure",
                    "message": graceful_message,
                    "applied_constraints": job_constraints.get("applied_constraints", []),
                    "unapplied_constraints": job_constraints.get("unapplied_constraints", []),
                    "parsing_notes": job_constraints.get("parsing_notes", []),
                }
                return
            
            # Parse query to detect compound logic
            is_compound, operator, sub_queries = self._parse_compound_query(user_prompt)
            
            if has_actionable_constraints:
                status_message = "Supported hard constraints detected. Evaluating all resumes in selected rank folder..."
                if has_semantic_intent:
                    status_message = "Mixed query detected. Evaluating all resumes in selected rank folder before semantic reasoning..."
                elif structured_only_prompt:
                    status_message = "Structured-only prompt detected. Evaluating all resumes in selected rank folder..."
                yield {"type": "status", "message": status_message}
                candidates = self._enumerate_rank_candidates(target_folder, rank)
            elif is_compound:
                yield {"type": "status", "message": f"Detected compound query: {operator} logic with {len(sub_queries)} conditions"}
                print(f"[COMPOUND QUERY] Operator: {operator}")
                print(f"[COMPOUND QUERY] Sub-queries: {sub_queries}")
                
                # Stage 1: Search for each sub-query separately
                subquery_results = []
                for i, sub_query in enumerate(sub_queries, 1):
                    yield {"type": "status", "message": f"Searching for condition {i}/{len(sub_queries)}: '{sub_query}'"}
                    results = self._retrieve_for_subquery(sub_query, rank, top_k=60)
                    subquery_results.append(results)
                
                # Stage 2: Merge results based on operator (AND/OR)
                yield {"type": "status", "message": f"Combining results using {operator} logic..."}
                candidates = self._merge_compound_results(operator, subquery_results)
                
            else:
                # Simple query: Use original single-stage retrieval
                yield {"type": "status", "message": "Generating query embedding..."}
                query_embedding = self.prepper.get_embeddings([user_prompt])
                
                if not query_embedding:
                    error_hint = self.prepper.last_error or "No details available."
                    yield {"type": "status", "message": "Embedding unavailable. Switching to keyword fallback retrieval..."}
                    print(f"[FALLBACK] Embedding unavailable. Reason: {error_hint}")
                    candidates = self._retrieve_candidates_keyword_fallback(rank, user_prompt, top_k=50)
                else:
                    yield {"type": "status", "message": "Searching vector database..."}
                    search_results = self.vector_db.query(query_embedding[0], rank, top_k=50)
                    if self.vector_db.last_error:
                        yield {"type": "error", "message": self.vector_db.last_error}
                        return

                    candidates = {}
                    for match in search_results:
                        if match['score'] >= self.config.min_similarity_score:
                            resume_id = match['metadata']['resume_id']
                            if resume_id not in candidates:
                                candidates[resume_id] = []
                            candidates[resume_id].append(match)
            
            total_candidates = len(candidates)
            yield {"type": "progress", "current": 0, "total": total_candidates, 
                   "message": f"Found {total_candidates} candidates to analyze"}
            
            # Deterministic hard filter gate
            past_feedback = self.feedback.get_recent_feedback(user_prompt)
            text_cache = {}
            perf_state = {}
            candidate_paths = {}
            path_index_started_at = time.perf_counter()
            for resume_id, chunks in candidates.items():
                chunk_list = chunks or []
                source_path = str(((chunk_list[0].get("metadata") or {}).get("source_path") or "")).strip() if chunk_list else ""
                if source_path:
                    candidate_paths[resume_id] = Path(source_path)
            self._record_perf_timing(perf_state, "candidate_path_index", time.perf_counter() - path_index_started_at)
            
            verified_matches = []
            uncertain_matches = []
            unknown_matches = []
            hard_filter_audit = []
            partial_evaluation = {
                "occurred": False,
                "reasons": [],
                "candidates": [],
            }
            hard_filter_summary = {
                "scanned": total_candidates,
                "passed": 0,
                "failed": 0,
                "unknown": 0,
                "matched": 0,
            }
            search_state = {"sync_reextract_count": 0}
            search_started_at = time.perf_counter()
            
            for i, (resume_id, chunks) in enumerate(candidates.items(), 1):
                candidate_started_at = time.perf_counter()
                original_path = candidate_paths.get(resume_id)
                filename = original_path.name if original_path else resume_id[:8]
                
                yield {"type": "progress", "current": i, "total": total_candidates, 
                       "message": f"Analyzing: {filename}"}

                prompt_for_reasoning = user_prompt
                facts_started_at = time.perf_counter()
                candidate_facts = self._build_candidate_facts(
                    filename,
                    rank,
                    chunks,
                    original_path=original_path,
                    text_cache=text_cache,
                    folder_metadata=folder_metadata,
                )
                self._record_perf_timing(perf_state, "build_candidate_facts", time.perf_counter() - facts_started_at)
                candidate_facts, reextract_meta = self._maybe_sync_reextract_candidate(
                    candidate_facts,
                    job_constraints,
                    filename,
                    rank,
                    chunks,
                    original_path,
                    text_cache,
                    folder_metadata,
                    search_state,
                    perf_state=perf_state,
                )
                hard_filter_started_at = time.perf_counter()
                hard_filter_result = self._evaluate_hard_filters(candidate_facts, job_constraints)
                self._record_perf_timing(perf_state, "hard_filter_evaluation", time.perf_counter() - hard_filter_started_at)
                age_constraint = ((job_constraints.get("hard_constraints") or {}).get("age_years"))
                age_value = ((candidate_facts.get("derived") or {}).get("age_years"))
                dob_value = ((candidate_facts.get("personal") or {}).get("dob"))
                applied_ship_types = ((candidate_facts.get("application") or {}).get("applied_ship_types") or [])
                experienced_ship_types = ((candidate_facts.get("experience") or {}).get("vessel_types") or [])
                audit_entry = {
                    "candidate_id": resume_id,
                    "filename": filename,
                    "hard_filter_decision": hard_filter_result["decision"],
                    "hard_filter_reasons": hard_filter_result["results"],
                    "evaluation_date_used": hard_filter_result.get("evaluation_date_used"),
                    "facts_version": hard_filter_result.get("facts_version"),
                    "llm_reached": False,
                    "result_bucket": "excluded",
                    "sync_reextract": reextract_meta,
                }
                audit_entry.update(self._derive_evidence_review_metadata(hard_filter_result, candidate_facts))

                if reextract_meta and not reextract_meta.get("succeeded"):
                    partial_evaluation["occurred"] = True
                    fallback_reason = str(reextract_meta.get("fallback_reason") or "").strip()
                    if fallback_reason and fallback_reason not in partial_evaluation["reasons"]:
                        partial_evaluation["reasons"].append(fallback_reason)
                    partial_evaluation["candidates"].append({
                        "candidate_id": resume_id,
                        "filename": filename,
                        "fallback_reason": fallback_reason,
                    })

                if hard_filter_result["decision"] == "FAIL":
                    hard_filter_summary["failed"] += 1
                    hard_filter_audit.append(audit_entry)
                    self._record_perf_timing(perf_state, "candidate_total", time.perf_counter() - candidate_started_at)
                    print(f"[HARD FILTER] Rejecting {filename}: {hard_filter_result['results']}")
                    if i == total_candidates or i % 10 == 0:
                        print(
                            "[PERF] Candidate progress "
                            f"{i}/{total_candidates} | "
                            f"sync_reextract_count={search_state.get('sync_reextract_count', 0)} | "
                            f"timings={json.dumps(self._perf_snapshot(perf_state), sort_keys=True)}"
                        )
                    continue

                if hard_filter_result["decision"] == "UNKNOWN":
                    hard_filter_summary["unknown"] += 1
                    audit_entry["result_bucket"] = "needs_review"
                    hard_filter_audit.append(audit_entry)
                    unknown_reason_types = list(dict.fromkeys(
                        reason.get("unknown_reason")
                        for reason in hard_filter_result["results"]
                        if reason.get("unknown_reason")
                    ))
                    evidence_review = self._derive_evidence_review_metadata(hard_filter_result, candidate_facts)
                    unknown_match = {
                        "filename": filename,
                        "reason": "; ".join(result["message"] for result in hard_filter_result["results"]) or "Hard filter result unknown.",
                        "hard_filter_decision": "UNKNOWN",
                        "hard_filter_reasons": hard_filter_result["results"],
                        "unknown_reason_types": unknown_reason_types,
                        "computed_age": age_value,
                        "dob": dob_value.isoformat() if dob_value else None,
                        "applied_ship_types": applied_ship_types,
                        "experienced_ship_types": experienced_ship_types,
                        "evaluation_date_used": hard_filter_result.get("evaluation_date_used"),
                        "facts_version": hard_filter_result.get("facts_version"),
                        "partial_evaluation": bool(reextract_meta and not reextract_meta.get("succeeded")),
                        "partial_evaluation_reason": str((reextract_meta or {}).get("fallback_reason") or ""),
                        "default_insights": self._build_default_search_insights(candidate_facts),
                    }
                    unknown_match.update(evidence_review)
                    unknown_matches.append(unknown_match)
                    yield {
                        "type": "hard_filter_unknown",
                        "match": unknown_match,
                        "current": i,
                        "total": total_candidates,
                    }
                    self._record_perf_timing(perf_state, "candidate_total", time.perf_counter() - candidate_started_at)
                    if i == total_candidates or i % 10 == 0:
                        print(
                            "[PERF] Candidate progress "
                            f"{i}/{total_candidates} | "
                            f"sync_reextract_count={search_state.get('sync_reextract_count', 0)} | "
                            f"timings={json.dumps(self._perf_snapshot(perf_state), sort_keys=True)}"
                        )
                    continue

                hard_filter_summary["passed"] += 1
                if age_constraint and age_value is not None and dob_value is not None:
                    prompt_for_reasoning = (
                        f"{user_prompt}\n"
                        f"Computed candidate age from DOB: {age_value} years old "
                        f"(DOB: {dob_value.isoformat()}). Treat this age as authoritative. "
                        f"This candidate already passed the deterministic age gate."
                    )

                if structured_only_prompt and not has_semantic_intent:
                    evidence_review = self._derive_evidence_review_metadata(hard_filter_result, candidate_facts)
                    match_data = {
                        "filename": filename,
                        "reason": "; ".join(result["message"] for result in hard_filter_result["results"]) or "Passed deterministic hard filters.",
                        "confidence": 1.0,
                        "hard_filter_decision": hard_filter_result["decision"],
                        "hard_filter_reasons": hard_filter_result["results"],
                        "computed_age": age_value,
                        "dob": dob_value.isoformat() if dob_value else None,
                        "applied_ship_types": applied_ship_types,
                        "experienced_ship_types": experienced_ship_types,
                        "evaluation_date_used": hard_filter_result.get("evaluation_date_used"),
                        "facts_version": hard_filter_result.get("facts_version"),
                        "default_insights": self._build_default_search_insights(candidate_facts),
                    }
                    match_data.update(evidence_review)
                    verified_matches.append(match_data)
                    audit_entry["result_bucket"] = "verified_match"
                    yield {"type": "match_found", "match": match_data,
                           "current": i, "total": total_candidates}
                    hard_filter_summary["matched"] += 1
                else:
                    audit_entry["llm_reached"] = True
                    audit_entry["result_bucket"] = "llm_no_match"

                    # LLM reasoning with confidence
                    llm_started_at = time.perf_counter()
                    self._llm_applied_constraints = list(job_constraints.get("applied_constraints", []))
                    try:
                        llm_result = self._reason_with_llm(prompt_for_reasoning, chunks, past_feedback)
                    finally:
                        self._llm_applied_constraints = []
                    self._record_perf_timing(perf_state, "llm_reasoning", time.perf_counter() - llm_started_at)

                    if llm_result.get('is_match'):
                        evidence_review = self._derive_evidence_review_metadata(hard_filter_result, candidate_facts)
                        match_data = {
                            "filename": filename,
                            "reason": llm_result.get('reason', 'Match found.'),
                            "confidence": llm_result.get('confidence', 0.5),
                            "hard_filter_decision": hard_filter_result["decision"],
                            "hard_filter_reasons": hard_filter_result["results"],
                            "computed_age": age_value,
                            "dob": dob_value.isoformat() if dob_value else None,
                            "applied_ship_types": applied_ship_types,
                            "experienced_ship_types": experienced_ship_types,
                            "evaluation_date_used": hard_filter_result.get("evaluation_date_used"),
                            "facts_version": hard_filter_result.get("facts_version"),
                            "default_insights": self._build_default_search_insights(candidate_facts),
                        }
                        match_data.update(evidence_review)

                        # Flag uncertain matches (confidence < 0.7)
                        if match_data['confidence'] < 0.7:
                            uncertain_matches.append(match_data)
                            audit_entry["result_bucket"] = "uncertain_match"
                            yield {"type": "uncertain_found", "match": match_data,
                                   "current": i, "total": total_candidates}
                        else:
                            verified_matches.append(match_data)
                            audit_entry["result_bucket"] = "verified_match"
                            yield {"type": "match_found", "match": match_data,
                                   "current": i, "total": total_candidates}
                        hard_filter_summary["matched"] += 1
                hard_filter_audit.append(audit_entry)
                
                # Only pace provider-backed LLM paths; deterministic hard-filter-only
                # candidates should not pay the same fixed delay.
                if i < total_candidates and audit_entry["llm_reached"]:
                    sleep_started_at = time.perf_counter()
                    time.sleep(self.LLM_RATE_LIMIT_SLEEP_SECONDS)
                    self._record_perf_timing(perf_state, "rate_limit_sleep", time.perf_counter() - sleep_started_at)
                self._record_perf_timing(perf_state, "candidate_total", time.perf_counter() - candidate_started_at)
                if i == total_candidates or i % 10 == 0:
                    print(
                        "[PERF] Candidate progress "
                        f"{i}/{total_candidates} | "
                        f"sync_reextract_count={search_state.get('sync_reextract_count', 0)} | "
                        f"timings={json.dumps(self._perf_snapshot(perf_state), sort_keys=True)}"
                    )
            
            total_elapsed = time.perf_counter() - search_started_at
            print(
                "[PERF] Search summary "
                f"rank={rank} total_candidates={total_candidates} "
                f"elapsed_seconds={total_elapsed:.3f} "
                f"sync_reextract_count={search_state.get('sync_reextract_count', 0)} "
                f"timings={json.dumps(self._perf_snapshot(perf_state), sort_keys=True)}"
            )
            yield {"type": "complete", 
                   "verified_matches": verified_matches,
                   "uncertain_matches": uncertain_matches,
                   "unknown_matches": unknown_matches,
                   "applied_constraints": job_constraints.get("applied_constraints", []),
                   "unapplied_constraints": job_constraints.get("unapplied_constraints", []),
                   "parsing_notes": job_constraints.get("parsing_notes", []),
                   "hard_filter_audit": hard_filter_audit,
                   "hard_filter_summary": hard_filter_summary,
                   "partial_evaluation": partial_evaluation,
                   "partial_evaluation_notice": (
                       "Some v1.1 candidates fell back to partial evaluation because synchronous re-extraction hit cooldown, per-search limits, timeout, failure, or concurrency guardrails."
                       if partial_evaluation["occurred"]
                       else ""
                   ),
                   "message": (
                       f"Scanned {hard_filter_summary['scanned']}, "
                       f"passed hard filters {hard_filter_summary['passed']}, "
                       f"unknown {hard_filter_summary['unknown']}, "
                       f"matched {hard_filter_summary['matched']}."
                   )}
        
        except Exception as e:
            print(f"[ERROR] Analysis failed: {e}")
            import traceback
            traceback.print_exc()
            yield {"type": "error", "message": f"Analysis failed: {str(e)}"}

    def run_analysis(self, rank, user_prompt, applied_ship_type=None, experienced_ship_type=None):
        """Non-streaming version"""
        verified_matches = []
        uncertain_matches = []
        unknown_matches = []
        hard_filter_summary = {}
        message = ""
        
        for event in self.run_analysis_stream(
            rank,
            user_prompt,
            applied_ship_type=applied_ship_type,
            experienced_ship_type=experienced_ship_type,
        ):
            if event['type'] == 'match_found':
                verified_matches.append(event['match'])
            elif event['type'] == 'uncertain_found':
                uncertain_matches.append(event['match'])
            elif event['type'] == 'hard_filter_unknown':
                unknown_matches.append(event['match'])
            elif event['type'] == 'complete':
                message = event['message']
                hard_filter_summary = event.get("hard_filter_summary", {})
            elif event['type'] == 'graceful_failure':
                return {
                    "success": False,
                    "graceful_failure": True,
                    "verified_matches": [],
                    "uncertain_matches": [],
                    "unknown_matches": [],
                    "hard_filter_summary": {},
                    "message": event["message"],
                    "applied_constraints": event.get("applied_constraints", []),
                    "unapplied_constraints": event.get("unapplied_constraints", []),
                    "parsing_notes": event.get("parsing_notes", []),
                }
            elif event['type'] == 'error':
                return {"success": False, "verified_matches": [], "uncertain_matches": [], "unknown_matches": [],
                        "message": event['message']}
        
        return {
            "success": True, 
            "verified_matches": verified_matches,
            "uncertain_matches": uncertain_matches,
            "unknown_matches": unknown_matches,
            "hard_filter_summary": hard_filter_summary,
            "message": message
        }
    
    def store_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence, 
                       user_decision, user_notes=""):
        """Store user feedback for learning"""
        self.feedback.add_feedback(
            filename, query, llm_decision, llm_reason, llm_confidence,
            user_decision, user_notes
        )


# ==============================================================================
# 8. BACKWARD COMPATIBILITY WRAPPER
# ==============================================================================
class Analyzer:
    """Backward-compatible wrapper"""
    _instance = None

    def __init__(self, config_source):
        print("\n*** RUNNING with Multi-Stage Retrieval + Learning ***\n")
        if Analyzer._instance is None:
            if hasattr(config_source, "get") and hasattr(config_source, "has_section"):
                config = config_source
            else:
                import configparser
                config = configparser.ConfigParser()
                config_path = os.getenv("NJORDHR_CONFIG_PATH", "config.ini")
                config.read(config_path)
            Analyzer._instance = AIResumeAnalyzer(config)

    def run_analysis(self, target_folder, prompt, applied_ship_type=None, experienced_ship_type=None):
        rank_name = Path(target_folder).name.replace('_', ' ').replace('-', '/')
        return Analyzer._instance.run_analysis(
            rank_name,
            prompt,
            applied_ship_type=applied_ship_type,
            experienced_ship_type=experienced_ship_type,
        )
    
    def run_analysis_stream(self, target_folder, prompt, applied_ship_type=None, experienced_ship_type=None):
        rank_name = Path(target_folder).name.replace('_', ' ').replace('-', '/')
        for progress_event in Analyzer._instance.run_analysis_stream(
            rank_name,
            prompt,
            applied_ship_type=applied_ship_type,
            experienced_ship_type=experienced_ship_type,
        ):
            yield progress_event
    
    def store_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence,
                       user_decision, user_notes=""):
        """Store user feedback"""
        Analyzer._instance.store_feedback(
            filename, query, llm_decision, llm_reason, llm_confidence,
            user_decision, user_notes
        )
