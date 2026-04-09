# AI Search v3.4 Migration Readiness Evidence

Date: 2026-04-09

Purpose:
- record the current migration-readiness evidence for the v1.1 -> v2.0 transition
- separate controls that are already implemented and tested from migration criteria that still need explicit run artifacts

Primary references:
- `/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/v3.4-implementation-discipline-and-feedback-loop.md`

Supporting implementation/tests:
- `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
- `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
- `/Users/kartikraghavan/Tools/NjordHR/csv_manager.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_job_constraints.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_ai_analyzer_pinecone.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_backend_event_log_flow.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_dual_write_repo.py`
- `/Users/kartikraghavan/Tools/NjordHR/tests/test_csv_candidate_event_repo.py`

## 1. What Is Already Evidenced

### 1.1 Version-mismatch routing

Current evidence:
- v1.1 candidates evaluated against v2.0-only rule families return `UNKNOWN`, not `FAIL`
- typed `VERSION_MISMATCH_UNKNOWN` routing is covered in `tests/test_ai_analyzer_job_constraints.py`

Judgment:
- this criterion is implemented and evidenced

### 1.2 Synchronous re-extraction controls

Current evidence in `tests/test_ai_analyzer_job_constraints.py`:
- successful synchronous re-extraction upgrades a v1.1 candidate before hard-filter evaluation
- per-search limit produces partial-evaluation behavior
- timeout falls back to partial evaluation
- cooldown blocks immediate repeated re-extraction
- waiter timeout exceeds owner timeout, protecting the concurrency path from early fallback

Current implementation evidence in `ai_analyzer.py`:
- cooldown
- per-search limit
- timeout
- failure fallback
- concurrency guard
- user-visible partial-evaluation notice

Judgment:
- the mandatory synchronous controls from the spec are implemented and directly evidenced through targeted tests

### 1.3 Namespace/index stability relevant to migration

Current evidence in `tests/test_ai_analyzer_pinecone.py`:
- namespace emptiness checks retry before declaring empty
- alternate index resolution works
- the primary namespace existence path uses list/id-based probing

Judgment:
- the previously observed false-empty namespace issue is fixed and no longer blocks migration-readiness confidence

## 2. What Is Only Partially Evidenced

### 2.1 Background re-extraction functional

Current evidence:
- code paths and registry/indexing machinery exist
- synchronous re-extraction path is validated
- offline real-PDF extraction-path sample now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_reextract_sample.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_current.json`
  - current result: `11/11` processed rows produced `facts_version = 2.0`

Missing evidence:
- no documented networked/background scheduler run showing the full ingestion-orchestrated migration path over a sample corpus

Current judgment:
- partially met: the underlying v2.0 extraction path is evidenced on a representative real-PDF sample, but the full background migration runner/orchestration path is still not evidenced

### 2.2 Re-extraction idempotence

Current evidence:
- implementation shape suggests idempotent behavior is intended
- registry/indexing discipline is in place
- offline real-PDF rerun evidence now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_reextract_sample.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_current.json`
  - current result: `11/11` processed rows produced identical digests on immediate rerun

Missing evidence:
- no documented idempotence run yet for the full background migration runner/orchestration path

Current judgment:
- partially met: the underlying v2.0 fact-building path is idempotent on the sampled real-PDF set, but the full background migration runner/orchestration path is still not evidenced

### 2.3 Migration progress observability

Current evidence:
- search-time partial-evaluation is observable
- `facts_version` is carried in candidate evaluation/audit paths
- AI search audit persistence now stores `Facts_Version` in the CSV audit log, so future prompt/search reviews can distinguish v1.1 vs v2.0 candidates from stored audit rows
- aggregated audit export/report path now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/ai_search_facts_version_report.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_current.json`
- post-change sample audit proof now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/generate_facts_version_audit_sample.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_sample_current.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_sample_run/ai_search_audit.csv`
- real main-audit proof now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/run_real_facts_version_audit_search.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_current.json`
  - current main-audit counts now include explicit `2.0: 11` rows alongside historical `<missing>: 1644`
- CSV/dual-write/backend audit-storage coverage exists in:
  - `tests/test_backend_event_log_flow.py`
  - `tests/test_dual_write_repo.py`
  - `tests/test_csv_candidate_event_repo.py`

Missing evidence:
- the historical pre-change corpus still dominates the audit log as `<missing>`
- the current real main-audit sample only demonstrates explicit `2.0` rows; it does not yet show mixed `1.1` and `2.0` counts from the live corpus
- the real sample run skipped ingestion and external LLM calls so it could execute in the offline sandbox; it is valid for audit-shape evidence, but not for proving background re-extraction behavior

Current judgment:
- partially met: audit-shape support, export/report tooling, and a real main-audit sample now exist, but the corpus still needs broader post-change search traffic and background re-extraction evidence

## 3. Overall Migration Readiness Judgment

Current overall judgment:
- the dual-version safety controls are largely implemented and well tested
- the remaining migration-readiness gap is operational evidence, not the synchronous control path itself

The strongest remaining migration evidence tasks are:
1. evidence the full background migration runner/orchestration path in a network-enabled environment
2. rerun that same migration path for idempotence evidence at the orchestration level
3. continue capturing/reporting v1.1 vs v2.0 progress counts as real post-change search traffic accumulates

## 4. Recommended Next Step

Do not broaden parser behavior next.

Instead, produce a small migration evidence pack with:
- sample batch size
- success/failure counts
- rerun idempotence result
- current v1.1 vs v2.0 counts

Once that exists, the remaining open Phase 1 item will mostly be real prompt-corpus growth over time rather than missing implementation.
