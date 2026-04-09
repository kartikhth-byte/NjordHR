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

Missing evidence:
- no documented representative batch run showing background re-extraction of at least a sample set of resumes to v2.0

Current judgment:
- implementation appears present enough to support this, but the sign-off artifact is still missing

### 2.2 Re-extraction idempotence

Current evidence:
- implementation shape suggests idempotent behavior is intended
- registry/indexing discipline is in place

Missing evidence:
- no documented run showing the same resume set re-extracted twice with identical v2.0 outcome

Current judgment:
- still a sign-off evidence gap

### 2.3 Migration progress observability

Current evidence:
- search-time partial-evaluation is observable
- `facts_version` is carried in candidate evaluation/audit paths
- AI search audit persistence now stores `Facts_Version` in the CSV audit log, so future prompt/search reviews can distinguish v1.1 vs v2.0 candidates from stored audit rows
- aggregated audit export/report path now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/ai_search_facts_version_report.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_current.json`
- CSV/dual-write/backend audit-storage coverage exists in:
  - `tests/test_backend_event_log_flow.py`
  - `tests/test_dual_write_repo.py`
  - `tests/test_csv_candidate_event_repo.py`

Missing evidence:
- current exported report shows only `<missing>` rows because the existing audit corpus predates `Facts_Version` persistence
- no post-change search sample has been run yet to populate explicit `1.1` / `2.0` values in the stored audit rows

Current judgment:
- partially met: audit-shape support and export/report tooling now exist, but the stored corpus still needs post-change searches to populate explicit version values

## 3. Overall Migration Readiness Judgment

Current overall judgment:
- the dual-version safety controls are largely implemented and well tested
- the remaining migration-readiness gap is operational evidence, not the synchronous control path itself

The strongest remaining migration evidence tasks are:
1. run a representative background re-extraction sample and record the result
2. rerun the same sample and record idempotence
3. capture/report v1.1 vs v2.0 progress counts in an operator-visible form

## 4. Recommended Next Step

Do not broaden parser behavior next.

Instead, produce a small migration evidence pack with:
- sample batch size
- success/failure counts
- rerun idempotence result
- current v1.1 vs v2.0 counts

Once that exists, the remaining open Phase 1 item will mostly be real prompt-corpus growth over time rather than missing implementation.
