# NjordHR Electron Desktop Shell Spec

## 1. Purpose
Define a moderate-effort desktop packaging strategy for NjordHR using Electron so the app behaves like a real desktop application on both macOS and Windows, while preserving the current Python backend, local agent, and browser-based frontend.

This spec is intended to replace the current "launch local services, then open an external browser" model with a packaged desktop window that:
- owns the application window
- manages backend and agent lifecycle
- provides consistent startup and focus behavior
- works on both macOS and Windows

## 2. Decision
Use **Electron** as the desktop shell.

Why Electron is the right fit for NjordHR:
- The current app already has a local web frontend and Python backend.
- Electron is well-suited to "start local services, wait for readiness, then open a desktop window".
- Cross-platform window behavior is more controllable than default-browser launch behavior.
- Packaging and update workflows are mature for macOS and Windows.
- It minimizes rewrite risk compared with rebuilding the UI or moving backend logic into another runtime.

### 2.1 Locked Technical Decisions
- **Python runtime model:** first Electron delivery uses the existing bundled Python runtime model, not system Python and not PyInstaller-frozen binaries.
- **Port ownership:** Electron selects backend and agent ports before launch and passes them via environment variables.
- **Backend readiness handshake:** backend startup is considered successful only after `GET /runtime/ready` responds on the chosen port.
- **Agent startup policy:** the app window opens once backend is ready; the local agent continues starting in the background.
- **Shutdown policy:** on normal app quit, Electron stops backend and agent processes that belong to the current runtime identity.
- **Renderer security baseline:** `contextIsolation=true` and `nodeIntegration=false`; privileged operations must go through `preload.js`.
- **Desktop test framework:** Playwright with Electron support is the default automation framework.

## 3. Goals
- Replace browser-launch UX with a packaged desktop window.
- Preserve all current product features and workflows.
- Keep the Python backend and local agent as the core runtime.
- Ensure consistent launch behavior across macOS and Windows.
- Make startup, logs, and failure handling more professional.
- Reduce OS/browser-dependent differences in focus and window activation.

## 4. Non-Goals
- Rewriting the frontend into a new framework.
- Rewriting the backend in Node/Electron.
- Removing Python backend or local agent.
- Full UI redesign as part of the shell migration.
- Solving every existing frontend styling inconsistency in this workstream.

## 5. Current Problem Statement
The current packaged app starts local services and opens the app in the default browser. That causes:
- inconsistent foreground/focus behavior
- browser-specific HTTPS-first or localhost behavior issues
- weak startup UX
- OS-dependent experience differences
- harder control over lifecycle, shutdown, and updates

These are product issues, not just launcher bugs.

## 6. Target Product Experience

### 6.1 Launch
- User opens `NjordHR` from desktop, dock, applications, or Start Menu.
- A small branded startup window appears immediately.
- Electron starts or reuses the local Python backend.
- Electron opens the main app window as soon as backend is ready.
- The local agent may continue starting in the background.
- If the agent is still starting, the app shows a small in-app banner instead of blocking launch.

### 6.2 Main Window
- NjordHR opens in its own desktop window, not in the default browser.
- The app window becomes the active foreground window on launch whenever the OS allows it.
- The window can be reopened/focused from app icon, dock, taskbar, or menu.

### 6.3 Failure UX
- If backend startup fails, show a friendly startup error window.
- User can:
  - retry
  - open logs
  - copy diagnostics
  - close the app

## 7. Architecture

### 7.1 High-Level Architecture
- **Electron Main Process**
  - owns app lifecycle
  - creates and manages windows
  - starts/stops backend and agent
  - handles logs, updates, and diagnostics

- **Electron Renderer**
  - displays startup shell UI if needed
  - hosts the main NjordHR app in a `BrowserWindow`
  - loads local frontend served from Python backend

- **Python Backend**
  - continues serving current frontend and API routes
  - remains source of business logic

- **Python Local Agent**
  - continues handling scraping/session/local sync functions

### 7.2 Runtime Model
- Electron starts first.
- Electron determines runtime directories and port allocation.
- Electron starts backend if not already running or if stale.
- Electron opens main window when backend is healthy.
- Electron starts local agent in parallel or immediately after backend.
- Electron can shut down child processes on app exit, subject to configured policy.

### 7.3 Python Runtime Model
For the first Electron implementation, NjordHR keeps the current bundled-runtime model:
- Electron launches the bundled Python executable shipped with the app.
- Electron does not depend on a user-installed system Python.
- Electron does not require a PyInstaller-frozen backend/agent binary for the first delivery.
- On Windows, Electron launches a bundled `python.exe` staged inside packaged app resources.

This keeps the backend and agent close to the current packaged runtime behavior while avoiding a second major runtime migration.

### 7.4 Port Handshake
- Electron owns port selection for backend and agent.
- Electron passes ports and related URLs to Python through environment variables:
  - `NJORDHR_PORT`
  - `NJORDHR_AGENT_PORT`
  - `NJORDHR_SERVER_URL`
  - other existing runtime env keys as needed
- Backend startup success is defined as a successful response from:
  - `GET /runtime/ready`
- Agent startup success is defined as a successful response from:
  - `GET /health`
- Agent readiness must not block main window opening.

### 7.4.1 Port Collision Strategy
- Electron must probe for a free backend port and a free agent port before launch.
- Default runtime target ports are:
  - backend: `5050`
  - agent: `5051`
- Electron probes upward within bounded ranges:
  - backend: `5050-5150`
  - agent: `5051-5151`
- The current launcher behavior of choosing the first free port near the default runtime ports is the baseline model to preserve.
- If no acceptable port is available within the configured search range, Electron must surface a clear startup failure that distinguishes:
  - port collision / no free port found
  - backend process crash after spawn
- Any hardcoded `5000` assumptions in shell-facing code paths must be audited and replaced with runtime-selected URLs before packaged rollout.

### 7.4.2 Readiness Endpoint Requirement
Electron runtime management uses a dedicated local endpoint:
- `GET /runtime/ready`

Required behavior:
- no authentication required
- localhost-only use
- returns `200` when backend is ready
- includes runtime identity fields required for stale-process comparison

Required response shape:
- `success`
- `backend_ready`
- `process_identity`
  - `project_dir`
  - `config_path`
  - `runtime_dir`
- `ports`
  - `backend_port`
  - `agent_port` if known
- `version`
  - backend/app build identity if available

`/config/runtime` remains a UI/runtime diagnostics endpoint, but Electron startup logic uses `/runtime/ready`.

### 7.5 Current Launcher Logic That Must Move Into Electron
Electron runtime management must replicate the critical responsibilities currently handled by the launcher scripts:
- runtime directory discovery
- config path discovery
- default path normalization
- unsafe path rewrite rules
- bundled Python path resolution
- runtime/dependency validation
- environment variable injection
- backend identity comparison
- stale-process restart logic

This is a required migration scope item, not incidental glue code.

### 7.6 Windows Bundled Python Clarification
The current packaged/runtime model is not identical across macOS and Windows:
- macOS packaging already embeds a Python runtime in the packaged app flow
- Windows currently uses a venv-oriented startup flow tied to Python availability during runtime preparation

For Electron delivery, the Windows backend spawn path is fixed as:
- bundled Python runtime staged with the packaged app
- resolved by Electron from app resources at launch

This path must be used by `process-manager.js` instead of any system-Python or runtime-created venv assumption.

## 8. Feature Coverage
The Electron shell must preserve all current major features.

### 8.1 Auth
- login
- bootstrap-first-user flow
- logout
- cloud auth mode

### 8.1.1 Electron Fallback Auth/Runtime Clarification
For Electron startup, distinguish between:
- provisioned packaged runtime, where the shell must honor valid provisioned cloud/Supabase settings
- unprovisioned local/dev runtime, where the shell may apply a safe local fallback so the desktop shell can start without external credential provisioning

Accepted safe fallback behavior for unprovisioned local/dev startup:
- default `USE_LOCAL_AGENT=true` when not otherwise provisioned
- default `NJORDHR_AUTH_MODE=local` when not otherwise provisioned
- if `USE_SUPABASE_DB=true` is requested but `SUPABASE_URL` and a Supabase secret key are not both present, Electron must force:
  - `USE_SUPABASE_DB=false`
  - `USE_SUPABASE_READS=false`
  - `USE_DUAL_WRITE=false`

This fallback is a shell-startup safeguard only. It must not be interpreted as the desired packaged production default for provisioned desktop deployments.

### 8.2 Download Workflow
- website connection and OTP flow
- start download
- stream download logs
- download folder summary
- browse downloaded files
- open/preview downloaded resumes

### 8.3 AI Search
- rank folder selection
- applied ship type filter
- experienced ship type filter
- AI prompt search
- deterministic filtering
- `Needs Review`
- partial-evaluation and graceful-failure UI
- search result rendering

### 8.4 Dashboard
- verified/archive views
- filtering/search/sort/pagination
- candidate drawer
- status changes
- notes/history
- resume links and preview
- exports

### 8.5 Setup
- runtime checks
- installer/update guidance
- local agent health status

### 8.6 Settings
- runtime config
- operational config
- secrets/admin config
- user/password management
- settings password management

### 8.7 Logs
- usage logs
- runtime diagnostics

### 8.8 Local Runtime
- backend log files
- agent log files
- runtime env/config paths
- download folder and verified folder support

## 9. UI / Windowing Requirements

### 9.1 Main App Window
- Single primary application window.
- Minimum supported laptop size:
  - 1366x768
- Default size should be suitable for common Windows laptops and Mac laptops.
- Window title should be `NjordHR`.
- Window icon should be platform-appropriate.

### 9.2 Startup Window
- Lightweight splash/startup window, not a modal dialog.
- Branded, minimal, non-scrollable.
- Shows:
  - app name
  - startup state
  - short status text
- Should close automatically once main window is shown.
- Should not block the main window from opening.

### 9.3 Failure Window
- If backend cannot start within timeout:
  - show error UI
  - include `Open Logs`
  - include `Retry`
  - include `Close`

### 9.4 Focus Behavior
- Main app window should be shown and focused after launch.
- If app is already running:
  - focus existing window instead of opening duplicates

### 9.5 Renderer Compatibility
The current frontend continues to load from the backend URL, but Electron must preserve a secure renderer configuration:
- `contextIsolation=true`
- `nodeIntegration=false`

Before production rollout, the frontend must be audited for any shell-specific assumptions and all privileged desktop actions must be exposed through `preload.js`.

## 10. Cross-Platform Requirements

### 10.1 Shared
- Same window structure and UI order on macOS and Windows.
- Same major workflows and screens.
- Same feature flags and backend behavior.
- Same startup and error states.

### 10.2 macOS
- App bundle under `/Applications` when installed.
- Proper dock behavior.
- Standard app activation/focus behavior.
- Future support for notarization and signing.

### 10.3 Windows
- Standard installer and Start Menu/Desktop shortcut behavior.
- App should open as active foreground window as far as Windows allows.
- Avoid PowerShell/console windows in normal use.
- Future support for code signing.

## 11. Backend/Agent Lifecycle Rules

### 11.1 Backend
- Electron should start backend using known runtime directories and env vars.
- Backend readiness should be checked via `/runtime/ready`.
- If an existing backend belongs to another install/config identity, restart it.
- If backend belongs to this install/config, reuse it when appropriate.

### 11.2 Agent
- Local agent should not block main window opening.
- Agent startup may continue in background.
- Agent health should be reflected in the app UI.
- Agent settings sync can happen after main window opens.

### 11.3 Shutdown
- Default packaged-app policy:
  - app quits -> app-owned backend and agent quit
- On `before-quit` / `will-quit`, Electron stops backend and agent processes that belong to the current runtime identity.
- If background service mode is desired later, it must be an explicit later feature, not default behavior.

### 11.4 Stale Process Detection
Electron must define and enforce runtime identity for backend reuse.

At minimum, runtime identity includes:
- install/app path
- config path
- runtime directory
- expected backend port

A backend process is stale if:
- required identity fields are missing
- install path does not match
- config path does not match
- runtime directory does not match
- expected health endpoint does not respond

Stale backend/agent processes must be stopped and restarted.

### 11.5 Identity Source
The stale-process comparison mechanism is based on:
- `GET /runtime/ready`

Electron compares expected runtime identity against the `process_identity` fields returned by that endpoint before deciding to reuse or restart an existing backend.

## 12. Logging and Diagnostics
- Logs remain on disk in platform-specific runtime directories.
- Electron should know and expose:
  - backend stdout/stderr logs
  - agent stdout/stderr logs
  - Electron launcher logs
- Desktop shell should support:
  - open logs folder
  - copy runtime diagnostics
  - show version/build info

Recommended runtime surfaces:
- `Help > Diagnostics`
- `Open Logs Folder`
- version/build stamp in Settings or Setup

Diagnostics are required before broad packaged rollout, not after it.

## 13. Packaging

### 13.1 Electron Packaging
- Use Electron Builder or equivalent.
- Produce:
  - macOS app bundle / installer
  - Windows installer

### 13.2 Python Runtime
- Python backend and agent remain bundled as part of the app runtime.
- Electron launches the bundled Python executable and packaged project runtime.
- System Python is not a requirement for the first Electron delivery.

### 13.3 Asset Strategy
- Frontend HTML/CSS/JS should remain in repo but should no longer depend on external browser launch.
- Styling dependencies should be reviewed for local bundling reliability.

### 13.4 Signing and Notarization
Production rollout requires:
- macOS Developer ID signing and notarization
- Windows code-signing certificate

Packaging design should anticipate these requirements from the start so E3/E4 do not stall on signing architecture changes.

### 13.5 Update Architecture
Auto-update does not need to ship in the first Electron delivery, but packaging should be written so update channels and signed artifact distribution can be added later without structural rewrite.

## 14. Security
- Keep secrets out of Electron source.
- Continue using runtime env/config files and secure provisioning practices.
- Avoid exposing more local APIs than necessary.
- Keep app window loading only trusted local/backend URLs.
- Disable arbitrary external navigation from the Electron shell unless explicitly allowed.

## 15. Updates
- Electron shell should support later auto-update integration.
- This is not required for the first shell migration, but architecture should not block it.
- Shell and backend/app versioning should be visible together.

## 16. Migration Strategy

### Phase A: Shell Prototype
- Add Electron shell.
- Launch backend.
- Open NjordHR inside Electron window.
- No external browser launch in normal path.

### Phase B: Startup and Diagnostics
- Add startup window.
- Add failure window.
- Add diagnostics/log access.
- Add version/build identity.

### Phase C: Packaging Integration
- Replace current launcher-centric packaging path with Electron-first packaging.
- Keep Python runtime integration intact.
- Ensure macOS and Windows installer parity.

### Phase D: UX Hardening
- Standardize startup timing and readiness.
- Improve window focus/restore behavior.
- Normalize Mac/Windows consistency issues.

## 17. Acceptance Criteria

### 17.1 Core
- App launches into a desktop window on both macOS and Windows.
- No external browser is required in the normal flow.
- Backend starts reliably.
- App window opens once backend is ready.
- Local agent can continue starting after main window opens.

### 17.2 Product
- All existing tabs and workflows remain available:
  - Download
  - AI Search
  - Dashboard
  - Setup
  - Settings
  - Logs

### 17.3 UX
- No raw console windows in normal use.
- Startup feedback is visible.
- Failure feedback is visible and actionable.
- App window focus behavior is better than the current browser-launch model.

### 17.4 Cross-Platform
- Same information hierarchy and workflow order on macOS and Windows.
- No major OS-specific UI divergence.

## 18. Open Decisions
- Whether to move frontend styling fully local/bundled as part of the same project phase

## 19. Recommendation
Proceed with an Electron shell as a dedicated implementation stream.

Recommended first implementation target:
1. Electron main process
2. backend lifecycle management
3. open app in Electron window once backend is ready
4. local agent continues in background
5. in-app banner for agent initialization
6. diagnostics/log access

This is the best moderate-effort path to make NjordHR feel like a real desktop application on both macOS and Windows without rewriting the core application logic.
