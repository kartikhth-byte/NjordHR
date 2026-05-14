# AI Search v3.4 Phase 1 Sign-Off Status

Date: 2026-04-09

Latest status refresh: 2026-05-14

Purpose:
- record what can be signed off now from the current Phase 1 implementation
- distinguish implementation sign-off from the remaining operational launch-gate items
- avoid overstating full Phase 1 completion before the remaining spec evidence thresholds are met

Primary references:
- `/Users/kartikraghavan/Tools/NjordHR/docs/candidate-intelligence-architecture-v3.4.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/prompt-corpus-and-feedback-spec-v0.3.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_V3_4_PHASE1_READINESS_CHECKLIST_2026-04-08.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_V3_4_PROMPT_CORPUS_EVIDENCE_2026-04-09.md`
- `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_V3_4_MIGRATION_READINESS_EVIDENCE_2026-04-09.md`

## 1. Sign-Off Decision

Current decision:
- **Implementation sign-off: approved**
- **Full Phase 1 launch-gate sign-off: not yet fully approved**

Interpretation:
- the substantive Phase 1 engineering build is now in place and stable enough to sign off as implemented
- the remaining blockers are not major parser or hard-filter implementation gaps
- the remaining blockers are evidence/rollout gates that require more real usage or a network-enabled migration run

## 2. What Is Signed Off Now

The following are considered signed off at the implementation level:

- deterministic hard-filter behavior for the active Phase 1 families currently in scope
- `FAIL` exclusion before LLM reasoning
- `UNKNOWN` routing into `Needs Review`
- `applied_constraints` activation contract
- candidate-level audit logging and search-session logging
- prompt bootstrap corpus and parser-evaluation tooling
- rank prompt normalization at current configured scope
- ship-type prompt recognition aligned to configured catalog
- experienced ship type UI catalog alignment
- current extraction-quality work for rank, COC, DOB/age, visa/passport, and conservative STCW handling
- May 12 validity-window and recent-contract vessel experience implementation unit:
  - passport validity window
  - US visa validity window
  - recent contract vessel experience
  - bootstrap prompt-coverage evidence attached in `/Users/kartikraghavan/Tools/NjordHR/docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_PROMPT_COVERAGE_EVIDENCE_2026-05-12.md`
- migration safety controls for synchronous re-extraction
- migration observability tooling for `Facts_Version`
- indexed retrieval chunking upgrade slice 1
- focused manual smoke flow completed on 2026-04-09 with passing results

## 3. What Is Not Yet Signed Off as Fully Closed

The following remain open as Phase 1 launch-gate items:

### 3.1 Real prompt-corpus thresholds
- Bootstrap prompt coverage is complete and usable for current sign-off review.
- Real stored-prompt volume is still below threshold for several active deterministic families.
- This is acceptable for a temporary bootstrap-backed sign-off posture, but not for calling the real prompt-corpus gate fully closed.
- For the May 12 validity-window and recent-contract family pack, bootstrap evidence is attached, but stored prompt counts remain below threshold:
  - `passport_validity`: `11`
  - `us_visa`: `12`
  - `recent_contract_vessel_experience`: `0`

### 3.1.1 May 12 Family Pack Closeout
- `passport_validity` bootstrap parser coverage: `20/20`
- `us_visa` validity-window bootstrap parser coverage: `20/20`
- `recent_contract_vessel_experience` bootstrap parser coverage: `19/20`
- one staged `cruise experience` recent-contract prompt remains a documented vocabulary miss, not a current parser-expansion trigger
- focused regression on 2026-05-13 passed:
  - `python3 -m pytest tests/test_ai_analyzer_hard_filter_rules.py tests/test_ai_analyzer_job_constraints.py tests/test_ai_analyzer_logistics.py tests/test_ai_analyzer_visa_filters.py`
  - result: `145 passed`

### 3.2 Full background migration runner evidence
- The underlying v2.0 extraction path is evidenced on real PDFs.
- Offline re-extraction/idempotence evidence exists.
- A guarded background migration runner now exists and has local plus one-file network-enabled evidence.
- Refreshed 2026-05-13 offline evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_reextract_sample_2026-05-13.json`
  - `11/11` processed rows produced `facts_version = 2.0`
  - `11/11` processed rows were idempotent on immediate rerun
- Guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/scripts/background_migration_runner.py`
  - local state mode pass 1: `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_pass1.json`
  - local state mode pass 2: `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_pass2.json`
  - `11/11` local rows produced `facts_version = 2.0`
- Network-enabled guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network_pass2.json`
  - `1/1` row produced `facts_version = 2.0` and was indexed through the configured embedding/vector-index path
- Broader evidence-only network-enabled guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network10_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_network10_pass2.json`
  - `10/10` rows produced `facts_version = 2.0` and were indexed through the configured embedding/vector-index path
  - normal ingest registry marking was intentionally disabled
- Controlled registry-marking guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass2.json`
  - `1/1` row produced `facts_version = 2.0`
  - `1/1` row was marked in the local ingest registry at `logs/registry.db`
  - vector-index upsert was intentionally disabled for this sample to isolate the registry write path
- Expanded controlled registry-marking guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass2.json`
  - `10/10` rows produced `facts_version = 2.0`
  - `10/10` rows were marked in the local ingest registry at `logs/registry.db`
  - vector-index upsert was intentionally disabled for this sample to isolate the registry write path
  - historical diagnostic: the run emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn
- Full local-corpus guarded runner evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_officer_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_engineer_pass1.json`
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_full_registry_2nd_officer_pass1.json`
  - `24/24` rows produced `facts_version = 2.0`
  - `24/24` rows were upserted through the configured embedding/vector-index path
  - `24/24` rows were marked in the local ingest registry at `logs/registry.db`
  - historical diagnostic: the runs emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn
- Ship-type warning follow-up check:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/background_migration_runner_2026-05-14_warning_check.json`
  - `1/1` row processed successfully without emitting the ship-type fallback warning

### 3.3 Full orchestration-level idempotence evidence
- The sampled extraction path is idempotent.
- The guarded runner local state path is idempotent for `11/11` comparable rows.
- The guarded runner network-enabled path is idempotent for `1/1` comparable rows.
- The broader guarded runner network-enabled path is idempotent for `10/10` comparable rows.
- The controlled registry-marking path is idempotent for `1/1` comparable row.
- The expanded controlled registry-marking path is idempotent for `10/10` comparable rows.
- The full local-corpus vector-upsert and registry-marking path is idempotent for `24/24` comparable rows.

### 3.4 Broader live version-progress evidence
- `Facts_Version` observability now exists.
- Real audit rows now include explicit `2.0` values.
- Broader mixed live-corpus version counts still depend on more post-change traffic over time.
- Refreshed 2026-05-13 audit-progress evidence:
  - before latest sample: `2.0: 2797`, `<missing>: 1644`
  - after latest sample: `2.0: 2808`, `<missing>: 1644`
  - latest sample scanned `11` `Chief_Officer` resumes for `having valid US visa`: `10` pass, `1` fail, `0` unknown
- Refreshed 2026-05-14 audit-progress evidence:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-14_after_registry_mark10.json`
  - latest counts remain `2.0: 2808`, `<missing>: 1644`
- Refreshed after full local-corpus registry-marking:
  - `/Users/kartikraghavan/Tools/NjordHR/AI_Search_Results/facts_version_audit_progress_2026-05-14_after_full_registry.json`
  - latest counts remain `2.0: 2808`, `<missing>: 1644`

## 4. Current Sign-Off Posture

Use this language for current status:

> Phase 1 implementation is signed off as substantially complete. The remaining open item is real prompt-corpus accumulation for newer prompt families.

Do **not** use this language yet:

> Phase 1 is fully complete.

That stronger statement should wait until:
- the temporary bootstrap prompt-corpus substitution is no longer the primary evidence for several active families, and
- more real prompts have accumulated for newer prompt families.

## 5. Immediate Next Steps

The next work should be operational, not parser expansion:

1. accumulate more real prompts through normal use, especially for `recent_contract_vessel_experience`
2. continue prompt-corpus review as real post-change search traffic accumulates

## 6. Summary Judgment

What can be completed now has been completed:
- implementation build work is signed off
- current smoke-tested runtime behavior is acceptable
- readiness/status docs now reflect the actual state

What still remains is not major engineering implementation. It is the final evidence pack required to move from:
- **implementation-complete**

to:
- **fully launch-gate complete under the v3.4 spec**
