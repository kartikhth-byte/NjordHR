# ai_analyzer.py - COMPLETE VERSION WITH SSE STREAMING + LEARNING + MULTI-STAGE RETRIEVAL

import os
import sys
import json
import time
import hashlib
import sqlite3
import re
import io
import threading
from pathlib import Path
from datetime import datetime

# --- Core Dependencies ---
import requests
import fitz  # PyMuPDF
from PIL import Image
from pinecone import Pinecone, ServerlessSpec

# --- Optional/Specialized Dependencies ---
try:
    import pytesseract
    HAS_OCR = True
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
    def registry_db_path(self): return self.config.get('Advanced', 'registry_db_path', fallback='registry.db')
    @property
    def feedback_db_path(self): return self.config.get('Advanced', 'feedback_db_path', fallback='feedback.db')


# ==============================================================================
# 2. LOCAL FILE REGISTRY (SQLite)
# ==============================================================================
class FileRegistry:
    """Manages a local SQLite database to track file states and avoid reprocessing."""
    DB_VERSION = 2
    
    def __init__(self, db_path='registry.db'):
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
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.api_key = _resolve_supabase_api_key()
        self.headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.lock = threading.Lock()

    def _request(self, method, path, params=None, json_body=None, timeout=15):
        resp = requests.request(
            method=method,
            url=f"{self.supabase_url}{path}",
            params=params or {},
            json=json_body,
            headers=self.headers,
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
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }]
        with self.lock:
            self._request(
                "POST",
                "/rest/v1/ai_file_registry",
                params={"on_conflict": "file_key"},
                json_body=body,
                timeout=20,
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
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }]
        with self.lock:
            self._request("POST", "/rest/v1/ai_feedback", json_body=body, timeout=20)
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

    def chunk_text(self, text, resume_id, rank):
        tokens = text.split()
        chunk_size, overlap = 400, 50
        chunks = []
        for i in range(0, len(tokens), chunk_size - overlap):
            chunk_text = " ".join(tokens[i:i + chunk_size])
            chunk_metadata = {"resume_id": resume_id, "rank": rank, "raw_text": chunk_text}
            chunks.append({"text": chunk_text, "metadata": chunk_metadata})
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

    def namespace_vector_count(self, namespace):
        try:
            stats = self._coerce_dict(self.index.describe_index_stats())
            namespaces = stats.get("namespaces", {})
            ns_meta = namespaces.get(namespace, {})
            return int(ns_meta.get("vector_count", 0) or 0)
        except Exception as e:
            self.last_error = f"Failed to inspect namespace stats: {e}"
            print(f"[ERROR] {self.last_error}")
            return 0

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
            self.index.upsert(vectors=vectors_to_upsert, namespace=rank)
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
    
    def __init__(self, config_parser):
        print("[INIT] Initializing AIResumeAnalyzer with Multi-Stage Retrieval...")
        self.config = ConfigManager(config_parser)
        if _should_use_cloud_ai_store():
            try:
                self.registry = SupabaseFileRegistry()
                self.feedback = SupabaseFeedbackStore()
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
        print("[INIT] Initialization complete")

    def _ingest_folder(self, folder_path, rank):
        print(f"[{rank}] Starting ingestion scan...")
        pdf_paths = list(Path(folder_path).glob("*.pdf"))
        files_to_process = [p for p in pdf_paths if self.registry.needs_processing(str(p), p.stat().st_mtime)]
        
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
            
            resume_id = self.registry.get_resume_id(str(path))
            chunks = self.prepper.chunk_text(text, resume_id, rank)
            embeddings = self.prepper.get_embeddings([c['text'] for c in chunks])

            if embeddings:
                embedding_failures = 0
                self.vector_db.upsert_chunks(chunks, embeddings, rank)
                print(f"    - Indexed {len(chunks)} chunks.")
                self.registry.upsert_file_record(str(path), path.stat().st_mtime, resume_id)
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
        prompt_lower = user_prompt.lower()
        
        # Check for AND logic
        if ' and ' in prompt_lower:
            sub_queries = [q.strip() for q in user_prompt.split(' and ')]
            return (True, 'AND', sub_queries)
        
        # Check for OR logic  
        elif ' or ' in prompt_lower:
            sub_queries = [q.strip() for q in user_prompt.split(' or ')]
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
        for pdf_path in target_folder.glob("*.pdf"):
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

1. **US VISA VALIDATION - VERY STRICT:**
   - If the "US Visa / Other" field shows "Other" → This means NO US VISA (mark FALSE)
   - If the field shows "US Visa", "C1/D", "B1/B2", etc. → Check expiry date
   - If expiry date is before 2025 → EXPIRED (mark FALSE)
   - If expiry date shows year "0001" or "-0001" → INVALID (mark FALSE)
   - If the field is blank or "N/A" → NO VISA (mark FALSE)
   - ONLY mark TRUE if there's a specific visa type (not "Other") with valid future expiry

2. COMPOUND REQUIREMENTS:
   - "A and B" requires BOTH A AND B
   - "A or B" requires EITHER A OR B (or both)
   - If ANY required condition (in AND queries) is missing → mark FALSE

3. TERMINOLOGY FLEXIBILITY:
   - "US visa" matches: "C1/D visa", "B1/B2 visa", "American visa", "US work authorization"
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

    def run_analysis_stream(self, rank, user_prompt):
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
            
            # Parse query to detect compound logic
            is_compound, operator, sub_queries = self._parse_compound_query(user_prompt)
            
            if is_compound:
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
            
            # Get past feedback for learning
            past_feedback = self.feedback.get_recent_feedback(user_prompt)
            
            verified_matches = []
            uncertain_matches = []
            
            for i, (resume_id, chunks) in enumerate(candidates.items(), 1):
                original_path = next((p for p in target_folder.glob("*.pdf") 
                                    if self.registry.get_resume_id(str(p)) == resume_id), None)
                filename = original_path.name if original_path else resume_id[:8]
                
                yield {"type": "progress", "current": i, "total": total_candidates, 
                       "message": f"Analyzing: {filename}"}
                
                # LLM reasoning with confidence
                llm_result = self._reason_with_llm(user_prompt, chunks, past_feedback)
                
                if llm_result.get('is_match'):
                    match_data = {
                        "filename": filename,
                        "reason": llm_result.get('reason', 'Match found.'),
                        "confidence": llm_result.get('confidence', 0.5)
                    }
                    
                    # Flag uncertain matches (confidence < 0.7)
                    if match_data['confidence'] < 0.7:
                        uncertain_matches.append(match_data)
                        yield {"type": "uncertain_found", "match": match_data, 
                               "current": i, "total": total_candidates}
                    else:
                        verified_matches.append(match_data)
                        yield {"type": "match_found", "match": match_data, 
                               "current": i, "total": total_candidates}
                
                # Rate limiting
                if i < total_candidates:
                    time.sleep(2.5)
            
            yield {"type": "complete", 
                   "verified_matches": verified_matches,
                   "uncertain_matches": uncertain_matches,
                   "message": f"Found {len(verified_matches)} verified, {len(uncertain_matches)} uncertain matches."}
        
        except Exception as e:
            print(f"[ERROR] Analysis failed: {e}")
            import traceback
            traceback.print_exc()
            yield {"type": "error", "message": f"Analysis failed: {str(e)}"}

    def run_analysis(self, rank, user_prompt):
        """Non-streaming version"""
        verified_matches = []
        uncertain_matches = []
        message = ""
        
        for event in self.run_analysis_stream(rank, user_prompt):
            if event['type'] == 'match_found':
                verified_matches.append(event['match'])
            elif event['type'] == 'uncertain_found':
                uncertain_matches.append(event['match'])
            elif event['type'] == 'complete':
                message = event['message']
            elif event['type'] == 'error':
                return {"success": False, "verified_matches": [], "uncertain_matches": [], 
                        "message": event['message']}
        
        return {
            "success": True, 
            "verified_matches": verified_matches,
            "uncertain_matches": uncertain_matches,
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

    def run_analysis(self, target_folder, prompt):
        rank_name = Path(target_folder).name.replace('_', ' ').replace('-', '/')
        return Analyzer._instance.run_analysis(rank_name, prompt)
    
    def run_analysis_stream(self, target_folder, prompt):
        rank_name = Path(target_folder).name.replace('_', ' ').replace('-', '/')
        for progress_event in Analyzer._instance.run_analysis_stream(rank_name, prompt):
            yield progress_event
    
    def store_feedback(self, filename, query, llm_decision, llm_reason, llm_confidence,
                       user_decision, user_notes=""):
        """Store user feedback"""
        Analyzer._instance.store_feedback(
            filename, query, llm_decision, llm_reason, llm_confidence,
            user_decision, user_notes
        )
