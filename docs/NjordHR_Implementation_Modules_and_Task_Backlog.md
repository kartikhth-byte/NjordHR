# NjordHR Implementation Modules and Task Backlog

## 1. How to use this backlog
- Execute modules in order unless marked parallel.
- Each task has a stable ID for tracking in issues/PRs.
- Do not start a module until its entry criteria are met.

## 2. Module overview
- `M0` Program setup and guardrails
- `M1` Cloud foundation (Supabase + API scaffolding)
- `M2` Data layer migration (CSV/SQLite -> Supabase)
- `M3` Local Agent (scraping + local folder download) - complete
- `M4` Frontend Integration (Cloud + Local Agent Modes) - complete
- `M5` Installer + auto-update + signing
- `M6` Cutover, hardening, and deprecation

---

## M0. Program Setup and Guardrails
**Goal:** lock architecture boundaries and rollout safety before build.

### Tasks
- `M0-T1` Finalize implementation rules in architecture spec.
- `M0-T2` Define feature flags:
  - `USE_SUPABASE_DB`
  - `USE_LOCAL_AGENT`
  - `USE_CLOUD_EXPORT`
- `M0-T3` Create branch/PR template with required checks.
- `M0-T4` Create test matrix and smoke checklist baseline.
  - Deliverable: [NjordHR_M0_Test_Matrix_and_Smoke_Checklist.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_M0_Test_Matrix_and_Smoke_Checklist.md)
- `M0-T5` Define rollback playbook per module.
  - Deliverable: [NjordHR_Module_Rollback_Playbook.md](/Users/kartikraghavan/Tools/NjordHR/docs/NjordHR_Module_Rollback_Playbook.md)

### Exit criteria
- Rules published and agreed.
- Feature flags documented.
- Baseline tests and smoke checklist runnable.

---

## M1. Cloud Foundation (Supabase + API Scaffolding)
**Goal:** stand up cloud control plane without changing user behavior yet.

### Tasks
- `M1-T1` Create Supabase project and environments (`dev`, `staging`, `prod`).
- `M1-T2` Create DB schema migration scripts:
  - `users`, `devices`, `agent_settings`
  - `candidates`, `candidate_events`
  - `analysis_feedback`
  - `download_jobs`, `download_job_logs`
- `M1-T3` Add indexes for candidate/event/job query paths.
- `M1-T4` Implement RLS policies and service-role usage boundaries.
- `M1-T5` Create cloud API service skeleton (container runtime).
  - Deliverable: `cloud_api/` package with `create_app()`, `/health`, `/runtime/ready`, and auth guard scaffold.
- `M1-T6` Add auth middleware and environment secret loading.
  - Deliverable: `python3 -m cloud_api` entrypoint plus shared cloud runtime settings helper.
- `M1-T7` Add health and readiness endpoints.
  - Deliverable: cloud API health check runbook in `docs/cloud_api_health_check_runbook.md`.

### Exit criteria
- Supabase schema + RLS applied in dev.
- Cloud API deploys and passes health checks.

---

## M2. Data Layer Migration (CSV/SQLite -> Supabase)
**Goal:** migrate persistence safely with dual-write and parity checks.

### Tasks
- `M2-T1` Introduce repository interfaces:
  - `CandidateEventRepo`
  - `FeedbackRepo`
  - `RegistryRepo`
  - Deliverable: abstract contracts for candidate events, feedback, and file-registry stores plus analyzer implementations wired to them.
- `M2-T2` Implement CSV-backed adapters (existing behavior).
  - Deliverable: `CSVFileRegistry` and `CSVFeedbackStore` adapters in `repositories/` with analyzer wired to them.
- `M2-T3` Implement Supabase-backed adapters.
  - Deliverable: `SupabaseFileRegistry` and `SupabaseFeedbackStore` adapters in `repositories/` with analyzer wired to them.
- `M2-T4` Wire feature-flagged dependency injection for repo selection.
  - Deliverable: `build_ai_store_bundle(...)` plus analyzer/backend startup paths that receive injected feature flags and store bundles.
- `M2-T5` Build one-time migration scripts:
  - `verified_resumes.csv -> candidate_events/candidates`
  - `feedback.db -> analysis_feedback`
  - `registry.db -> registry table`
  - Deliverable: `scripts/migrate_local_state_to_supabase.py` orchestration plus reusable helper functions for each migration target.
- `M2-T6` Add dual-write mode (CSV + Supabase) with idempotency keys.
  - Deliverable: dual-write registry/feedback adapters with idempotency tracking, feature-flagged bundle selection, and coverage for repeated-write deduplication plus primary-read fallback.
- `M2-T7` Build parity report script (CSV vs Supabase counts and spot-checks).
  - Deliverable: `scripts/ai_store_parity_report.py` with count comparisons, sampled field checks, and missing-row detection for registry and feedback stores.
- `M2-T8` Switch read paths to Supabase under flag:
  - dashboard, history, status, notes, rank counts, export metadata.

### Exit criteria
- Dual-write stable for defined soak period.
- Parity report within agreed tolerance.
- Reads from Supabase pass tests and smoke checks.

---

## M3. Local Agent (Scraping + Local Folder Download)
**Goal:** move scraping/download into local machine process.

### Tasks
- `M3-T1` Create `agent/` service package with process entrypoint.
  - Deliverable: `agent/__main__.py` plus launcher wiring so the local agent can start via `python -m agent` and `scripts/run_agent.sh`.
- `M3-T2` Move scraper endpoints from monolith into local agent API:
  - `session/start`, `session/verify-otp`, `jobs/download`, `session/disconnect`.
  - Deliverable: canonical local-agent route stubs plus backend proxy coverage so session and download flows can run through the agent when `USE_LOCAL_AGENT=true`.
- `M3-T3` Add local settings store:
  - download folder
  - cloud API URL
  - device token
  - sync toggles.
  - Deliverable: normalized agent config store with persisted download folder, cloud API URL, device token, and sync flags plus tests for round-trip defaults and updates.
- `M3-T4` Implement folder validation and writability checks.
  - Deliverable: folder normalization plus actual write-probe validation in the agent filesystem helper and health/settings routes.
- `M3-T5` Add job queue and worker in agent runtime.
- `M3-T6` Add job progress and log streaming (`/jobs/:id/stream`).
- `M3-T7` Add cloud sync client:
  - candidate events
  - job states/logs
  - optional resume upload.
- `M3-T8` Add reconnect + retry with idempotency for unstable network.
- `M3-T9` Add local diagnostics endpoint and log bundle export.
- `M3-T10` Add cloud resume upload pipeline:
  - compute checksum
  - upload to object storage
  - write `resume_storage_path` and upload status.
- `M3-T11` Add local upload queue persistence and replay on restart.
- `M3-T12` Add offline mode support:
  - download allowed without cloud
  - deferred upload/sync once connectivity returns.

### Exit criteria
- Agent can download to user-selected folder reliably.
- Cloud receives job/event updates from agent.
- Cloud resume uploads are idempotent and recover after restart/network loss.

### Status
- Complete as of 2026-05-24.
- Remaining active work has moved to `M5` Installer + auto-update + signing.

---

## M4. Frontend Integration (Cloud + Local Agent Modes)
**Goal:** keep UX unified while routing to correct runtime.

**Current module under review:** `M4` Frontend Integration (Cloud + Local Agent Modes) - complete

### Status
- Complete as of 2026-05-24.
- `M4-T1` through `M4-T10` are now all implemented and committed.

### Tasks
- `M4-T1` Replace hardcoded API URL with env-based config.
- `M4-T2` Add mode detection and indicator:
  - `Cloud only`
  - `Cloud + Local Agent`.
- `M4-T3` Add Settings UI:
  - local agent connectivity status
  - download folder chooser
  - sync toggles.
- `M4-T4` Route download/session calls to local agent endpoints.
- `M4-T5` Route dashboard/search/history/export to cloud API.
- `M4-T6` Add offline/agent-disconnected UX states and recovery prompts.
- `M4-T7` Keep accessibility and workflow stepper behavior intact.
- `M4-T8` Resume open strategy in dashboard:
  - cloud signed URL primary
  - local-agent fallback when cloud object unavailable.
- `M4-T9` Add sync-state UI badges:
  - `Local only`
  - `Sync pending`
  - `Synced`
  - `Sync failed`.
- `M4-T10` Refresh the Settings UX and warning surfacing:
  - split backend save vs local-agent sync feedback more clearly
  - surface partial-success warnings with actionable detail
  - reduce full-form coupling so mailbox and folder updates are easier to observe and debug.

### Exit criteria
- User can choose local download folder in Settings.
- Download works locally; dashboard/search remain online.
- Resume open behavior is deterministic with explicit fallback and status visibility.

---

## M5. Installer + Auto-Update + Signing
**Goal:** production-grade deploy and update path for local agent.

### Tasks
- `M5-T1` Build cross-platform packaging pipeline:
  - macOS package
  - Windows installer.
- `M5-T2` Add install scripts and service registration:
  - macOS `launchd`
  - Windows service/task scheduler.
- `M5-T3` Add uninstall scripts and cleanup routines.
- `M5-T4` Implement update manifest service:
  - version
  - download URL
  - sha256
  - signature.
- `M5-T5` Implement update client in agent:
  - check
  - verify
  - apply
  - restart
  - rollback.
- `M5-T6` Configure signing:
  - Apple Developer signing + notarization
  - Windows Authenticode signing.
- `M5-T7` Run staged rollout process:
  - canary
  - percentage rollout
  - full rollout.

### Exit criteria
- Signed installer works on macOS and Windows.
- Signed auto-update works with rollback safety.

---

## M6. Cutover, Hardening, and Deprecation
**Goal:** complete transition and remove legacy persistence safely.

### Tasks
- `M6-T1` Enable Supabase read/write paths by default.
- `M6-T2` Deprecate CSV/SQLite write paths after freeze window.
- `M6-T3` Remove legacy migration-only code from runtime.
- `M6-T4` Harden observability:
  - dashboards for job failures, latency, sync lag.
- `M6-T5` Security audit:
  - RLS verification
  - token lifecycle
  - secret management.
- `M6-T6` DR drill:
  - Supabase restore test
  - agent reconnect/recovery test.
- `M6-T7` Final UAT and sign-off.
- `M6-T8` Resume storage canonical cutover:
  - require cloud object path for new candidate events (except explicit offline mode).
- `M6-T9` Historical resume backfill completion:
  - upload legacy local corpus
  - zero unresolved critical upload failures.
- `M6-T10` Retention and legal hold policy enforcement for resume blobs.

### Exit criteria
- Legacy CSV/SQLite no longer required for operation.
- Production SLO and security checks pass.
- Canonical resume storage is centralized (cloud or approved company object store), with local cache optional.

---

## 3. Parallel work lanes
- Lane A (Backend/Data): `M1`, `M2`
- Lane B (Agent): `M3` complete
- Lane C (Frontend): `M4`
- Lane D (DevOps/Release): `M5`

Suggested overlap:
- Start `M3` after `M1-T5` and `M1-T6`.
- Start `M4` after `M3-T2` API stubs exist.
- Start `M5` after `M3-T1` stable agent entrypoint exists.

---

## 4. Suggested issue labels
- `module:M0` ... `module:M6`
- `type:backend`, `type:frontend`, `type:agent`, `type:devops`, `type:security`
- `risk:high`, `risk:medium`, `risk:low`
- `flag:USE_SUPABASE_DB`, `flag:USE_LOCAL_AGENT`, `flag:USE_CLOUD_EXPORT`

---

## 5. Definition of Done (applies to every task)
- Code complete with tests.
- Feature flag behavior documented (if applicable).
- Logs/metrics added for new runtime behavior.
- Security checks done for auth/data paths.
- Manual smoke step recorded.
- Rollback step verified.

---

## 6. Priority Plan

### 6.1 Priority tiers
- `P0` Must do first. Blocks architecture viability.
- `P1` High. Needed for production-ready workflow.
- `P2` Medium. Hardening and rollout polish.

### 6.2 Current AI Search Phase 1 follow-up focus
- `P0` Pinecone false-empty rerun issue is resolved:
  - root cause was the namespace existence probe
  - the old zero-vector cosine query was unreliable
  - the fix uses Pinecone `list(namespace=..., limit=1)` with bounded retry and query fallback only when `list()` is unavailable
- `P1` Keep STCW extractor conservative for now:
  - do not add a broader `UNKNOWN -> PASS` heuristic yet
  - do not reduce the `Needs Review` bucket unless the same missed-positive pattern repeats across multiple folders
- `P1` Continue manual quality review of current AI Search output:
  - spot-check verified matches
  - spot-check fails
  - spot-check needs-review samples
- `P1` If further STCW work is needed, prioritize false `FAIL` / false `expired` corrections over promoting more `UNKNOWN` cases to `PASS`
- `P1` Treat the current rank / COC / DOB / visa extraction round as validated unless new repeated corpus evidence appears:
  - full diagnostic sweep has been run across the currently implemented extraction areas
  - rank extraction is materially improved across the validated folders
  - COC extraction is materially improved across the validated deck and engineer folders
  - DOB / age and visa / passport extraction appear broadly stable in the current corpus
  - remaining small residual buckets should not trigger broad parser expansion by default
- `P1` If future extraction tuning resumes:
  - start again with a folder-level diagnostic
  - save the artifact
  - patch only the repeated observed pattern
  - rerun the same-folder diagnostic before any broader validation

### 6.2.1 Current experience-filters / engine-layer post-merge follow-up focus
- `P1` Sentence-aware engine negation contrast handling is closed:
  - current v1 suppresses straightforward pre-mention negation inside a bounded look-behind window
  - sentence and clause boundaries reset the negation window before extraction
  - contrastive clauses such as `No ME experience, but has X-DF experience` now keep later positive evidence
  - negated lists such as `No ME or X-DF experience` remain suppressed
  - broader context classification remains deferred to a future extraction-hardening spec
- `P1` Methanol / ammonia broad-bucket fallback symmetry is closed:
  - `engine_experience_layers_v1.md` documents the symmetric conservative rule
  - dual-fuel-only evidence goes to Needs Review for fuel-specific bucket requests
  - generic manufacturer or family evidence fails for both methanol and ammonia
  - regression coverage pins both buckets against manufacturer-only and generic-family evidence
- `P1` `MAN B&W LMC` alias follow-up is closed:
  - `_engine_type_aliases` normalizes `MAN B&W LMC` to `man_b_w_mc`
  - regression coverage pins both prompt extraction and resume-text extraction
- `P1` CoC demonym normalization follow-up is closed:
  - demonyms such as `Iranian`, `Maldivian`, `Mauritian`, and `Argentinian` normalize on prompt and candidate-evidence paths
  - recruiter-facing CoC country rule messages use display labels while audit values remain canonical
  - broader CoC country alias migration is closed in `docs/specs/coc_country_alias_migration_v1.md`; analyzer and backend country aliases now read from `candidate_facts/aliases/coc_country.json`, and the inline maps plus temporary rollback switch are removed
- `P2` Settings integer input typing follow-up is closed:
  - `_agent_setting_int` rejects boolean payloads from local-agent settings instead of coercing them through `int(bool)`
  - integer `0` remains a valid returned poll interval
  - regression coverage pins both `True` / `False` rejection and `0` preservation in `tests/test_settings_precedence.py`
- `P2` Logical-group debug payload hygiene follow-up is closed:
  - `_combine_any_of_item_results` stores redacted child summaries in `actual_value` instead of full nested raw child results
  - recruiter-facing reason text remains separate from debug/audit payload shape
  - regression coverage pins PASS, FAIL, and UNKNOWN paths in `tests/test_ai_analyzer_hard_filter_rules.py`
  - consuming hard-filter redaction coverage is pinned in `tests/test_ai_analyzer_ship_type_filters.py`
- `P1` Shadow-LLM rescue promotion copy is clarified:
  - `LLM_Promotion_Stage` applies only to the five eval-cleared rescue families: `certificate_requirement`, `rank_match`, `stcw_basic`, `us_visa`, and `age_range`
  - remaining shadow-LLM normalization families are still pending separate evidence runs and promotion PRs
  - compound-prompt normalizer contract is locked in `docs/specs/shadow_llm_compound_prompt_normalizer_v1.md`; live promotion requires deterministic enforcement coverage across validator, canonicalizer, sanity checker, and dispatcher before a family enters `PROMOTED_FAMILIES`
- `P1` Availability filter rollout contract is locked in `docs/specs/availability_filter_v1.md`; deterministic extraction, picker semantics, and Needs Review behavior are implemented, the `availability` capability catalog row is promoted into live compound-normalizer dispatch, the 200-prompt shadow-normalizer evidence corpus is tracked in `docs/eval-evidence/availability-shadow-normalizer-corpus-2026-06-29.json`, and the first real Gemini audit artifact is tracked in `docs/eval-evidence/availability-shadow-normalizer-llm-evidence-2026-06-30.json`. The evidence gate passes in the artifact: schema-valid rate 0.99 (198/200), unsafe widening count 0, Class A LLM match rate 1.0, Class B correct rate 0.9625 with recall lift 0.9625, Class C safe-route rate 1.0, reviewed false-positive rate 0.0. Cross-family coverage is a no-op for this first promotion because `PROMOTED_FAMILIES` was empty at evaluation time.
- `P1` Tool-assisted compound-normalizer pilot is implemented for `availability` and specified in `docs/specs/shadow_llm_compound_prompt_normalizer_v1.md`: provider-scoped helper tools are allowed only for span, date, catalog-parameter, and conflict normalization; evaluator/search/database/filesystem/network/audit-write/telemetry-write/state-mutation tools remain prohibited. Fixture evidence is tracked in `docs/eval-evidence/availability-helper-tool-pilot-fixture-evidence-2026-06-30.json`; the first real helper-assisted run is tracked in `docs/eval-evidence/availability-normalizer-helper-tools-llm-evidence-2026-06-30.json` and failed because helper context caused display-value shortening plus one Class C invalid route; the prompt-fixed rerun is tracked in `docs/eval-evidence/availability-normalizer-helper-tools-prompt-fix-llm-evidence-2026-06-30.json` and passes with schema-valid rate 1.0, unsafe widening count 0, Class A 1.0, Class B 1.0, Class C 1.0, and reviewed false-positive rate 0.0. The JSON-only baseline remains tracked in `docs/eval-evidence/availability-normalizer-json-only-llm-evidence-2026-06-30.json`.
- `P1` Vessel-tonnage compound-normalizer rollout is promoted into live dispatch: `vessel_tonnage` is declared in `candidate_facts/aliases/filter_capability_catalog.json` with min/max/unit/year fields, validator coverage, provider prompt coverage, and a 200-prompt fixture corpus in `docs/eval-evidence/vessel-tonnage-shadow-normalizer-corpus-2026-06-30.json` with Class A=80, Class B=80, Class C=40 and required cross-family `availability` + `vessel_tonnage` prompts across Class A, Class B, and Class C. The first real Gemini JSON-only artifact lives in `docs/eval-evidence/vessel-tonnage-normalizer-json-only-llm-evidence-2026-06-30.json`; its promotion gate fails with schema-valid rate 1.0, unsafe widening count 4, Class A match rate 0.7625, Class B correct rate 0.7125 with recall lift 0.7125, Class C safe-route rate 0.725, and reviewed false-positive rate 0.0. The prompt-fix rerun lives in `docs/eval-evidence/vessel-tonnage-normalizer-json-only-prompt-fix-llm-evidence-2026-06-30.json` and passes with schema-valid rate 1.0, unsafe widening count 0, Class A match rate 1.0, Class B correct rate 0.7875 with recall lift 0.7875, Class C safe-route rate 1.0, and reviewed false-positive rate 0.0. `PROMOTED_FAMILIES` now contains both `availability` and `vessel_tonnage`. The vessel-tonnage helper-tool pilot is implemented as an opt-in provider path with hash-only audit and fixture evidence in `docs/eval-evidence/vessel-tonnage-helper-tool-pilot-fixture-evidence-2026-07-01.json`. The real helper A/B artifacts live in `docs/eval-evidence/vessel-tonnage-normalizer-json-only-helper-baseline-llm-evidence-2026-07-01.json` and `docs/eval-evidence/vessel-tonnage-normalizer-helper-tools-llm-evidence-2026-07-01.json`; helper adoption fails because the helper-assisted run regresses Class A from 0.975 to 0.825 via source-span narrowing, so `use_helper_tools=false` remains the default path.
- `P1` CoC country compound-normalizer rollout is promoted into live dispatch: `coc_country_match` is declared in `candidate_facts/aliases/filter_capability_catalog.json` using canonical country IDs from `candidate_facts/aliases/coc_country.json`, its provider prompt plus 200-prompt fixture corpus live in `docs/eval-evidence/coc-country-shadow-normalizer-corpus-2026-07-01.json`, and the first real Gemini JSON-only artifact lives in `docs/eval-evidence/coc-country-normalizer-json-only-llm-evidence-2026-07-01.json` with a failing Class A gate. The prompt-fix rerun lives in `docs/eval-evidence/coc-country-normalizer-json-only-prompt-fix-llm-evidence-2026-07-01.json` and passes with schema-valid rate 1.0, unsafe widening count 0, Class A match rate 1.0, Class B correct rate 1.0 with recall lift 0.8875, Class C safe-route rate 1.0, and reviewed false-positive rate 0.0. `PROMOTED_FAMILIES` now contains `{"availability", "coc_country_match", "vessel_tonnage"}`. Helper tools remain dormant for `coc_country_match`.
- `P1` Compound-normalizer dispatch-strategy evaluation is defined for the current N=3 live family set: `scripts/compound_dispatch_strategy_evidence.py` compares `sequential_per_family`, `parallel_per_family`, and `unified_multi_family` against the 25-case `docs/eval-evidence/compound-dispatch-strategy-n3-corpus-2026-07-01.json`. The harness is evidence-only; live dispatch remains unchanged until a future real-Gemini artifact proves schema-valid rate >= 0.99, unsafe widening count 0, constraint-family match rate 1.0, review-family match rate 1.0, no unified regression against parallel, and p50 total-elapsed reduction >= 30% or >= 200ms absolute, whichever threshold is smaller.
- `P1` Compound-normalizer dispatch-strategy real Gemini evidence is tracked in `docs/eval-evidence/compound-dispatch-strategy-n3-sequential-llm-evidence-2026-07-01.json`, `docs/eval-evidence/compound-dispatch-strategy-n3-parallel-llm-evidence-2026-07-01.json`, and `docs/eval-evidence/compound-dispatch-strategy-n3-unified-llm-evidence-2026-07-01.json`. Parallel clears the latency bar against sequential (p50 5253.459ms -> 2038.629ms, 61.2% reduction) but both per-family strategies fail the schema gate at 0.68 due availability cross-family parameter-shape errors. Unified fails the adoption bar with schema-valid rate 0.12, unsafe widening count 1, constraint-family match rate 0.92, and no meaningful p50 improvement over parallel (2038.629ms -> 2034.953ms). Live dispatch remains sequential; the next fix is availability cross-family schema/prompt repair before any dispatch switch.
- `P1` Compound-normalizer dispatch-strategy post-schema-fix evidence is tracked in `docs/eval-evidence/compound-dispatch-strategy-n3-sequential-post-schema-fix-llm-evidence-2026-07-01.json`, `docs/eval-evidence/compound-dispatch-strategy-n3-parallel-post-schema-fix-llm-evidence-2026-07-01.json`, and `docs/eval-evidence/compound-dispatch-strategy-n3-unified-post-schema-fix-llm-evidence-2026-07-01.json`. Sequential and parallel both clear the quality gates with schema-valid rate 1.0, unsafe widening count 0, constraint-family match rate 1.0, and review-family match rate 1.0. Parallel clears the latency bar against sequential (p50 5030.604ms -> 1981.997ms, 60.6% reduction). Unified remains rejected with schema-valid rate 0.12, unsafe widening count 1, constraint-family match rate 0.92, and only a 1.3% p50 reduction against parallel. A future runtime PR can switch live dispatch from sequential to parallel; unified dispatch remains rejected for the current N=3 family set.
- `P1` Compound-normalizer live dispatch now defaults to `parallel_per_family` for the current N=3 family set, backed by the post-schema-fix evidence artifacts. Operators can roll back without redeploy by setting `NJORDHR_LLM_NORMALIZER_DISPATCH_STRATEGY=sequential_per_family`; invalid strategy values also fall back to sequential. `NJORDHR_LLM_NORMALIZER_FAMILY_TIMEOUT_SECONDS` bounds each family provider call in parallel dispatch. Unified dispatch remains rejected and is not available as a runtime strategy.
- `P1` Unmatched-prompt semantic fallback is pending future design: after the compound normalizer emits no constraints, `needs_review`, or `unapplied` entries, a JSON-only classifier can route resume-relevant free-form prompts to semantic search, route non-resume prompts to graceful `out_of_scope` closure, or route ambiguous prompts to `needs_review`. The fallback must not invent hard-filter constraints.

### 6.2 P0 (Start immediately)
- `M0-T2` Define feature flags (`USE_SUPABASE_DB`, `USE_LOCAL_AGENT`, `USE_CLOUD_EXPORT`).
- `M1-T1` Create Supabase environments (`dev`, `staging`, `prod`).
- `M1-T2` Create core schema migrations.
- `M1-T4` Implement RLS baseline for core tables.
- `M1-T5` Cloud API service skeleton (container runtime).
- `M2-T1` Repository interfaces for data access.
- `M3-T1` Local agent package/entrypoint.
- `M3-T2` Move scraper/session/download endpoints into agent API stubs.
- `M4-T1` Frontend env-based API configuration (remove hardcoded localhost).

### 6.3 P1 (Immediately after P0)
- `M2-T3` Supabase repository implementations.
- `M2-T5` Migration scripts (CSV + SQLite -> Supabase).
- `M2-T6` Dual-write mode with idempotency keys.
- `M3-T3` Local agent settings store.
- `M3-T4` Download folder validation/writability checks.
- `M3-T5` Agent job queue + worker.
- `M3-T6` Agent job log/progress streaming.
- `M4-T2` Mode indicator (`Cloud only` vs `Cloud + Local Agent`).
- `M4-T3` Settings UI (agent status + download folder).
- `M4-T4` Route download/session calls to local agent.
- `M4-T5` Route dashboard/search/history/export to cloud API.

### 6.4 P2 (Production rollout)
- `M5-*` Installer, signing, and auto-update.
- `M6-*` Cutover, deprecation, DR drills, and full hardening.

### 6.5 First 10 tasks to execute (historical rollout order)
1. `M0-T2` Feature flags and defaults.
2. `M1-T1` Supabase project/environment setup.
3. `M1-T2` DB schema migrations.
4. `M1-T4` RLS and access policy tests.
5. `M1-T5` Cloud API skeleton deployment.
6. `M2-T1` Data repository interfaces.
7. `M3-T1` Local agent process skeleton.
8. `M3-T2` Agent API endpoint stubs for session/download.
9. `M4-T1` Frontend API env routing.
10. `M2-T3` Supabase repository implementation (first read path).

### 6.6 Two-week execution target
- Week 1 target:
  - Complete tasks 1-8 above.
- Week 2 target:
  - Complete tasks 9-10, plus `M2-T5` and `M3-T3`.

### 6.7 Suggested owners by lane
- Backend/Data owner:
  - `M1`, `M2`
- Agent owner:
  - `M3`
- Frontend owner:
  - `M4`
- Release/DevOps owner:
  - `M5`, `M6`

### 6.8 Newly noted pending UX tasks (not yet implemented)
- `M5-T7` Ensure Njord logo is consistently shown in the app header across supported builds/platforms.
- `M5-T8` Windows first-run bootstrap UX:
  - move install/dependency bootstrap to background (no visible terminal window),
  - add first-run progress UI with status text + progress indicator while runtime/dependencies are prepared.
- `M5-T9` Add password generator action in `User Password` page:
  - provide one-click strong password generation when creating/updating user credentials.
- `M5-T10` Add branded Windows application icon parity with macOS:
  - use orange background + navy anchor icon for Windows app/start-menu/taskbar assets.

---

## 7. Current Tactical Workstream: Deterministic AI Search Filters

**Reason:** AI Search produced incorrect results for age-range prompts because structured constraints were still effectively being interpreted by LLM reasoning instead of being enforced deterministically.

### 7.1 Immediate objective
- Build a deterministic hard-filter layer in AI Search before LLM reasoning.
- Start with age derived from DOB.
- Keep LLM usage for semantic explanation/ranking only after hard-filter pass.

### 7.2 Current status
- In progress in:
  - `/Users/kartikraghavan/Tools/NjordHR/ai_analyzer.py`
  - `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
- Implemented in source:
  - minimal `JobConstraints` extraction for age
  - minimal `CandidateFacts` extraction for DOB
  - evaluation-time age computation
  - hard-filter result states:
    - `PASS`
    - `FAIL`
    - `UNKNOWN`
  - AI Search summary counters:
    - scanned
    - passed hard filters
    - needs review
    - matched
  - `UNKNOWN` candidates rendered as `Needs Review`
- Packaged mac app validation completed successfully for the tested age-only flow.
- Root cause of the final mismatch was fixed in DOB parsing:
  - support for resume DOB format `DD-Mon-YYYY`
  - removal of unsafe fallback that could pick unrelated dates
- Structured-only age prompts now evaluate the full selected rank folder before LLM reasoning.

### 7.2.1 Current DOB parsing contract
- DOB is only parsed when it appears next to an explicit DOB label such as `Date of Birth`, `DOB`, or `D.O.B`.
- Supported unambiguous DOB formats:
  - `DD-Mon-YYYY` and equivalent month-name separator variants such as `DD Mon YYYY`, `DD/Mon/YYYY`
  - `Mon DD YYYY` / `Month DD YYYY`
  - ISO-style `YYYY-MM-DD`
- Ambiguous numeric formats must be treated as `UNKNOWN`, not guessed:
  - examples: `04/11/1989`, `03-02-1974`, `11.04.89`
- Unlabeled dates elsewhere in the resume must not be used as DOB fallbacks.

### 7.3 Next tasks in this workstream
- `AI-T1` Completed in current implementation:
  - regression coverage exists for age range prompts, DOB parsing edge cases, and the exact birthday boundary in age computation
- `AI-T2` Completed in current implementation:
  - deterministic `FAIL` / `UNKNOWN` candidates are kept out of LLM reasoning and this behavior is covered by the current test suite
- `AI-T3` Completed in current implementation:
  - `UNKNOWN` candidates are rendered in a separate `Needs Review` bucket
  - no inclusion toggle is currently implemented or required for the validated Phase 1 flow
- `AI-T4` Completed in current implementation:
  - deterministic ship-type extraction/evaluation is wired for both applied ship type and experienced ship type
  - backend, UI controls, and regression coverage are present
  - prompt-side ship-type recognition is aligned to the configured `ShipTypes.ship_type_options` catalog in `config.ini`
  - regression coverage pins every unique normalized configured ship-type label for both vessel-type prompt constraints and experienced-ship-type prompt constraints
  - current catalog count is 104 raw entries collapsing to 103 unique normalized labels; `Dredger` / `DREDGER` is the lone case-fold collision
- `AI-T5` Completed in current implementation:
  - deterministic/audit logging for hard-filter outcomes is emitted, persisted, and surfaced in the current flow
- `AI-T6` Retrieval chunking upgrade is partially completed in current implementation:
  - completed:
    - the main indexed chunker no longer uses only fixed whitespace-only token windows
    - paragraph/blank-line boundaries are preferred where feasible
    - short table-like multiline blocks are preserved more carefully
    - direct chunking regressions now exist
  - deferred / optional follow-up:
    - keyword-fallback pseudo-chunking can be improved later if fallback retrieval quality becomes a practical concern
  - keep any further retrieval-quality work out of the deterministic Phase 1 change set so retrieval changes do not get mixed with hard-filter correctness work

### 7.3.1 Extraction-quality status after diagnostics
- Completed validation work:
  - diagnostic-first tuning and validation has now been run for:
    - STCW
    - rank normalization
    - COC extraction
    - DOB / age extraction
    - visa / passport extraction
- Current quality judgment:
  - STCW remains intentionally conservative and still has the largest unresolved unknown bucket
  - rank extraction is in a materially better state for the current corpus family
  - COC extraction is in a materially better state for the current corpus family
  - remaining COC misses are now mostly incomplete-source or narrower folder-specific issues rather than one broad missing alias family
  - DOB / age and visa / passport extraction do not currently need broadening work
- Recommended next-step posture:
  - do not continue broad extractor expansion by default
  - use the saved diagnostic artifacts as the baseline for future regressions
  - only reopen extractor tuning when a new repeated pattern appears in diagnostics or manual review

### 7.4 Acceptance criteria
- Prompt `between 30 and 50 years old` excludes candidates older than 50 or younger than 30.
- Candidates with missing/ambiguous DOB are not shown as verified matches.
- UI clearly distinguishes deterministic pass/fail/unknown behavior from LLM confidence.
- Real resume DOB formats observed in the corpus are covered by regression tests.
