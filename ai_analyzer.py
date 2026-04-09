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
from datetime import UTC, datetime, date

# --- Core Dependencies ---
import requests
import fitz  # PyMuPDF
from PIL import Image
from pinecone import Pinecone, ServerlessSpec

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
        os.getenv("SUPABASE_SECRET_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


def _should_use_cloud_ai_store():
    return (
        str(os.getenv("USE_SUPABASE_DB", "")).strip().lower() in {"1", "true", "yes", "on"}
        and bool(os.getenv("SUPABASE_URL", "").strip())
        and bool(_resolve_supabase_api_key())
    )


class SupabaseStoreBase:
    DEFAULT_TIMEOUT_SECONDS = 30

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
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

    def _chunk_metadata(self, resume_id, rank, chunk_text):
        return {
            "resume_id": resume_id,
            "rank": rank,
            "raw_text": chunk_text,
        }

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

    def chunk_text(self, text, resume_id, rank):
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
                        "metadata": self._chunk_metadata(resume_id, rank, chunk_text),
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
                    "metadata": self._chunk_metadata(resume_id, rank, chunk_text),
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
    V2_ONLY_CONSTRAINT_IDS = {"rank_match", "coc_document_gate", "stcw_basic"}
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
            r'within\s+the\s+ages?\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'within\s+the\s+age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
            r'with\s*in\s+the\s+age\s+of\s+(\d{1,2})\s+(?:and|to)\s+(\d{1,2})',
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
            r'over\s+(\d{1,2})',
            r'above\s+(\d{1,2})',
            r'minimum\s+age\s+(?:of\s+)?(\d{1,2})',
        ]
        for pattern in min_patterns:
            match = re.search(pattern, prompt)
            if match:
                value = int(match.group(1))
                if 'older than' in pattern or 'over' in pattern or 'above' in pattern:
                    value += 1
                return {"min_age": value, "max_age": None}

        max_patterns = [
            r'up\s+to\s+(\d{1,2})\s+years?\s+old',
            r'younger\s+than\s+(\d{1,2})',
            r'under\s+(\d{1,2})',
            r'below\s+(\d{1,2})',
            r'maximum\s+age\s+(?:of\s+)?(\d{1,2})',
        ]
        for pattern in max_patterns:
            match = re.search(pattern, prompt)
            if match:
                value = int(match.group(1))
                if 'younger than' in pattern or 'under' in pattern or 'below' in pattern:
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
            r"\bvalid\s+certificate\s+of\s+competency\b",
            r"\bcertificate\s+of\s+competency\s+required\b",
            r"\bmust\s+hold\s+valid\s+coc\b",
        ]
        if any(re.search(pattern, prompt) for pattern in patterns):
            return {
                "coc_required": True,
                "coc_valid_required": True,
            }
        return None

    def _extract_stcw_basic_constraint(self, user_prompt):
        prompt = str(user_prompt or "").lower()
        patterns = [
            r"\bvalid\s+stcw\s+basic\b",
            r"\bstcw\s+basic\s+required\b",
            r"\bmust\s+hold\s+all\s+basic\s+stcw\s+certificates\b",
            r"\bvalid\s+stcw\s+basic\s+required\b",
        ]
        if any(re.search(pattern, prompt) for pattern in patterns):
            return {"required": True}
        return None

    def _extract_min_sea_service_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        match = re.search(r"\b(\d+)\s*\+?\s*(years?|months?)\b", prompt, flags=re.IGNORECASE)
        if not match:
            return None
        value = int(match.group(1))
        unit = match.group(2).lower()
        months = value * 12 if unit.startswith("year") else value
        original_phrase = match.group(0).strip()
        if re.search(r"\bsea\s+service\b|\bexperience\b", prompt, flags=re.IGNORECASE):
            return {"min_total_months": months, "display_value": original_phrase, "operator": "gte"}
        return None

    def _extract_vessel_type_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        configured_matches = self._extract_configured_ship_types(prompt)
        if configured_matches:
            return {"required": configured_matches, "display_value": ", ".join(configured_matches), "operator": "contains_any"}

        # Configured labels returned nothing; fall back to hardcoded alias table.
        # This only happens when [ShipTypes] ship_type_options is absent from config.ini.
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
            date_fact = self._extract_date_fact_from_snippet(date_match.group(1))
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
                "relative_display_value": f"in {relative_match.group(1)} days",
                "display_value": relative_match.group(0).strip().lower(),
            }
        return None

    def _extract_endorsement_constraint(self, user_prompt):
        prompt = str(user_prompt or "")
        mappings = [
            (r"\bdpo\b|\bdp operator\b", "dp_operational", "DPO"),
            (r"\bgmdss\b", "gmdss", "GMDSS"),
            (r"\boil tanker endorsement\b", "tanker_oil", "oil tanker endorsement"),
            (r"\bchemical tanker endorsement\b", "tanker_chemical", "chemical tanker endorsement"),
            (r"\bgas tanker endorsement\b", "tanker_gas", "gas tanker endorsement"),
        ]
        for pattern, canonical_id, display_value in mappings:
            if re.search(pattern, prompt, flags=re.IGNORECASE):
                return {"endorsements_required": [canonical_id], "display_value": display_value}

        if re.search(r"\btanker endorsement\b", prompt, flags=re.IGNORECASE):
            return {"ambiguous": True, "fragment": "tanker endorsement"}
        if re.search(r"\bDP2\b|\bDP3\b|\bDP\b", prompt):
            return {"ambiguous": True, "fragment": re.search(r"\bDP2\b|\bDP3\b|\bDP\b", prompt).group(0)}
        return None

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

        rank_constraint = self._extract_rank_constraint(user_prompt)
        if rank_constraint:
            constraints["hard_constraints"]["rank"] = rank_constraint
            constraints["applied_constraints"].append("rank_match")

        visa_constraint = self._extract_us_visa_constraint(user_prompt)
        if visa_constraint:
            constraints["hard_constraints"]["us_visa"] = visa_constraint
            constraints["applied_constraints"].append("us_visa")

        coc_constraint = self._extract_coc_requirement_constraint(user_prompt)
        if coc_constraint:
            constraints["hard_constraints"]["certifications"] = coc_constraint
            constraints["applied_constraints"].append("coc_document_gate")

        stcw_constraint = self._extract_stcw_basic_constraint(user_prompt)
        if stcw_constraint:
            constraints["hard_constraints"]["stcw_basic"] = stcw_constraint
            constraints["applied_constraints"].append("stcw_basic")

        experienced_ship_type = self._extract_experience_ship_type_constraint(user_prompt)
        if experienced_ship_type:
            constraints["hard_constraints"]["experience_ship_type"] = experienced_ship_type
            constraints["applied_constraints"].append("experience_ship_type")

        sea_service_constraint = self._extract_min_sea_service_constraint(user_prompt)
        if sea_service_constraint:
            constraints["hard_constraints"]["sea_service"] = sea_service_constraint
            constraints["unapplied_constraints"].append("min_sea_service")

        vessel_type_constraint = self._extract_vessel_type_constraint(user_prompt)
        if vessel_type_constraint:
            constraints["hard_constraints"]["vessel_type"] = vessel_type_constraint
            constraints["unapplied_constraints"].append("vessel_type")

        availability_constraint = self._extract_availability_constraint(user_prompt)
        if availability_constraint:
            constraints["hard_constraints"]["availability"] = availability_constraint
            constraints["unapplied_constraints"].append("availability")

        endorsement_constraint = self._extract_endorsement_constraint(user_prompt)
        if endorsement_constraint:
            if endorsement_constraint.get("ambiguous"):
                constraints["parsing_notes"].append(endorsement_constraint["fragment"])
            else:
                certs = constraints["hard_constraints"].setdefault("certifications", {})
                certs["endorsements_required"] = endorsement_constraint["endorsements_required"]
                certs["endorsement_display_value"] = endorsement_constraint["display_value"]
                constraints["unapplied_constraints"].append("stcw_endorsement")

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
            r"(?im)^rank\s*[:\-]\s*([^\n,]+)",
        ]
        applied_patterns = [
            r"(?i)\bapplied\s+for\s+rank\s*[:\-]?\s*(.+?)(?=\s+(?:present\s+rank|from\s+date|till\s+date|personal(?:\s*&\s*contact)?\s+details|name|email|availability\s+details)\b|$)",
        ]

        def _normalize_raw_rank(raw_rank):
            cleaned = re.sub(r"\s+", " ", str(raw_rank or "").strip(" ,:-"))
            canonical_id, department, seniority_bucket, confidence = self._normalize_rank(cleaned)
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
        runtime_parser = self.config.config
        if runtime_parser.has_section("ShipTypes") and runtime_parser.has_option("ShipTypes", "ship_type_options"):
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
        if not any(token in prompt for token in ("experience", "experienced", "sailed", "worked on", "vessel", "ship")):
            return None
        configured_matches = self._extract_configured_ship_types(prompt)
        if configured_matches:
            return configured_matches[0]
        # Configured labels returned nothing; fall back to hardcoded alias table.
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
                ]
                if any(re.search(pattern, prompt) for pattern in patterns):
                    return canonical
        return None

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
                "patterns": [r"\bmcv\s*\(\s*australia\s*\)", r"\bmcv\b"],
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

        group_patterns = [
            (
                "usa",
                [r"\bvalid\s+us\s+visa\b", r"\bcurrent\s+us\s+visa\b", r"\bhas\s+a\s+valid\s+us\s+visa\b",
                 r"\bus\s+visa\b", r"\bamerican\s+visa\b", r"\bus\s+work\s+authorization\b"],
                "valid US visa",
            ),
            (
                "australia",
                [r"\bvalid\s+australia(?:n)?\s+visa\b", r"\bcurrent\s+australia(?:n)?\s+visa\b", r"\baustralia(?:n)?\s+visa\b"],
                "valid Australia visa",
            ),
            (
                "schengen",
                [r"\bvalid\s+schengen\s+visa\b", r"\bcurrent\s+schengen\s+visa\b", r"\bschengen\s+visa\b"],
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

    def _strip_age_constraint_phrases(self, user_prompt):
        prompt = str(user_prompt or "")
        if not prompt:
            return ""

        patterns = [
            r'between\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'between\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+ages?\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'within\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'with\s*in\s+the\s+age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+range\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'age\s+range\s+of\s+\d{1,2}\s*-\s*\d{1,2}',
            r'age\s+of\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}\s+years?\s+old',
            r'ages?\s+\d{1,2}\s+(?:and|to)\s+\d{1,2}',
            r'aged?\s+\d{1,2}\s*(?:-|to|and)\s*\d{1,2}',
            r'\d{1,2}\s*(?:-|to)\s*\d{1,2}\s+years?\s+old',
            r'at\s+least\s+\d{1,2}\s+years?\s+old',
            r'older\s+than\s+\d{1,2}',
            r'over\s+\d{1,2}',
            r'above\s+\d{1,2}',
            r'minimum\s+age\s+(?:of\s+)?\d{1,2}',
            r'up\s+to\s+\d{1,2}\s+years?\s+old',
            r'younger\s+than\s+\d{1,2}',
            r'under\s+\d{1,2}',
            r'below\s+\d{1,2}',
            r'maximum\s+age\s+(?:of\s+)?\d{1,2}',
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

    def _is_structured_only_prompt(self, user_prompt):
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
                    "raw_text": text[:12000],
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

    def _extract_experienced_ship_types_from_text(self, raw_text):
        text = self._normalize_ship_type(raw_text)
        if not text:
            return []
        configured_matches = self._extract_configured_ship_types(text)
        if configured_matches:
            return configured_matches
        # Configured labels returned nothing; fall back to hardcoded alias table.
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
            visa_def = self._extract_specific_visa_type_from_prompt(snippet)
            if visa_def:
                expiry_snippet = snippet
                expiry_label = re.search(r"(?:valid\s+until)", snippet, flags=re.IGNORECASE)
                if expiry_label:
                    expiry_snippet = snippet[expiry_label.start():]
                expiry_fact = self._extract_date_fact_from_snippet(expiry_snippet)
                visa_records.append({
                    "status": "PARSED",
                    "visa_type": visa_def["canonical"],
                    "visa_group": visa_def["group"],
                    "expiry_date": expiry_fact.get("date"),
                    "expiry_status": expiry_fact.get("status", "MISSING"),
                })
                continue

            normalized_snippet = snippet.lower()
            if any(re.search(no_visa_pattern, normalized_snippet, flags=re.IGNORECASE) for no_visa_pattern in no_visa_patterns):
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
            "passport_fact": passport_fact,
            "visa_fact": visa_fact,
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
        table_grade_patterns = [
            r"master\s*\((?:fg|ncv)\)",
            r"chief\s+officer\s*\((?:fg|ncv)\)",
            r"chief\s+mate\s*\((?:fg|ncv)\)",
            r"first\s+mate\s*\((?:fg|ncv)\)",
            r"second\s+mate\s*\((?:fg|ncv)\)|second\s+officer\s*\((?:fg|ncv)\)|2nd\s+officer\s*\((?:fg|ncv)\)",
            r"third\s+officer\s*\((?:fg|ncv)\)|3rd\s+officer\s*\((?:fg|ncv)\)",
            r"chief\s+engineer\s*\((?:fg|ncv)\)",
            r"second\s+engineer\s*\((?:fg|ncv)\)|2nd\s+engineer\s*\((?:fg|ncv)\)",
            r"third\s+engineer\s*\((?:fg|ncv)\)|3rd\s+engineer\s*\((?:fg|ncv)\)",
            r"fourth\s+engineer\s*\((?:fg|ncv)\)|4th\s+engineer\s*\((?:fg|ncv)\)",
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
            if re.search(r"\bcoc\b|certificate\s+of\s+competency", line, flags=re.IGNORECASE):
                snippets.append(" ".join(lines[idx:idx + 4]))
                continue
            if (
                any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in table_grade_patterns)
                and len(re.findall(date_token_pattern, line, flags=re.IGNORECASE)) >= 2
            ):
                context_start = max(0, idx - 1)
                snippets.append(" ".join(lines[context_start:idx + 1]))

        if not snippets:
            for match in re.finditer(r"\bcoc\b|certificate\s+of\s+competency", text, flags=re.IGNORECASE):
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
            (r"chief\s+engineer", "chief_engineer"),
            (r"second\s+engineer|2nd\s+engineer", "2nd_engineer"),
            (r"third\s+engineer|3rd\s+engineer", "3rd_engineer"),
            (r"fourth\s+engineer|4th\s+engineer", "4th_engineer"),
            (r"meo\s+class\s+i\s*(?:\(\s*motor\s*\))?\b", "chief_engineer"),
            (r"meo\s+class\s+ii\s*(?:\(\s*motor\s*\))?\b", "2nd_engineer"),
            (r"meo\s+class\s+iv\b", "4th_engineer"),
            (r"master", "master"),
            (r"first\s+mate|chief\s+mate|chief\s+officer", "chief_officer"),
            (r"second\s+mate|2nd\s+officer|second\s+officer", "2nd_officer"),
            (r"third\s+mate|3rd\s+officer|third\s+officer", "3rd_officer"),
        ]

        for snippet in snippets:
            normalized_snippet = snippet.lower()
            grade = None
            for pattern, canonical_grade in grade_patterns:
                if re.search(pattern, normalized_snippet):
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
            snippet = text[max(0, match.start() - 40):match.start() + 220]
            local_window = text[max(0, match.start() - 20):min(len(text), match.end() + 80)]
            lowered = snippet.lower()
            lowered_local = local_window.lower()
            if re.search(r"\b(no|not held|n/?a|none|absent)\b", lowered):
                return "absent"
            if re.search(r"\b(expired|lapsed|out of date)\b", lowered_local):
                return "expired"
            if re.search(r"\b(pending|in progress|applied for)\b", lowered):
                return "unknown"

            # Dates should only drive certificate validity when the alias carries
            # a nearby certificate-local cue. This prevents expiry dates from
            # later endorsement rows from being attached to dense course lists.
            if re.search(
                r"\b(valid until|valid till|validity|expires|expires on|expiry date|exp date|exp\b)\b",
                lowered_local,
            ):
                expiry_fact = self._extract_date_fact_from_snippet(local_window)
                if expiry_fact.get("status") == "PARSED":
                    if expiry_fact.get("date") and expiry_fact["date"] < date.today():
                        return "expired"
                    return "present"

            if re.search(r"\b(held|valid|completed|certificate)\b", lowered):
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
            "pst": ["pst", "personal survival techniques"],
            "fpff": ["fpff", "fire prevention and fire fighting"],
            "efa": ["efa", "elementary first aid"],
            "pssr": ["pssr", "personal safety and social responsibilities"],
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
            "tanker_oil": ["oil tanker endorsement"],
            "tanker_chemical": ["chemical tanker endorsement"],
            "tanker_gas": ["gas tanker endorsement"],
            "dp_operational": ["dpo", "dp operator"],
            "gmdss": ["gmdss"],
        }
        return {
            endorsement_id: self._extract_certificate_state(text, aliases)
            for endorsement_id, aliases in endorsement_aliases.items()
        }

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

        label_pattern = r'(?:date\s+of\s+birth|dob|d\.o\.b\.?)'
        date_patterns = [
            r'(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})',
            r'(\d{1,2})[\s\/\-.]+([A-Za-z]{3,9})[\s\/\-.]+(\d{2,4})',
            r'([A-Za-z]{3,9})[\s\/\-.]+(\d{1,2}),?[\s\/\-.]+(\d{2,4})',
        ]
        ambiguous_numeric_pattern = r'\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})\b'

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

            if re.search(ambiguous_numeric_pattern, snippet):
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

        if cache_key:
            source_text = text_cache.get(cache_key, "")
            if not source_text:
                try:
                    source_text = self.pdf_processor.extract_text(str(original_path)) or ""
                except Exception:
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
        logistics_fact = self._extract_logistics_from_text(source_text)
        metadata_entry = {}
        if folder_metadata:
            metadata_entry = folder_metadata.get(filename) or {}
        applied_ship_types = metadata_entry.get("applied_ship_types")
        if not isinstance(applied_ship_types, list):
            applied_ship_types = []
        current_rank_fact = self._extract_rank_fact_from_text(source_text)
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
                "passport_valid": logistics_fact.get("passport_valid"),
                "us_visa_valid": logistics_fact.get("us_visa_valid"),
                "us_visa_status": logistics_fact.get("us_visa_status"),
                "us_visa_expiry_date": logistics_fact.get("us_visa_expiry_date").isoformat() if logistics_fact.get("us_visa_expiry_date") else None,
                "availability_date": None,
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
            },
            "fact_meta": {
                "personal.dob": self._build_fact_meta(
                    age_info.get("dob").isoformat() if age_info.get("dob") else None,
                    confidence=age_info.get("dob_confidence"),
                    extraction_method=age_info.get("dob_extraction_method"),
                    status=age_info.get("dob_parse_status"),
                    source_label=age_info.get("dob_source_label"),
                    context={"field": "personal.dob"},
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
                    context={"field": "derived.age_years", "derived_from": "personal.dob"},
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
                "logistics.passport_expiry_date": self._build_fact_meta(
                    logistics_fact.get("passport_expiry_date").isoformat() if logistics_fact.get("passport_expiry_date") else None,
                    confidence=logistics_fact.get("passport_fact", {}).get("confidence"),
                    extraction_method=logistics_fact.get("passport_fact", {}).get("extraction_method", ""),
                    status=logistics_fact.get("passport_expiry_status", "MISSING"),
                    source_label=logistics_fact.get("passport_fact", {}).get("source_label"),
                    context={"field": "logistics.passport_expiry_date"},
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
            visa_type, expiry_date = valid_records[0]
            return self._base_rule_result(
                "PASS",
                "US_VISA_VALID",
                f"Visa {visa_type} is valid until {expiry_date.isoformat()} for requested filter '{requested_label}'.",
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

        return self._base_rule_result(
            "UNKNOWN",
            "US_VISA_MISSING",
            f"Could not determine visa evidence for requested filter '{requested_label}'.",
            actual_value=[],
            expected_value=constraint,
            confidence=((candidate_facts.get("fact_meta") or {}).get("travel.visa_records") or {}).get("confidence"),
            unknown_reason="FACTUAL_UNKNOWN",
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
                    expected_value=[requested_ship_type] if requested_ship_type else [],
                    confidence=((candidate_facts.get("fact_meta") or {}).get("experience.vessel_types") or {}).get("confidence"),
                    unknown_reason="FACTUAL_UNKNOWN",
                ))
            elif self._evaluate_rule("contains_any", experienced_ship_types, [requested_ship_type]):
                results.append(self._base_rule_result(
                    "PASS",
                    "EXPERIENCE_SHIP_TYPE_MATCH",
                    f"Candidate resume shows experience on '{experience_ship_type_constraint}'.",
                    actual_value=experienced_ship_types,
                    expected_value=[requested_ship_type] if requested_ship_type else [],
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
                    expected_value=[requested_ship_type] if requested_ship_type else [],
                    confidence=((candidate_facts.get("fact_meta") or {}).get("experience.vessel_types") or {}).get("confidence"),
                ))

        if hard_filter_debug:
            print(
                "[FILTERS] "
                f"candidate_id={candidate_id or '-'} "
                f"activated_rules={activated_rules}"
            )

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
            chunks = self.prepper.chunk_text(text, resume_id, rank)
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
        target_folder = Path(self.config.download_root) / rank.replace(' ', '_').replace('/', '-')
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
            chunk_text = text[:12000]
            pseudo_chunk = {
                "id": f"fallback-{resume_id}-{idx}",
                "score": score,
                "metadata": {
                    "resume_id": resume_id,
                    "rank": rank,
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

    def _reason_with_llm(self, prompt, retrieved_chunks, past_feedback):
        """Enhanced LLM reasoning with confidence scoring and past feedback"""
        context = "\n---\n".join([chunk['metadata']['raw_text'] for chunk in retrieved_chunks])
        
        # Build feedback context
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
        
        # Detect compound query for enhanced prompt
        is_compound = ' and ' in prompt.lower() or ' or ' in prompt.lower()
        compound_note = '[Note: This is a compound query - ALL conditions must be satisfied]' if ' and ' in prompt.lower() else ''
        
        reasoning_prompt = f"""
Analyze the resume context below to determine if it matches the user's requirements.

User Requirements: "{prompt}"
{compound_note}

Resume Context:
{context}
{feedback_context}

Instructions:

1. **VISA VALIDATION - VERY STRICT:**
   - Supported visa evidence includes: "US Visa", "C1/D (USA)", "C1 (USA)", "D (USA)", "B1/B2 (USA)", "Australia Entry visa", "MCV (Australia)", and "Schengen"
   - If a visa field shows "Other", blank, "N/A", or "None" → This means NO VALID VISA (mark FALSE)
   - If a supported visa type is present → Check expiry date
   - If expiry date is before the current date → EXPIRED (mark FALSE)
   - If expiry date shows year "0001" or "-0001" → INVALID (mark FALSE)
   - ONLY mark TRUE if there's a specific supported visa type with valid future expiry
   - NEVER substitute one country's visa for another country's visa
   - A certificate, CoC, license, flag endorsement, or document issued by a country is NOT a visa or right-to-work unless the resume explicitly says so

2. COMPOUND REQUIREMENTS:
   - "A and B" requires BOTH A AND B
   - "A or B" requires EITHER A OR B (or both)
   - If ANY required condition (in AND queries) is missing → mark FALSE

3. TERMINOLOGY FLEXIBILITY:
   - "US visa" matches: "C1/D visa", "C1 visa", "D visa", "B1/B2 visa", "American visa", "US work authorization"
   - "Australia visa" matches: "Australia Entry visa", "MCV (Australia)"
   - "Schengen visa" matches: "Schengen"
   - "UK visa" does NOT match US visa, Schengen visa, or UK-issued certificates
   - "Oil tanker" matches: "crude oil tanker", "product tanker", "VLCC", "oil/chem tanker"
   - "Bulk carrier" matches: "dry cargo vessel", "handy size", "capesize", "panamax"

4. CONFIDENCE SCORING:
   - 0.9-1.0: Perfect match, all requirements clearly met with strong evidence
   - 0.7-0.89: Good match, requirements met but some minor ambiguity
   - 0.5-0.69: Uncertain, some requirements unclear or borderline (FLAG FOR REVIEW)
   - 0.0-0.49: Poor match, requirements not met or very unclear
   
   **Flag as "uncertain" if confidence < 0.7 - user will review these**

5. REASON CLARITY:
   - Be specific about visa status (type, expiry date)
   - Quantify experience (years, vessel types)
   - State what's missing if no-match
   - Use natural language recruiters understand

Respond ONLY with valid JSON (no markdown):
{{"is_match": boolean, "reason": "Clear specific explanation", "confidence": 0.0-1.0}}

Examples of GOOD responses:

{{"is_match": true, "reason": "Candidate has valid US C1/D visa expiring 2028 and 12 years as Chief Engineer on oil tankers including VLCCs", "confidence": 0.95}}

{{"is_match": false, "reason": "US visa field shows 'Other' (not a valid US visa type), though candidate has extensive oil tanker experience", "confidence": 0.3}}

{{"is_match": true, "reason": "Valid US B1/B2 visa until 2027, but only 3 years oil tanker experience (requirement was 5+)", "confidence": 0.65}}

{{"is_match": false, "reason": "US visa expired in 2023, candidate needs valid current visa", "confidence": 0.2}}
"""
        
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.reasoning_model}:generateContent"
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.config.gemini_api_key}
        payload = {"contents": [{"parts": [{"text": reasoning_prompt}]}]}
        
        try:
            response = requests.post(api_url, headers=headers, json=payload)
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
            target_folder = Path(self.config.download_root) / rank.replace(' ', '_').replace('/', '-')
            
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
            structured_only_prompt = self._is_structured_only_prompt(user_prompt)
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
                    }
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
                audit_entry["llm_reached"] = True
                audit_entry["result_bucket"] = "llm_no_match"
                if age_constraint and age_value is not None and dob_value is not None:
                    prompt_for_reasoning = (
                        f"{user_prompt}\n"
                        f"Computed candidate age from DOB: {age_value} years old "
                        f"(DOB: {dob_value.isoformat()}). Treat this age as authoritative. "
                        f"This candidate already passed the deterministic age gate."
                    )
                
                # LLM reasoning with confidence
                llm_started_at = time.perf_counter()
                llm_result = self._reason_with_llm(prompt_for_reasoning, chunks, past_feedback)
                self._record_perf_timing(perf_state, "llm_reasoning", time.perf_counter() - llm_started_at)
                
                if llm_result.get('is_match'):
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
                    }
                    
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

    def __init__(self, gemini_api_key):
        print("\n*** RUNNING with Multi-Stage Retrieval + Learning ***\n")
        if Analyzer._instance is None:
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
