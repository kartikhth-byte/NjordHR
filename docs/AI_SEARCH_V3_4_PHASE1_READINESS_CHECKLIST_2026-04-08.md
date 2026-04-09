# AI Search v3.4 Phase 1 Readiness Checklist

Date: 2026-04-08

Purpose:
- record which v3.4 Phase 1 exit criteria are already evidenced by the current implementation
- separate implemented behavior from still-missing sign-off evidence
- give the next operator a concrete readiness view without re-reading the whole spec

Primary specs:
- `/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/v3.4-implementation-discipline-and-feedback-loop.md`
- `/Users/kartikraghavan/Tools/NjordHR/IMPLEMENTATION_RISKS_V3_4.md`

Status values used below:
- `met`: implemented and evidenced well enough from current tests/diagnostics/docs
- `partial`: implemented in substance, but explicit sign-off evidence is still missing
- `open`: still needs implementation or verification evidence

## 1. Automated Tests

### 1.1 Full test suite passes
- Status: `partial`
- Current evidence:
  - multiple targeted suites pass for the recently touched areas:
    - `tests/test_ai_analyzer_dob_parsing.py`
    - `tests/test_ai_analyzer_age_filters.py`
    - `tests/test_ai_analyzer_ship_type_filters.py`
    - `tests/test_ai_analyzer_job_constraints.py`
    - `tests/test_ai_analyzer_pinecone.py`
    - `tests/test_rag_chunking.py`
- Gap:
  - no current recorded evidence in this checklist that the entire repository test suite was run end-to-end in one pass after the latest combined changes

### 1.2 Validated baseline matches
- Status: `partial`
- Current evidence:
  - deterministic age-path validation and broader extractor diagnostics were run and reviewed
  - packaged deterministic age-gate validation was previously noted in handoff
- Gap:
  - no single current sign-off line here yet that states the exact validated baseline run and result artifact set

## 2. Extraction Quality

### 2.1 Rank extraction quality
- Status: `met`
- Current evidence:
  - diagnostic-first tuning completed
  - broader validation pass completed
  - backlog/handoff now reflect rank as materially improved and stable for the current corpus family

### 2.2 COC extraction quality
- Status: `met`
- Current evidence:
  - folder-level diagnostics completed
  - broader validation completed across deck and engineer folders
  - remaining misses are now mostly incomplete-source or narrower residuals, not one broad missing pattern

### 2.3 STCW basic extraction quality
- Status: `partial`
- Current evidence:
  - extractor and tests are present
  - diagnostics were run
  - current quality judgment is documented: intentionally conservative, largest remaining unknown bucket
- Gap:
  - this is usable, but the sign-off pack should explicitly state whether current STCW quality is accepted for Phase 1 launch under the conservative review-path model

### 2.4 US visa extraction quality
- Status: `met`
- Current evidence:
  - dedicated visa tests exist
  - corpus validation indicates stable behavior for the current resume family
  - current docs already state that DOB/visa do not need broadening work

## 3. Prompt Corpus Coverage

### 3.1 Age range prompt parsing coverage
- Status: `open`
- Current evidence:
  - parser implementation and tests exist
- Gap:
  - no explicit 20-prompt sampled coverage report or threshold result recorded yet

### 3.2 US visa prompt parsing coverage
- Status: `open`
- Current evidence:
  - parser implementation and tests exist
- Gap:
  - no explicit 20-prompt sampled coverage report or threshold result recorded yet

### 3.3 Rank prompt parsing coverage
- Status: `open`
- Current evidence:
  - parser implementation and tests exist
  - resume-side diagnostics are strong
- Gap:
  - prompt-corpus threshold measurement still needs an explicit report

### 3.4 COC prompt parsing coverage
- Status: `open`
- Current evidence:
  - parser implementation and tests exist
- Gap:
  - prompt-corpus threshold measurement still needs an explicit report

### 3.5 STCW prompt parsing coverage
- Status: `open`
- Current evidence:
  - parser implementation and tests exist
- Gap:
  - prompt-corpus threshold measurement still needs an explicit report

Assessment:
- prompt-corpus coverage is the clearest remaining Phase 1 readiness gap
- this is a launch-gate evidence gap, not primarily a parser-code gap

## 4. Filter Behavior and Routing

### 4.1 `FAIL` candidates never reach LLM reasoning
- Status: `met`
- Current evidence:
  - covered in `tests/test_ai_analyzer_age_filters.py`
  - analyzer audit output carries `llm_reached`

### 4.2 `applied_constraints` contract verified
- Status: `met`
- Current evidence:
  - contract enforced in hard-filter activation logic
  - covered by job-constraint tests and follow-up hard-filter diagnostics

### 4.3 Partial-evaluation notice surfaced
- Status: `met`
- Current evidence:
  - backend emits `partial_evaluation` and `partial_evaluation_notice`
  - frontend renders notice
  - behavior covered in `tests/test_ai_analyzer_job_constraints.py`

### 4.4 `FACTUAL_UNKNOWN` review path functional
- Status: `met`
- Current evidence:
  - typed UNKNOWN routing implemented
  - `UNKNOWN` candidates surface in `Needs Review`
  - `unknown_reason_types` rendered in UI
  - covered by job-constraint tests

### 4.5 Graceful-failure presentation decided and verified
- Status: `met`
- Current evidence:
  - backend emits graceful-failure event
  - frontend renders warning with unsupported constraints / parsing notes
  - covered by job-constraint tests

## 5. Dual-Version and Migration Readiness

### 5.1 v1.1 candidates return UNKNOWN rather than FAIL for v2.0-only rules
- Status: `met`
- Current evidence:
  - covered in `tests/test_ai_analyzer_job_constraints.py`

### 5.2 Synchronous re-extraction controls implemented
- Status: `met`
- Current evidence:
  - timeout
  - per-search limit
  - cooldown
  - failure fallback
  - concurrency guard
  - waiter/owner timeout follow-up fixed
  - covered by targeted job-constraint tests

### 5.3 Background re-extraction functional
- Status: `partial`
- Current evidence:
  - indexing / re-extraction machinery exists
  - Pinecone false-empty issue is fixed
- Gap:
  - this checklist does not yet contain explicit sample-run evidence showing the background v2.0 re-extraction path exercised as a migration task

### 5.4 Re-extraction idempotent
- Status: `partial`
- Current evidence:
  - implementation shape suggests idempotent behavior is intended
- Gap:
  - this checklist does not yet reference a concrete v2.0 re-extraction idempotence run/result

### 5.5 Migration progress observable
- Status: `partial`
- Current evidence:
  - partial-evaluation and dual-version behavior are observable in search
- Gap:
  - still need explicit evidence that admin-facing v1.1 vs v2.0 progress reporting exists and was exercised against the spec criterion

Assessment:
- migration-readiness is the second major evidence gap after prompt-corpus coverage

## 6. Audit / Review Loop Requirements

### 6.1 Search session ID logging
- Status: `met`
- Current evidence:
  - implemented in `backend_server.py`
  - written via candidate-level audit logging

### 6.2 Candidate-level audit logging
- Status: `met`
- Current evidence:
  - implemented and tested
  - includes hard-filter decision, reasons, result bucket, LLM reachability, ship-type filters

### 6.3 Uncertain-match feedback logging
- Status: `met`
- Current evidence:
  - feedback store exists
  - `/submit_feedback` endpoint exists

### 6.4 Weekly / periodic review loop operationalized
- Status: `partial`
- Current evidence:
  - the diagnostic-first method is now established and documented
- Gap:
  - no recurring operating record is attached here showing prompt-corpus review cadence in practice

## 7. Retrieval / AI-T6

### 7.1 Retrieval chunking upgrade
- Status: `partial`
- Current evidence:
  - main indexed chunker is now structure-aware
  - direct chunking tests exist
- Deferred:
  - keyword-fallback pseudo-chunking is not improved yet
- Judgment:
  - this is acceptable to stop at for now because fallback retrieval is a degraded path, not the primary search path

## 8. Overall Readiness Judgment

Current overall judgment:
- core deterministic Phase 1 implementation looks largely complete
- the strongest remaining gaps are evidence / sign-off gaps, not parser-code gaps

Most likely still-needed work before claiming Phase 1 fully closed:
1. prompt-corpus coverage report for active deterministic families
2. explicit extraction-quality sign-off summary tying existing diagnostics to the Section 10.7 thresholds
3. migration-readiness evidence pack:
   - background re-extraction sample run
   - idempotence check
   - migration-progress observability check
4. ship-type parser scope alignment:
   - current ship-type recognition is still coarse-bucket oriented and does not yet cover the full configured `ShipTypes.ship_type_options` catalog in `config.ini`
   - this is now a clearly scoped parser-alignment follow-up, separate from the already-implemented applied/experienced ship-type filter wiring

Recommended next step:
- do not broaden extractor or retrieval behavior next
- produce the prompt-corpus / launch-gate evidence pack first
