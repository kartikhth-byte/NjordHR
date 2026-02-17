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
    def gemini_api_key(self): return self.config.get('Credentials', 'Gemini_API_Key')
    @property
    def pinecone_api_key(self): return self.config.get('Credentials', 'Pinecone_API_Key')
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
        model_candidates = [configured_model]
        # Fallback to current Gemini embedding model if older model is configured.
        if configured_model == "text-embedding-004":
            model_candidates.append("gemini-embedding-001")
        elif configured_model == "gemini-embedding-001":
            model_candidates.append("text-embedding-004")

        api_versions = ["v1beta", "v1"]

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
                            return [item["values"] for item in payload["embeddings"]]
                        self.last_error = "Embedding API returned no embeddings."
                        continue

                    error_text = response.text.strip().replace("\n", " ")
                    if len(error_text) > 300:
                        error_text = error_text[:300] + "..."
                    self.last_error = (
                        f"Embedding request failed ({response.status_code}) "
                        f"using {model_name} on {api_version}: {error_text}"
                    )
                except requests.exceptions.RequestException as e:
                    self.last_error = f"Embedding request error using {model_name} on {api_version}: {e}"

        if self.last_error:
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
    def __init__(self, config_manager):
        self.config = config_manager
        self.pc = Pinecone(api_key=self.config.pinecone_api_key)
        self.index_name = self.config.pinecone_index
        self._index = None

    @property
    def index(self):
        if self._index is None:
            available_indexes = self.pc.list_indexes().names()
            
            if self.index_name not in available_indexes:
                print(f"[PINECONE] Creating new index: {self.index_name}")
                self.pc.create_index(
                    name=self.index_name,
                    dimension=768,
                    metric='cosine',
                    spec=ServerlessSpec(cloud='aws', region='us-east-1')
                )
            
            self._index = self.pc.Index(self.index_name)
        return self._index

    def upsert_chunks(self, chunks, embeddings, rank):
        if not embeddings: return
        
        vectors_to_upsert = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{chunk['metadata']['resume_id']}-{i}"
            vectors_to_upsert.append({"id": chunk_id, "values": embeddings[i], "metadata": chunk['metadata']})
        
        if vectors_to_upsert:
            self.index.upsert(vectors=vectors_to_upsert, namespace=rank)

    def query(self, query_embedding, rank, top_k=30):
        try:
            results = self.index.query(namespace=rank, vector=query_embedding, top_k=top_k, include_metadata=True)
            return results['matches']
        except Exception as e:
            print(f"[ERROR] Query failed: {e}")
            return []


# ==============================================================================
# 7. CORE RAG ENGINE WITH LEARNING + MULTI-STAGE RETRIEVAL
# ==============================================================================
class AIResumeAnalyzer:
    """The main RAG pipeline engine with feedback learning and multi-stage retrieval."""
    
    def __init__(self, config_parser):
        print("[INIT] Initializing AIResumeAnalyzer with Multi-Stage Retrieval...")
        self.config = ConfigManager(config_parser)
        self.registry = FileRegistry()
        self.feedback = FeedbackStore()
        self.pdf_processor = AdvancedPDFProcessor()
        self.prepper = RAGPrepper(self.config)
        self.vector_db = PineconeManager(self.config)
        print("[INIT] Initialization complete")

    def _ingest_folder(self, folder_path, rank):
        print(f"[{rank}] Starting ingestion scan...")
        pdf_paths = list(Path(folder_path).glob("*.pdf"))
        files_to_process = [p for p in pdf_paths if self.registry.needs_processing(str(p), p.stat().st_mtime)]
        
        if not files_to_process:
            print(f"[{rank}] Index is up to date.")
            return

        print(f"[{rank}] Found {len(files_to_process)} new/updated files to index.")
        for path in files_to_process:
            print(f"  -> Processing: {path.name}")
            text = self.pdf_processor.extract_text(str(path))
            if not text or len(text.strip()) < 100:
                print("    - SKIPPED: Not enough text extracted.")
                continue
            
            resume_id = self.registry.get_resume_id(str(path))
            chunks = self.prepper.chunk_text(text, resume_id, rank)
            embeddings = self.prepper.get_embeddings([c['text'] for c in chunks])

            if embeddings:
                self.vector_db.upsert_chunks(chunks, embeddings, rank)
                print(f"    - Indexed {len(chunks)} chunks.")
                self.registry.upsert_file_record(str(path), path.stat().st_mtime, resume_id)

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
            return {}
        
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
            self._ingest_folder(str(target_folder), rank)
            
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
                    yield {"type": "error", "message": f"Could not generate query embedding. {error_hint}"}
                    return

                yield {"type": "status", "message": "Searching vector database..."}
                search_results = self.vector_db.query(query_embedding[0], rank, top_k=50)
                
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
            config.read('config.ini')
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
