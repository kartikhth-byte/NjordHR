# NjordHR Online Hybrid Architecture Spec

## 1. Purpose
Define the production architecture and implementation plan to run NjordHR online while preserving local resume download to user-selected folders.

This spec covers:
- Vercel-hosted web app
- Supabase database and storage
- Local scraping agent on user machines
- Production installer with auto-update and code signing
- Migration from CSV/SQLite/local-only runtime

## 2. Goals
- Keep scraper download local to user machine.
- Make dashboard/search/history shared and online.
- Replace CSV and local SQLite with Supabase-backed persistence.
- Support multi-user, multi-device, and future scaling.
- Provide secure install/update path for local agent.

## 3. Non-Goals
- Running Selenium scraper on Vercel serverless.
- Allowing cloud backend to write directly to end-user local folders.
- Full multi-tenant enterprise SSO in v1.

## Implementation Rules
- No behavior regression:
  - Local download-to-folder must remain functional during every migration phase.
- Strict boundary:
  - Scraping and filesystem writes are local-agent only.
  - Cloud services must never write directly to user local folders.
- Data access discipline:
  - New features must use repository/service abstractions, not direct CSV/SQLite access.
  - Dual-write period is mandatory before deprecating CSV/SQLite.
- Security baseline:
  - Secrets only via environment variables and secret managers.
  - Supabase RLS policies are required before production rollout.
- Idempotency:
  - Job creation, event logging, and sync writes must be idempotent with retry-safe keys.
- Compatibility and cutover:
  - Existing user-facing workflows remain available until replacement paths pass acceptance tests.
  - Each phase needs an explicit rollback path.
- Observability:
  - Structured logs and job state telemetry are required in cloud and local agent.
  - Failures must include actionable reason codes.
- Release gating:
  - A phase cannot progress unless tests pass, smoke checks pass, and migration checks are documented.
- Installer trust:
  - Production installers and updates must be signed.
  - Auto-update must verify signature and checksum before apply.
- Controlled rollout:
  - All major changes must be behind feature flags (`USE_SUPABASE_DB`, `USE_LOCAL_AGENT`, etc.) with staged enablement.

## 4. Current State (Code Reality)
- Local scraper session in memory:
  - `backend_server.py` uses global `scraper_session`.
- Local filesystem persistence:
  - `csv_manager.py` writes `Verified_Resumes/verified_resumes.csv`.
  - `scraper_engine.py` writes PDFs to `Default_Download_Folder`.
  - `logger_config.py` writes `logs/*.log`.
- Local SQLite:
  - `ai_analyzer.py` uses `registry.db` and `feedback.db`.
- Frontend hardcoded API host:
  - `frontend.html` has `API_BASE_URL = http://127.0.0.1:5000`.

## 5. Target Architecture

### 5.1 High-level split
- Frontend (Vercel):
  - Web UI and authenticated user experience.
- Cloud API (container runtime, not Vercel functions):
  - Business APIs, Supabase DB integration, orchestration.
- Supabase:
  - Postgres for event log/status/search metadata.
  - Storage for optional cloud copy of resumes and exported artifacts.
- Local Agent (user machine):
  - Selenium session, OTP handling, local PDF downloads, local folder access.
  - Pushes events/progress/metadata to cloud API.

### 5.2 Why this split
- Local folder write requires local process permissions.
- Selenium browser automation needs persistent runtime and local browser tooling.
- Online dashboard/search requires shared state and centralized data.

## 6. Data Model (Supabase)

### 6.1 Core tables
- `users`
  - `id uuid pk`
  - `email text unique`
  - `created_at timestamptz`

- `devices`
  - `id uuid pk`
  - `user_id uuid fk users.id`
  - `device_name text`
  - `platform text`
  - `agent_version text`
  - `last_seen_at timestamptz`
  - `status text` (`active|revoked`)

- `agent_settings`
  - `id uuid pk`
  - `device_id uuid fk devices.id`
  - `download_folder text`
  - `updated_at timestamptz`

- `candidates`
  - `id uuid pk`
  - `candidate_external_id text` (from filename/candidate id)
  - `latest_filename text`
  - `rank_applied_for text`
  - `name text`
  - `present_rank text`
  - `email text`
  - `country text`
  - `mobile_no text`
  - `created_at timestamptz`
  - `updated_at timestamptz`

- `candidate_events`
  - `id uuid pk`
  - `candidate_id uuid fk candidates.id`
  - `event_type text` (`initial_verification|resume_updated|status_change|note_added`)
  - `status text`
  - `notes text`
  - `search_ship_type text`
  - `ai_search_prompt text`
  - `ai_match_reason text`
  - `filename text`
  - `resume_storage_path text nullable`
  - `created_by_user_id uuid`
  - `created_by_device_id uuid`
  - `created_at timestamptz`

- `analysis_feedback`
  - `id uuid pk`
  - `user_id uuid`
  - `filename text`
  - `query text`
  - `llm_decision text`
  - `llm_reason text`
  - `llm_confidence numeric`
  - `user_decision text`
  - `user_notes text`
  - `created_at timestamptz`

- `download_jobs`
  - `id uuid pk`
  - `user_id uuid`
  - `device_id uuid`
  - `rank text`
  - `ship_type text`
  - `force_redownload boolean`
  - `status text` (`queued|running|success|failed|cancelled`)
  - `message text`
  - `started_at timestamptz`
  - `ended_at timestamptz`

- `download_job_logs`
  - `id bigserial pk`
  - `job_id uuid fk download_jobs.id`
  - `level text`
  - `line text`
  - `created_at timestamptz`

### 6.2 Views
- `candidate_latest_state_v`
  - latest status/notes/event per candidate.
- `rank_counts_v`
  - latest candidate counts grouped by rank.

### 6.3 RLS
- Enforce user-level access:
  - user can only see rows linked to own `user_id` or own device.
- Service role used only by cloud API and provisioning pipelines.

## 7. Storage Strategy
- Local-first download remains mandatory.
- Optional cloud copy toggle:
  - Agent uploads PDF to Supabase Storage bucket `resumes`.
  - Store path in `candidate_events.resume_storage_path`.
- Export:
  - Build ZIP from cloud copies if present.
  - Fallback to metadata-only export when cloud files unavailable.

## 8. API Contracts

### 8.1 Cloud API
- `POST /api/device/register`
- `POST /api/device/heartbeat`
- `POST /api/jobs/download` (create job)
- `GET /api/jobs/:id/stream` (SSE progress from DB log tail)
- `POST /api/events/candidate`
- `GET /api/dashboard/data`
- `POST /api/dashboard/status`
- `POST /api/dashboard/notes`
- `GET /api/candidates/:id/history`
- `POST /api/export`

### 8.2 Local Agent API (localhost)
- `GET /health`
- `GET /settings`
- `PUT /settings/download-folder`
- `POST /session/start`
- `POST /session/verify-otp`
- `POST /jobs/download`
- `GET /jobs/:id/stream`
- `POST /session/disconnect`

## 9. Local Agent Specification

### 9.1 Runtime
- Python service initially.
- Long-running process with local queue and worker thread.
- Uses existing `scraper_engine.py` logic refactored into agent package.

### 9.2 Local config
- macOS: `~/Library/Application Support/NjordHR/agent.json`
- Windows: `%APPDATA%/NjordHR/agent.json`
- Linux: `~/.config/njordhr/agent.json`

Fields:
- `device_id`
- `api_base_url`
- `download_folder`
- `auto_start`
- `cloud_sync_enabled`
- `cloud_upload_resumes`
- `log_level`

### 9.3 Security
- Agent receives device token from cloud registration.
- Local API requires short-lived session token from UI handshake.
- CORS locked to trusted origins.
- Sensitive values never stored in plaintext where avoidable.

## 10. Installer and Auto-Update (Production)

### 10.1 Packaging
- macOS: signed `.pkg` or `.dmg` with launch agent plist.
- Windows: signed `.msi` or NSIS installer + Windows service/scheduled task.

### 10.2 Install actions
- Install binary/runtime.
- Create config directory and default config.
- Register startup service.
- Start agent.
- Optional post-install connectivity test.

### 10.3 Auto-update
- Use release manifest:
  - `version`, `platform`, `sha256`, `signature`, `url`.
- Agent checks update channel periodically.
- Download to temp path, verify signature and hash, apply update, restart.
- Rollback to previous version on failed boot health check.

### 10.4 Signing
- macOS:
  - Apple Developer ID signing + notarization.
- Windows:
  - Authenticode code-sign certificate.
- Reject unsigned or mismatched update payloads.

## 11. Frontend Changes Required
- Move from hardcoded localhost URL to env-based API routing.
- Add Settings screen:
  - Show connected agent status.
  - Download folder selector (for local agent mode).
  - Cloud sync toggles.
- Add connection mode indicator:
  - `Cloud only`, `Cloud + Local Agent`.
- Keep existing workflow UI (Download/Search/Dashboard), but route actions:
  - download/session endpoints -> local agent
  - dashboard/search/history/export -> cloud API

## 12. Backend Changes Required (Current File Mapping)

### 12.1 Replace / refactor in `backend_server.py`
- Remove dependency on global `scraper_session` for cloud deployment.
- Split endpoints:
  - Cloud data endpoints remain.
  - Local scraper endpoints move to agent service.
- Replace local file serving `/get_resume` with signed URL issuance.
- Replace local logs with DB-backed job logs.

### 12.2 Replace `csv_manager.py`
- Introduce `repositories/candidate_event_repo.py`.
- Implement Supabase-backed methods equivalent to:
  - `log_event`
  - `get_latest_status_per_candidate`
  - `get_candidate_history`
  - `log_status_change`
  - `log_note_added`
  - `get_rank_counts`

### 12.3 Update `ai_analyzer.py`
- Replace SQLite stores:
  - `registry.db` -> Supabase tables for file index state.
  - `feedback.db` -> `analysis_feedback`.
- Keep vector search provider as-is initially if functional.

### 12.4 Keep and move `scraper_engine.py`
- Keep logic mostly intact.
- Relocate into agent runtime package.
- Add adapter for cloud progress/event posting.

## 13. Migration Plan

### Phase 0: Foundation
- Add repository abstraction layer while keeping CSV path active.
- Add feature flags:
  - `USE_SUPABASE_DB`
  - `USE_LOCAL_AGENT`

### Phase 1: Supabase dual-write
- Write events to CSV and Supabase simultaneously.
- Read paths still from CSV for safety.
- Validate parity reports.

### Phase 2: Supabase read switch
- Dashboard/history/status/notes/export read from Supabase.
- CSV retained as fallback/backup.

### Phase 3: Agent extraction
- Move scraper endpoints to local agent service.
- Cloud creates/monitors jobs and receives logs/events.

### Phase 4: Installer + updater
- Deliver signed installers and update channel.
- Add staged rollout and telemetry.

### Phase 5: Decommission local CSV/SQLite
- Remove CSV write path after verification window.
- Archive legacy migration tooling.

## 14. Testing and Validation
- Unit:
  - repository methods, auth checks, folder validation.
- Integration:
  - local agent + cloud API handshake.
  - download job stream end-to-end.
- E2E:
  - OTP flow, download to chosen folder, dashboard updates, export.
- Security:
  - token misuse tests, RLS tests, signed update verification.
- Reliability:
  - network drop, agent restart, reconnect, retry idempotency.

## 15. Operational Requirements
- Observability:
  - structured logs in cloud and agent.
  - job metrics: success rate, avg duration, failure reasons.
- Backups:
  - Supabase automated backups and restore drill.
- Incident controls:
  - revoke device token.
  - disable problematic release via update channel.

## 16. Risks and Mitigations
- Risk: Agent-cloud connectivity instability.
  - Mitigation: durable local queue + retry with idempotency keys.
- Risk: User confusion on local vs cloud mode.
  - Mitigation: explicit mode indicator and guided onboarding.
- Risk: File access permission failures.
  - Mitigation: folder validation and startup diagnostics.
- Risk: Selenium/chrome dependency drift.
  - Mitigation: pinned compatible versions and health checks.

## 17. Deliverables
- `docs/NjordHR_Online_Hybrid_Architecture_Spec.md` (this file)
- Supabase schema SQL migration scripts
- Cloud API service refactor
- Local agent service
- Installer packages (macOS and Windows)
- Auto-update service manifest/signing pipeline

## 18. Acceptance Criteria
- User can set local download folder from UI settings.
- Resumes download locally on user machine in selected folder.
- Dashboard/search/history/status/notes work online with Supabase.
- Export works from online data model.
- Installer can install/start/upgrade signed local agent.
- System works across restart/reconnect without data loss.
