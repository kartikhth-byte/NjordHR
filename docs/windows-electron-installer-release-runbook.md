# Windows Electron Installer Build and Upload Runbook

This runbook is the operator checklist for creating the current Windows Electron installer, validating it, and uploading it to a GitHub Release.

Use this path for the Electron NSIS installer artifact:

```text
build\electron\NjordHR-Electron-<version>-win.exe
```

Do not confuse it with:

- `build\electron-stage\`: intermediate app and Python runtime payload staged before Electron Builder runs
- `scripts\packaging\windows\build_inno_installer.ps1`: older Inno Setup/browser-launch packaging path
- `build\windows\`: older Windows portable ZIP/Inno output path

## Security Note

The validation build flow below provisions Supabase defaults into the packaged app's `default_runtime.env`.

- Treat the generated installer as containing deployment credentials.
- Upload it only to a GitHub Release location appropriate for that credential model.
- Never paste `SUPABASE_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, or installed `default_runtime.env` output into public logs, screenshots, or support messages.

## What The Build Machine Needs

The finished installer bundles Python for end users. The Windows machine that creates the installer still needs build prerequisites:

1. Git.
2. Node.js and npm.
3. Python 3.11 visible to the Python launcher as `py -3.11`.
4. GitHub CLI `gh` for release upload.
5. Internet access for npm, pip, Electron Builder, and Supabase validation.

Validate prerequisites in PowerShell:

```powershell
git --version
node --version
npm.cmd --version
py -3.11 --version
gh --version
```

If `py -3.11 --version` fails, install Python 3.11 side by side with any newer Python version and reopen PowerShell. The build script intentionally uses Python 3.11 as the Windows runtime source.

## Checkout And Prepare The Repo

Clone or update the Windows checkout:

```powershell
cd $HOME
git clone https://github.com/kartikhth-byte/NjordHR.git
cd .\NjordHR
# Stay on the current branch by default; only checkout a specific branch if you
# are intentionally building a named spec/release branch.
git branch --show-current
git pull
```

Install Electron build dependencies:

```powershell
cd .\electron
npm.cmd install
```

Confirm Electron Builder was installed:

```powershell
Test-Path .\node_modules\.bin\electron-builder.cmd
```

Expected:

```text
True
```

Use `npm.cmd` in PowerShell. On Windows PowerShell systems with restrictive execution policy, plain `npm` may resolve to unsigned `npm.ps1` and fail before the build starts.

## Provision Supabase Defaults For The Installer

Set the provisioning variables in the same PowerShell session that will run the build:

```powershell
$env:NJORDHR_DEFAULT_SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
$env:NJORDHR_DEFAULT_SUPABASE_SECRET_KEY = Read-Host "Enter Supabase Secret Key"

$env:NJORDHR_DEFAULT_AUTH_MODE = "cloud"
$env:NJORDHR_DEFAULT_USE_SUPABASE_DB = "true"
$env:NJORDHR_DEFAULT_USE_SUPABASE_READS = "true"
$env:NJORDHR_DEFAULT_USE_DUAL_WRITE = "false"
$env:NJORDHR_DEFAULT_USE_LOCAL_AGENT = "true"
```

Only set the legacy fallback key when the deployment still needs it:

```powershell
$env:NJORDHR_DEFAULT_SUPABASE_SERVICE_ROLE_KEY = Read-Host "Enter Supabase Service Role Key if needed"
```

Validate the variables before building without printing the secret:

```powershell
$env:NJORDHR_DEFAULT_SUPABASE_URL
[bool]$env:NJORDHR_DEFAULT_SUPABASE_SECRET_KEY
$env:NJORDHR_DEFAULT_AUTH_MODE
$env:NJORDHR_DEFAULT_USE_SUPABASE_DB
$env:NJORDHR_DEFAULT_USE_SUPABASE_READS
```

Expected:

- the Supabase URL is printed
- the secret presence check prints `True`
- auth mode prints `cloud`
- the Supabase flags print `true`

PowerShell `$env:` values are session-scoped. Re-enter them after opening a new shell.

## Build The Electron Installer

From `C:\Users\<user>\NjordHR\electron`:

```powershell
npm.cmd run dist:win
```

The command performs:

1. icon generation/reuse
2. app payload staging under `build\electron-stage\app`
3. bundled Windows Python runtime staging under `build\electron-stage\python`
4. dependency installation into the staged Python runtime
5. Electron Builder NSIS installer creation

Do not stop after the line:

```text
[NjordHR] Electron runtime staged at ...
```

That line only means staging succeeded. A completed installer build must continue through Electron Builder and create `build\electron`.

## Validate The Staged Payload

Before installing, verify the files that have caused packaged Windows failures before:

```powershell
Test-Path ..\build\electron-stage\app\runtime_env.py
Test-Path ..\build\electron-stage\app\rank_folders.py
Test-Path ..\build\electron-stage\app\web_vendor\react.development.js
Test-Path ..\build\electron-stage\app\web_vendor\react-dom.development.js
Test-Path ..\build\electron-stage\app\web_vendor\babel.min.js
```

All must return `True`.

Validate generated runtime defaults:

```powershell
Get-Content ..\build\electron-stage\app\default_runtime.env
```

For a cloud validation build, confirm:

```text
USE_SUPABASE_DB=true
USE_SUPABASE_READS=true
USE_LOCAL_AGENT=true
NJORDHR_AUTH_MODE=cloud
SUPABASE_URL=https://...
SUPABASE_SECRET_KEY=...
```

If these values show local mode with blank Supabase fields, the `$env:NJORDHR_DEFAULT_*` variables were not visible to the build process. Re-set them and rebuild.

## Locate The Installer

From the `electron` directory:

```powershell
$installer = Get-ChildItem ..\build\electron\NjordHR-Electron-*-win.exe |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$installer.FullName
```

If `build\electron` does not exist, the build did not reach or complete Electron Builder.

## Clean Install And Smoke Test

Close any running NjordHR windows first. Then remove the old validation install and runtime state:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\NjordHR" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:APPDATA\NjordHR" -ErrorAction SilentlyContinue
```

Install the rebuilt `.exe`:

```powershell
Start-Process $installer.FullName -Wait
```

Launch:

```powershell
Start-Process "$env:LOCALAPPDATA\Programs\NjordHR\NjordHR.exe"
```

Validate installed payload:

```powershell
Test-Path "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\runtime_env.py"
Test-Path "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\rank_folders.py"
Test-Path "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\web_vendor\react.development.js"
Test-Path "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\web_vendor\react-dom.development.js"
Test-Path "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\web_vendor\babel.min.js"
```

Validate frontend vendor serving once the app is running:

```powershell
Invoke-WebRequest http://127.0.0.1:5050/ui_vendor/react.development.js -UseBasicParsing
```

This must not return `Vendor asset not found.` A missing `web_vendor` payload can start the backend but leave the Electron app window blank because React/Babel never load.

Validate installed runtime defaults only in a private shell:

```powershell
Get-Content "$env:LOCALAPPDATA\Programs\NjordHR\resources\app\default_runtime.env"
```

If the login/bootstrap UI reports Supabase DNS or cloud availability errors, confirm the Supabase project is active and the project URL resolves from the Windows machine before blaming the installer.

## Upload To GitHub Release

Authenticate GitHub CLI once:

```powershell
gh auth login
```

From the repo root:

```powershell
cd C:\Users\<user>\NjordHR

$installer = Get-ChildItem .\build\electron\NjordHR-Electron-*-win.exe |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$tag = "windows-validation-YYYY-MM-DD"
```

Create a new release and attach the installer. The one-line form avoids PowerShell backtick continuation mistakes:

```powershell
gh release create $tag $installer.FullName --target deterministic-engine-gaps-v1 --title "NjordHR Windows Validation Build YYYY-MM-DD" --notes "Windows validation build from deterministic-engine-gaps-v1."
```

If the release already exists, upload or replace the installer asset:

```powershell
gh release upload $tag $installer.FullName --clobber
```

Validate the upload:

```powershell
gh release view $tag
gh release view $tag --json assets --jq ".assets[].name"
gh release view $tag --json assets --jq ".assets[] | {name: .name, size: .size}"
```

Open the release page when needed:

```powershell
gh release view $tag --web
```

## Troubleshooting Learned From The First Windows Validation Build

| Symptom | Cause | Fix |
|---|---|---|
| `npm.ps1 cannot be loaded` | PowerShell blocks unsigned `npm.ps1` | Run `npm.cmd ...` or use a process-scoped execution-policy override |
| `py -3.11 ... No suitable Python runtime found` | Python 3.11 is not installed/detected on the build machine | Install Python 3.11, reopen PowerShell, verify `py -3.11 --version` |
| Staging succeeds but no `build\electron` exists | Final Electron Builder step did not run or failed | Inspect output after `Electron runtime staged`; install Electron npm dependencies if needed |
| `'electron-builder' is not recognized` | `electron\node_modules` dependencies are missing | Run `npm.cmd install` in `electron` |
| Staged `default_runtime.env` is local mode with blank Supabase fields | provisioning env vars were missing from the current shell | Re-set `$env:NJORDHR_DEFAULT_*` and rebuild |
| Startup failure `ModuleNotFoundError: No module named 'runtime_env'` | packaged app omitted root Python runtime helper | Ensure staged/installed `runtime_env.py` exists |
| Blank Electron window and `/ui_vendor/react.development.js` says `Vendor asset not found` | packaged app omitted `web_vendor` UI runtime assets | Ensure staged/installed `web_vendor` files exist and rebuild |
| Cloud bootstrap says Supabase host fails DNS resolution | project URL/network/Supabase project availability issue | confirm URL, DNS reachability, and that the Supabase project is not paused |

## Follow-Up Automation Opportunity

The current workflow is still operator-heavy. A future packaging task should add a Windows PowerShell wrapper that validates prerequisites, captures provisioning variables safely, runs `npm.cmd run dist:win`, checks the staged payload, and prints the installer/upload commands.
