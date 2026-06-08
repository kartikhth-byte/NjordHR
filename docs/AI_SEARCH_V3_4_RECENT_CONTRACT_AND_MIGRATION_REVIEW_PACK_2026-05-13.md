# AI Search v3.4 Recent Contract And Migration Review Pack

Date: 2026-05-13

## Purpose

This review pack defines the commit-sized boundary for the Phase 1 AI-search work covering:

- passport validity-window prompt support
- US visa validity-window prompt support
- recent contract vessel-experience prompt support
- prompt-corpus coverage evidence for those families
- migration-readiness evidence for facts version `2.0`

It is intentionally a stabilization and review artifact. It does not approve full-corpus registry marking or broaden Phase 1 into ranking, indexed-facts-only retrieval, or later-phase behavior.

Update: a one-row controlled registry-marking sample was completed on 2026-05-13 after this review boundary was created. A 10-row controlled registry-marking sample was completed on 2026-05-14. Full local-corpus registry marking with vector upsert was completed on 2026-05-14 across the three local rank folders.

## Included Change Set

Core analyzer and tests:

- `ai_analyzer.py`
- `tests/test_ai_analyzer_hard_filter_rules.py`
- `tests/test_ai_analyzer_job_constraints.py`
- `tests/test_ai_analyzer_logistics.py`
- `tests/test_ai_analyzer_visa_filters.py`

Evidence and review tooling:

- `scripts/ai_analyzer_recent_contract_vessel_folder_diagnostic.py`
- `scripts/ai_analyzer_recent_contract_vessel_rollup.py`
- `scripts/background_migration_runner.py`
- `scripts/background_reextract_sample.py`
- `scripts/bootstrap_prompt_corpus_eval.py`
- `scripts/prompt_corpus_review_report.py`
- `scripts/run_real_facts_version_audit_search.py`

Evidence documents:

- `docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_BOOTSTRAP_PROMPT_CORPUS_2026-05-12.json`
- `docs/AI_SEARCH_VALIDITY_AND_RECENT_CONTRACT_PROMPT_COVERAGE_EVIDENCE_2026-05-12.md`
- `docs/AI_SEARCH_V3_4_MIGRATION_READINESS_EVIDENCE_2026-04-09.md`
- `docs/AI_SEARCH_V3_4_PHASE1_SIGNOFF_STATUS_2026-04-09.md`
- `docs/prompt-family-coverage-status-2026-05-08.md`

Generated evidence artifacts on disk:

- `AI_Search_Results/2nd_engineer_recent_contract_container_diagnostic_2026-05-12_pre_impl.json`
- `AI_Search_Results/2nd_engineer_recent_contract_container_diagnostic_2026-05-12_post_impl.json`
- `AI_Search_Results/ai_analyzer_recent_contract_vessel_rollup_2026-05-12.json`
- `AI_Search_Results/2nd_officer_recent_contract_container_review_2026-05-12.json`
- `AI_Search_Results/3rd_officer_recent_contract_container_review_2026-05-12.json`
- `AI_Search_Results/junior_4th_engineer_recent_contract_container_review_2026-05-12.json`
- `AI_Search_Results/master_recent_contract_container_review_2026-05-12.json`
- `AI_Search_Results/validity_and_recent_contract_bootstrap_prompt_corpus_eval_2026-05-12.json`
- `AI_Search_Results/prompt_corpus_review_2026-05-12.json`
- `AI_Search_Results/background_reextract_sample_2026-05-13.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_network_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_network_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_network10_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_network10_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_officer_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_officer_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_engineer_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_chief_engineer_pass2.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_2nd_officer_pass1.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_full_registry_2nd_officer_pass2.json`
- `AI_Search_Results/facts_version_audit_progress_2026-05-13_after_network10.json`
- `AI_Search_Results/facts_version_audit_progress_2026-05-14_after_registry_mark10.json`
- `AI_Search_Results/facts_version_audit_progress_2026-05-14_after_full_registry.json`
- `AI_Search_Results/background_migration_runner_2026-05-14_warning_check.json`

Some `AI_Search_Results` files may be ignored by git. Decide whether they should be force-added as durable evidence before staging.

## Not Included In This Unit

The current worktree contains unrelated modified and untracked files in areas such as:

- email intake and agent services
- Outlook auth and secret storage
- backend event-log flow
- Electron packaging/runtime files
- frontend HTML and vendored assets
- release package directories
- scraper and requirements changes
- unrelated docs/spec drafts

Those files should not be staged with this AI-search unit unless separately reviewed.

## Validation Completed

Focused analyzer regression suite:

```bash
python3 -m pytest tests/test_ai_analyzer_hard_filter_rules.py tests/test_ai_analyzer_job_constraints.py tests/test_ai_analyzer_logistics.py tests/test_ai_analyzer_visa_filters.py
```

Result: `145 passed`.

Script syntax check:

```bash
python3 -m py_compile scripts/background_migration_runner.py scripts/background_reextract_sample.py scripts/run_real_facts_version_audit_search.py scripts/bootstrap_prompt_corpus_eval.py scripts/prompt_corpus_review_report.py
```

Result: passed.

Prompt-family bootstrap coverage:

- `passport_validity`: 20 prompts, threshold ratio `1.0`
- `us_visa`: 20 prompts, threshold ratio `1.0`
- `recent_contract_vessel_experience`: 20 prompts, threshold ratio `0.95`
- staged mismatch count: `1`

Background migration evidence:

- local state migration: 11/11 rows processed to v2.0
- local idempotency rerun: 11/11 comparable rows matched
- network sample: 1/1 indexed, idempotent
- broader evidence-only network sample: 10/10 processed and indexed
- broader evidence-only idempotency rerun: 10/10 comparable rows matched
- registry rows marked: 0

Controlled registry-marking evidence:

- rank folder: `Verified_Resumes/Chief_Officer`
- sample size: 1
- output pass 1: `AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass1.json`
- output pass 2: `AI_Search_Results/background_migration_runner_2026-05-13_registry_mark_pass2.json`
- state file: `AI_Search_Results/background_migration_runner_state_2026-05-13_registry_mark.json`
- mode: `--mark-ingest-registry` enabled, `--upsert-index` disabled
- pass 1 result: 1/1 processed, 1/1 marked in the ingest registry, 0 failed
- pass 2 result: 1/1 processed, 1/1 marked in the ingest registry, 1/1 comparable digest matched, 0 failed
- registry DB checked: `logs/registry.db`
- marked file: `Verified_Resumes/Chief_Officer/Chief-Officer_Bulk-Carrier_180473_2025-09-05_11-47-44.pdf`

Expanded controlled registry-marking evidence:

- run date: 2026-05-14
- rank folder: `Verified_Resumes/Chief_Officer`
- sample size: 10
- output pass 1: `AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass1.json`
- output pass 2: `AI_Search_Results/background_migration_runner_2026-05-14_registry_mark10_pass2.json`
- state file: `AI_Search_Results/background_migration_runner_state_2026-05-14_registry_mark10.json`
- mode: `--mark-ingest-registry` enabled, `--upsert-index` disabled
- pass 1 result: 10/10 processed, 10/10 marked in the ingest registry, 0 failed
- pass 2 result: 10/10 processed, 10/10 marked in the ingest registry, 10/10 comparable digests matched, 0 failed
- all processed rows produced `facts_version = 2.0`
- registry DB checked: `logs/registry.db`
- marked file range: `Chief-Officer_Bulk-Carrier_180473_2025-09-05_11-47-44.pdf` through `Chief-Officer_Bulk-Carrier_62065_2025-09-05_11-47-54.pdf`
- historical diagnostic: this run emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn

Full local-corpus registry-marking and vector-upsert evidence:

- run date: 2026-05-14
- mode: `--upsert-index` enabled, `--mark-ingest-registry` enabled
- scope: all PDFs in the three local rank folders under `Verified_Resumes`
- `Chief_Officer`: 11/11 processed, 11/11 indexed, 11/11 registry-marked, 0 failed; rerun had 11/11 comparable digest matches
- `Chief_Engineer`: 8/8 processed, 8/8 indexed, 8/8 registry-marked, 0 failed; rerun had 8/8 comparable digest matches
- `2nd_Officer`: 5/5 processed, 5/5 indexed, 5/5 registry-marked, 0 failed; rerun had 5/5 comparable digest matches
- total: 24/24 processed, 24/24 indexed, 24/24 registry-marked, 0 failed; rerun had 24/24 comparable digest matches
- all processed rows produced `facts_version = 2.0`
- registry DB checked: `logs/registry.db`
- registry folder counts checked: `Chief_Officer = 11`, `Chief_Engineer = 8`, `2nd_Officer = 5`
- historical diagnostic: these runs emitted repeated ship-type fallback warnings even though runtime ship-type config existed; the warning path was later narrowed so no-match snippets no longer warn
- warning follow-up check: `AI_Search_Results/background_migration_runner_2026-05-14_warning_check.json` processed 1/1 row successfully without emitting the ship-type fallback warning

Latest facts-version audit after the broader evidence-only network sample:

- `2.0`: 2808
- `<missing>`: 1644

Latest facts-version audit after the 10-row registry-marking sample:

- artifact: `AI_Search_Results/facts_version_audit_progress_2026-05-14_after_registry_mark10.json`
- total audit rows: 4452
- `2.0`: 2808
- `<missing>`: 1644
- sessions with `2.0`: 72
- sessions with `<missing>`: 14

Latest facts-version audit after the full local-corpus registry-marking run:

- artifact: `AI_Search_Results/facts_version_audit_progress_2026-05-14_after_full_registry.json`
- total audit rows: 4452
- `2.0`: 2808
- `<missing>`: 1644
- sessions with `2.0`: 72
- sessions with `<missing>`: 14

## Residual Risks And Decisions

- The staged prompt miss for recent contract vessel experience should be kept visible. It should become a parser follow-up only if the same phrasing appears in real recruiter prompts or product explicitly wants that phrasing supported.
- Real stored prompt-corpus coverage is still thin for these new families, especially recent contract vessel experience. Continue using the prompt-review workflow as real searches accumulate.
- Registry marking is now evidenced on one-row, 10-row, and full local-corpus runs. Full local-corpus registry marking has completed for the available local `Verified_Resumes` folders.
- Full-corpus migration has not been run. Do not infer full migration readiness from the 10-row network evidence sample alone.
- Raw-text fallback and mixed-version safeguards must remain in place, per v3.4 Phase 1 discipline.

## Recommended Next Action

Use this review pack to stage only the AI-search validity/recent-contract/migration-evidence unit. After that boundary is clean, the next operational task should be prompt-corpus accumulation and review from real post-change searches.
