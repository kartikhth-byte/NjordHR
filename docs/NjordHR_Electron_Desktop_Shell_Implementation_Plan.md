# NjordHR Electron Desktop Shell Implementation Plan

## 1. Purpose
Translate the Electron shell spec into an executable implementation plan for NjordHR.

This plan is intentionally scoped to:
- replace browser-launch packaging with an Electron desktop window
- preserve the existing Python backend and local agent
- keep current NjordHR features working during migration
- support both macOS and Windows

This is a packaging/runtime workstream, not a UI redesign workstream.

## 2. Guiding Principles
- Keep backend and agent logic unchanged unless shell integration requires a narrow change.
- Treat Electron as a desktop wrapper around the current app, not a rewrite.
- Preserve existing app behavior first, then improve UX.
- Keep macOS and Windows behavior aligned.
- Use staged rollout with explicit rollback.

## 2.1 Locked Before Implementation
- Use Electron.
- Use the existing bundled Python runtime model for first delivery.
- Electron owns backend and agent port selection.
- Backend readiness uses `GET /runtime/ready`.
- Agent startup is asynchronous and must not block main window opening.
- App quit stops app-owned backend and agent processes.
- Diagnostics move earlier than packaging rollout.
- Playwright is the Electron test framework.

## 2.2 Resolved Pre-E0 Decisions
These are now fixed inputs to implementation:

1. **Readiness and identity endpoint**
   - Electron uses `GET /runtime/ready`
   - endpoint is unauthenticated and localhost-only in purpose
   - endpoint returns runtime identity required for stale-process comparison

2. **Port collision strategy**
   - backend default: `5050`
   - agent default: `5051`
   - Electron probes upward within bounded ranges:
     - backend: `5050-5150`
     - agent: `5051-5151`
   - if no free port exists, startup fails with explicit collision reason

3. **Windows Python model**
   - Electron launches bundled `python.exe` from inside packaged app resources
   - Windows delivery does not rely on system Python

## 3. Recommended Delivery Sequence

### Phase E0: Shell Foundation
Goal:
- get NjordHR opening inside an Electron window on both macOS and Windows

Deliverables:
- Electron app scaffold
- main process
- preload bridge
- BrowserWindow loading local backend URL
- backend launcher integration
- basic app icons and metadata
- backend support for `GET /runtime/ready`
- backend port handshake defined and implemented
- bundled Python runtime launch path implemented
- frontend renderer audit under Electron security defaults

Acceptance:
- Electron app launches
- backend starts or is reused correctly
- main window loads the current app
- no default browser is required
- renderer audit completed under `contextIsolation=true` and `nodeIntegration=false`

### Phase E1: Runtime Lifecycle
Goal:
- make backend and agent lifecycle predictable and cross-platform

Deliverables:
- backend process manager in Electron
- optional agent process manager in Electron
- identity/reuse logic migrated from shell scripts into Electron runtime manager
- consistent runtime directories
- stop/restart behavior
- explicit app-quit shutdown behavior
- stale-process detection rules implemented
- current launcher responsibility breakdown implemented:
  - config normalization
  - unsafe path rewrite rules
  - env injection
  - runtime path resolution
  - bundled Python resolution
  - backend/agent identity comparison

Acceptance:
- stale backend is not reused
- correct backend is reused when appropriate
- local agent starts in background
- app window opens once backend is ready
- app-owned backend and agent stop on app quit

### Phase E2: Startup, Failure UX, and Diagnostics
Goal:
- replace ad hoc launcher UX with a proper desktop startup flow

Deliverables:
- native splash/startup window
- startup state transitions
- failure window
- retry/open logs actions
- main window focus/restore logic
- diagnostics window or menu action
- open logs folder action
- version/build identity display
- runtime status display

Acceptance:
- no console windows in normal use
- launch feedback is visible
- failure feedback is actionable
- main app window becomes primary window on launch
- support can identify exact running build
- user can open logs without navigating hidden folders

### Phase E3: Packaging and Installers
Goal:
- make Electron the primary packaged app for macOS and Windows

Deliverables:
- Electron packaging config
- macOS app packaging integration
- Windows installer integration
- updated shortcut/install scripts
- runtime asset bundling
- signing-ready configuration
- updater-aware packaging layout

Acceptance:
- packaged app installs correctly on macOS and Windows
- installer puts correct artifacts in expected OS locations
- launch path is Electron-first, not browser-first
- packaging structure is compatible with later code signing and update feeds

### Phase E4: Hardening and Rollout
Goal:
- stabilize, test, and transition from legacy launcher model

Deliverables:
- smoke test suite for Electron builds
- documented rollback path
- phased switch from script launchers to Electron launchers
- Playwright-based Electron smoke coverage
- signing/notarization readiness checklist

Acceptance:
- Mac and Windows smoke passes
- legacy launchers retained only as fallback during transition
- rollback documented and tested

## 4. Proposed Repository Structure

Add a dedicated Electron workspace under the repo root:

```text
electron/
  package.json
  electron-builder.yml
  src/
    main/
      main.ts|js
      runtime-manager.ts|js
      process-manager.ts|js
      paths.ts|js
      diagnostics.ts|js
      windows.ts|js
    preload/
      preload.ts|js
    renderer/
      splash.html
      error.html
      splash.css
      error.css
```

Keep the current app files in place:
- `backend_server.py`
- `agent_server.py`
- `frontend.html`
- existing Python/runtime scripts

## 5. File-Level Work Plan

### 5.1 New Electron Files

#### `electron/package.json`
Responsibilities:
- Electron dependencies
- build scripts
- development scripts
- packaging commands
- Playwright Electron test dependencies

#### `electron/electron-builder.yml`
Responsibilities:
- macOS packaging config
- Windows packaging config
- icons
- artifact naming
- bundled resources
- updater-ready layout placeholders
- signing-ready placeholders

#### `electron/src/main/main.js`
Responsibilities:
- app entrypoint
- single-instance lock
- window creation
- app activation behavior
- startup routing
- explicit app-quit lifecycle for child processes

#### `electron/src/main/runtime-manager.js`
Responsibilities:
- resolve runtime paths
- choose ports
- determine config path
- surface runtime identity
- replicate current launcher config normalization rules
- resolve bundled Python runtime paths
- on Windows, resolve packaged `python.exe` from app resources

#### `electron/src/main/process-manager.js`
Responsibilities:
- start/reuse/restart backend
- start agent asynchronously
- handle health checks
- shutdown child processes
- implement stale-process detection rules
- own backend port handshake
- surface explicit port-collision failure reason
- use `/runtime/ready` as backend readiness and identity source

#### `electron/src/main/windows.js`
Responsibilities:
- main window config
- splash window config
- failure window config
- focus and restore behavior

#### `electron/src/main/diagnostics.js`
Responsibilities:
- log path discovery
- version/build info
- open logs folder
- expose runtime state to renderer

#### `electron/src/preload/preload.js`
Responsibilities:
- safe bridge for diagnostics and shell actions
- no direct Node exposure to arbitrary frontend code
- explicit compatibility bridge for any frontend shell-only actions

#### `electron/src/renderer/splash.html`
Responsibilities:
- minimal branded startup UI
- not a modal dialog
- short status text only

#### `electron/src/renderer/error.html`
Responsibilities:
- startup failure UI
- retry/open logs/copy diagnostics actions

### 5.2 Existing Files Likely to Change

#### `/Users/kartikraghavan/Tools/NjordHR/frontend.html`
Expected shell-related changes only:
- add desktop-shell-aware diagnostics hooks if needed
- keep existing in-app banner for local-agent initialization
- add version/build surfacing later
- audit renderer assumptions under `contextIsolation=true` and `nodeIntegration=false`

Do not:
- redesign the whole app in this phase

#### `/Users/kartikraghavan/Tools/NjordHR/backend_server.py`
Possible narrow changes:
- add and maintain `GET /runtime/ready` for Electron readiness + runtime identity
- keep `/config/runtime` as UI/runtime diagnostics endpoint
- expose build/version metadata if useful

#### `/Users/kartikraghavan/Tools/NjordHR/agent_server.py`
Possible narrow changes:
- no major change expected
- only if agent health/settings flow needs a shell-specific improvement

#### `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/windows/build_inno_installer.ps1`
Future changes:
- install Electron app instead of browser-launch wrapper

#### `/Users/kartikraghavan/Tools/NjordHR/scripts/packaging/macos/build_app_bundle.sh`
Future changes:
- package Electron shell artifacts as primary app bundle

## 6. Runtime Behavior Plan

### 6.1 Startup
1. Electron main starts.
2. Acquire single-instance lock.
3. Create splash window.
4. Resolve runtime dirs/config/env.
5. Choose backend and agent ports in Electron.
6. Probe for free runtime ports near the default backend/agent ports.
7. Start or reuse backend.
8. When backend health is confirmed via `GET /runtime/ready`:
   - create main app window
   - load `http://127.0.0.1:<port>` or `http://localhost:<port>`
   - show/focus main window
   - close splash
9. Start local agent in background if needed.
10. Configure agent in background if needed.
11. Surface agent-starting state in-app until ready.

### 6.2 If Backend Fails
1. Close splash.
2. Show error window.
3. Offer:
   - Retry
   - Open Logs
   - Copy Diagnostics
   - Close

### 6.3 If Agent Fails
- App still opens.
- In-app banner shows local agent unavailable.
- Setup/Settings surfaces continue to show diagnostic state.

### 6.4 Shutdown
- On app quit, Electron stops backend and agent processes that belong to the current runtime identity.
- Electron must not leave app-owned stale backend or agent processes bound to runtime ports after normal exit.

## 7. Cross-Platform Requirements by Phase

### macOS
- app bundle support
- dock activation
- `open -a` compatible installed app
- future notarization path preserved

### Windows
- Start Menu and desktop shortcut support
- no console window
- main window opens as foreground app as far as OS rules allow
- packaged installer support

## 8. Testing Plan

### 8.1 Automated
- Playwright-based Electron smoke tests
- backend launch/reuse tests where practical
- packaging sanity checks

### 8.1.1 E0 Renderer Audit
E0 must include a one-time renderer audit:
- launch the existing frontend inside Electron with:
  - `contextIsolation=true`
  - `nodeIntegration=false`
- inspect console/runtime errors
- document any required `preload.js` bridges or compatibility issues before broader shell work proceeds

### 8.2 Manual Smoke for Both macOS and Windows
- launch app from installed icon
- verify main app window opens
- login
- Download tab
- AI Search deterministic prompt
- Dashboard resume preview
- Setup runtime visibility
- close and relaunch

### 8.3 Failure Smoke
- backend unavailable
- port collision
- broken config
- local agent delayed startup

## 9. Rollout Plan

### Stage 1
- Electron prototype in repo
- developer-only launch path

### Stage 2
- side-by-side packaging available
- existing launcher remains fallback

### Stage 3
- Electron becomes default packaged app
- old browser-launch packaging deprecated

### Stage 4
- remove old launcher-centric UX once stable

## 10. Rollback Plan
- Keep current script-based launcher path until Electron packages pass smoke on both OSes.
- If Electron packaging fails:
  - continue shipping current Python-launch packaging
  - keep backend/runtime improvements independent of Electron

## 11. Open Questions
- whether auto-start should launch full Electron UI or background runtime only
- whether to bundle frontend CSS locally as part of the same stream or immediately after
- whether updater work is in-scope for initial Electron delivery

## 12. Pre-Implementation Checklist
These must be accepted before Electron code starts:

1. Python runtime model confirmed:
   - bundled runtime, not system Python
2. Backend handshake confirmed:
   - Electron selects port
   - backend gets port via env
   - readiness is `GET /runtime/ready`
3. Launcher migration scope accepted:
   - config normalization
   - unsafe path rewrite rules
   - runtime env injection
   - identity comparison
4. Diagnostics moved into E2
5. Shutdown behavior fixed as requirement:
   - app quit stops app-owned backend and agent
6. Test framework fixed:
   - Playwright for Electron
7. Signing path acknowledged:
   - macOS notarization and Windows signing are E3/E4 readiness dependencies
8. Windows packaged Python path defined concretely
9. Port collision fallback/error strategy accepted

## 13. Recommended First Build Slice
Implement this first:

1. `electron/` scaffold
2. Electron main window
3. start/reuse Python backend
4. load NjordHR into Electron window
5. open window once backend is ready
6. leave agent startup asynchronous
7. keep existing in-app local-agent banner

This first slice is the fastest path to proving the desktop-shell direction while minimizing regression risk.
