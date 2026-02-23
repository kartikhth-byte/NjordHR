# NjordHR Implementation Modules and Task Backlog

## 1. How to use this backlog
- Execute modules in order unless marked parallel.
- Each task has a stable ID for tracking in issues/PRs.
- Do not start a module until its entry criteria are met.

## 2. Module overview
- `M0` Program setup and guardrails
- `M1` Cloud foundation (Supabase + API scaffolding)
- `M2` Data layer migration (CSV/SQLite -> Supabase)
- `M3` Local Agent (scraping + local folder download)
- `M4` Frontend integration (cloud + local agent modes)
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
- `M0-T5` Define rollback playbook per module.

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
- `M1-T6` Add auth middleware and environment secret loading.
- `M1-T7` Add health and readiness endpoints.

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
- `M2-T2` Implement CSV-backed adapters (existing behavior).
- `M2-T3` Implement Supabase-backed adapters.
- `M2-T4` Wire feature-flagged dependency injection for repo selection.
- `M2-T5` Build one-time migration scripts:
  - `verified_resumes.csv -> candidate_events/candidates`
  - `feedback.db -> analysis_feedback`
  - `registry.db -> registry table`
- `M2-T6` Add dual-write mode (CSV + Supabase) with idempotency keys.
- `M2-T7` Build parity report script (CSV vs Supabase counts and spot-checks).
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
- `M3-T2` Move scraper endpoints from monolith into local agent API:
  - `session/start`, `session/verify-otp`, `jobs/download`, `session/disconnect`.
- `M3-T3` Add local settings store:
  - download folder
  - cloud API URL
  - device token
  - sync toggles.
- `M3-T4` Implement folder validation and writability checks.
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

---

## M4. Frontend Integration (Cloud + Local Agent Modes)
**Goal:** keep UX unified while routing to correct runtime.

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
- Lane B (Agent): `M3`
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

### 6.5 First 10 tasks to execute (strict order)
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
