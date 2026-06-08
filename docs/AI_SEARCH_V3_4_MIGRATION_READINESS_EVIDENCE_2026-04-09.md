# AI Search v3.4 Migration Readiness Evidence

Date: 2026-04-09

Latest status refresh: 2026-05-14

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
- a guarded sample migration runner now exists:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_migration_runner.py`
- offline real-PDF extraction-path sample now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_reextract_sample.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_current.json`
  - current result: `11/11` processed rows produced `facts_version = 2.0`
- refreshed offline real-PDF extraction-path sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_2026-05-13.json`
  - current result: `11/11` processed rows produced `facts_version = 2.0`
- guarded migration-runner sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_pass2.json`
  - local migration-state mode processed `11/11` rows to `facts_version = 2.0`
- network-enabled guarded migration-runner sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network_pass2.json`
  - network upsert mode processed and indexed `1/1` row to `facts_version = 2.0`
- broader evidence-only network-enabled guarded migration-runner sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network10_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network10_pass2.json`
  - network upsert mode processed and indexed `10/10` rows to `facts_version = 2.0`
  - normal ingest registry marking was intentionally disabled
- controlled local registry-marking sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass2.json`
  - registry mark mode processed `1/1` row to `facts_version = 2.0`
  - registry mark mode marked `1/1` row in `logs/registry.db`
  - vector-index upsert was intentionally disabled for this sample to isolate the registry write path
- expanded controlled local registry-marking sample exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass2.json`
  - registry mark mode processed `10/10` rows to `facts_version = 2.0`
  - registry mark mode marked `10/10` rows in `logs/registry.db`
  - vector-index upsert was intentionally disabled for this sample to isolate the registry write path
  - historical diagnostic: the run emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn
- full local-corpus registry-marking and vector-upsert run exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_officer_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_engineer_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_2nd_officer_pass1.json`
  - `Chief_Officer`: `11/11` processed, indexed, and registry-marked
  - `Chief_Engineer`: `8/8` processed, indexed, and registry-marked
  - `2nd_Officer`: `5/5` processed, indexed, and registry-marked
  - total: `24/24` processed, indexed, and registry-marked
  - all processed rows produced `facts_version = 2.0`
  - historical diagnostic: the runs emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn
- ship-type warning follow-up check exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_warning_check.json`
  - current result: `1/1` processed successfully without emitting the ship-type fallback warning

Missing evidence:
- no documented migration run beyond the currently available local `Verified_Resumes` corpus

Current judgment:
- complete for the currently available local corpus: the underlying v2.0 extraction path, guarded migration-runner path, vector-index upsert path, registry-marking path, and full local-corpus run are evidenced

### 2.2 Re-extraction idempotence

Current evidence:
- implementation shape suggests idempotent behavior is intended
- registry/indexing discipline is in place
- offline real-PDF rerun evidence now exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_reextract_sample.py`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_current.json`
  - current result: `11/11` processed rows produced identical digests on immediate rerun
- refreshed offline real-PDF rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_2026-05-13.json`
  - current result: `11/11` processed rows produced identical digests on immediate rerun
- guarded migration-runner rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_pass2.json`
  - current result: `11/11` comparable rows matched persisted digests
- network-enabled guarded migration-runner rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network_pass2.json`
  - current result: `1/1` comparable rows matched persisted digests after external embedding/vector-index upsert
- broader network-enabled guarded migration-runner rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network10_pass2.json`
  - current result: `10/10` comparable rows matched persisted digests after external embedding/vector-index upsert
- controlled registry-marking rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass2.json`
  - current result: `1/1` comparable rows matched persisted digests after local ingest-registry marking
- expanded controlled registry-marking rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass2.json`
  - current result: `10/10` comparable rows matched persisted digests after local ingest-registry marking
- full local-corpus rerun evidence exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_officer_pass2.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_engineer_pass2.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_2nd_officer_pass2.json`
  - current result: `24/24` comparable rows matched persisted digests after vector upsert and local ingest-registry marking

Missing evidence:
- no documented idempotence run beyond the currently available local `Verified_Resumes` corpus

Current judgment:
- complete for the currently available local corpus: local guarded-runner idempotence, network-enabled idempotence, registry-marking idempotence, and full local-corpus idempotence are evidenced

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
- refreshed audit progress proof exists via:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-13.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-13_after_sample.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-13_after_migration_runner.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-13_after_network10.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-14_after_registry_mark10.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-14_after_full_registry.json`
  - before the refreshed sample: `2.0: 2797`, `<missing>: 1644`
  - after the refreshed sample: `2.0: 2808`, `<missing>: 1644`
  - after the expanded registry-marking sample: `2.0: 2808`, `<missing>: 1644`
  - after the full local-corpus registry-marking run: `2.0: 2808`, `<missing>: 1644`
  - latest sample searched `Chief_Officer` with prompt `having valid US visa`, scanned `11`, passed `10`, failed `1`, unknown `0`
- CSV/dual-write/backend audit-storage coverage exists in:
  - `tests/test_backend_event_log_flow.py`
  - `tests/test_dual_write_repo.py`
  - `tests/test_csv_candidate_event_repo.py`

Missing evidence:
- the historical pre-change corpus still dominates the audit log as `<missing>`
- the historical pre-change corpus remains present as `<missing>` audit rows
- the current real main-audit sample demonstrates explicit `2.0` rows and growing post-change volume
- the guarded migration runner writes separate migration evidence artifacts rather than AI search audit rows
- the real sample run skipped ingestion and external LLM calls so it could execute in the offline sandbox; it is valid for audit-shape evidence, but not for proving background re-extraction behavior

Current judgment:
- materially improved: audit-shape support, export/report tooling, refreshed real main-audit samples, guarded migration-runner samples, controlled registry-marking samples, and a full local-corpus vector-upsert/registry-marking run now exist

## 3. Overall Migration Readiness Judgment

Current overall judgment:
- the dual-version safety controls are largely implemented and well tested
- the currently available local corpus has been migrated through the guarded vector-upsert and registry-marking path

The strongest remaining migration evidence tasks are:
1. continue capturing/reporting facts-version progress counts as real post-change search traffic accumulates

## 4. Recommended Next Step

Do not broaden parser behavior next.

The full local-corpus registry-marking and vector-upsert evidence pack now exists. The remaining open Phase 1 item is mostly real prompt-corpus growth over time rather than missing implementation.
