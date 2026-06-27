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
- `P1` Sentence-aware engine negation remains intentionally deferred:
  - current v1 suppresses straightforward pre-mention negation inside a bounded look-behind window
  - sentence-boundary-aware handling is still needed to avoid suppressing positive evidence in nearby later sentences
  - treat this as the first engine-layer correctness follow-up if field telemetry shows false suppression
- `P1` Decide and document methanol / ammonia broad-bucket fallback symmetry:
  - current deterministic layer supports the subtype families
  - broad bucket behavior for manufacturer-only evidence should be made explicit and then regression-tested
  - do not change bucket semantics piecemeal without a written decision
- `P1` `MAN B&W LMC` alias follow-up is closed:
  - `_engine_type_aliases` normalizes `MAN B&W LMC` to `man_b_w_mc`
  - regression coverage pins both prompt extraction and resume-text extraction
- `P1` Reopen CoC demonym normalization:
  - add demonyms such as `Iranian`, `Maldivian`, `Mauritian`, and `Argentinian`
  - keep this work isolated from unrelated search-prompt changes
- `P2` Harden settings input typing:
  - reject boolean payloads passed into `_agent_setting_int`
  - keep `0` as a valid round-tripping integer value
- `P2` Clean debug payload hygiene for logical-group aggregation:
  - `_combine_any_of_item_results` should not leak oversized internal detail into `actual_value`
  - keep recruiter-facing reason text and debug/audit payloads intentionally separate

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
  - follow-up still open:
    - prompt-side ship-type normalization is currently broader-bucket oriented and does not yet align to the full configured `ShipTypes.ship_type_options` catalog in `config.ini`
    - treat config-aligned ship-type prompt recognition as a separate narrow implementation unit rather than mixing it into unrelated parser work
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
