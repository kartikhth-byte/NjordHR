Param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AppDataDir = Join-Path $env:APPDATA "NjordHR"
$RuntimeDir = Join-Path $AppDataDir "runtime"
$VenvDir = Join-Path $RuntimeDir "venv"
$ConfigPath = Join-Path $AppDataDir "config.ini"
$DefaultRuntimeEnvPath = Join-Path $ProjectDir "default_runtime.env"
$DefaultDownloadDir = Join-Path $env:USERPROFILE "Downloads\NjordHR"
$DefaultVerifiedDir = Join-Path $AppDataDir "Verified_Resumes"
$DefaultLogDir = Join-Path $AppDataDir "logs"
New-Item -Path $RuntimeDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultDownloadDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultVerifiedDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultLogDir -ItemType Directory -Force | Out-Null

$LauncherLogPath = Join-Path $RuntimeDir "launcher.log"
function Write-Log([string]$Message) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $LauncherLogPath -Value $line -Encoding UTF8
}

Write-Log "Launcher started."
Write-Log "ProjectDir=$ProjectDir"
Write-Log "ConfigPath=$ConfigPath"
Write-Log "RuntimeDir=$RuntimeDir"

function Cleanup-Lock {
    # No-op: launch lock removed to avoid false-positive "already running" loops.
}

function Read-EnvFile([string]$Path) {
    $result = @{}
    if (-not (Test-Path $Path)) {
        return $result
    }
    foreach ($line in Get-Content -Path $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -le 0) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $val = $trimmed.Substring($idx + 1).Trim()
        if ($key) {
            $result[$key] = $val
        }
    }
    return $result
}

function Escape-CmdValue([string]$Value) {
    if ($null -eq $Value) { return "" }
    return $Value.Replace('"', '""')
}

function Build-CmdEnvPrefix([hashtable]$Vars) {
    $segments = @()
    foreach ($k in ($Vars.Keys | Sort-Object)) {
        $v = Escape-CmdValue ([string]$Vars[$k])
        $segments += "set `"$k=$v`""
    }
    return ($segments -join "&& ")
}

function Test-Http([string]$url) {
    try {
        Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 2 -UseBasicParsing | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-PortListening([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(250)
        if (-not $ok) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Pick-Free-Port([int]$startPort) {
    $port = $startPort
    while (Test-PortListening $port) {
        $port++
        if ($port -gt ($startPort + 100)) {
            throw "Could not find free port near $startPort"
        }
    }
    return $port
}

function Wait-Http([string]$url, [int]$retries = 40) {
    for ($i = 0; $i -lt $retries; $i++) {
        if (Test-Http $url) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-PythonInvocation {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        # Enforce 3.11 for stable desktop runtime behavior across Windows hosts.
        $probe311 = & $py.Source -3.11 -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{
                Exe = $py.Source
                PrefixArgs = @("-3.11")
                Display = "py -3.11"
            }
        }
        throw "Found py launcher but Python 3.11 is unavailable. Install Python 3.11 and run again."
    }
    throw "Python launcher 'py' not found. Install Python 3.11 from python.org and ensure py.exe is available."
}

function Invoke-Python([hashtable]$Py, [string[]]$PyArgs) {
    & $Py.Exe @($Py.PrefixArgs + $PyArgs)
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Py.Display) $($PyArgs -join ' ')"
    }
}

function Ensure-Config([hashtable]$Py) {
    if (-not (Test-Path $ConfigPath)) {
        $sourceConfig = Join-Path $ProjectDir "config.ini"
        if (-not (Test-Path $sourceConfig)) {
            $sourceConfig = Join-Path $ProjectDir "config.example.ini"
        }
        if (Test-Path $sourceConfig) {
            Copy-Item $sourceConfig $ConfigPath -Force
        }
    }
    if (-not (Test-Path $ConfigPath)) {
        @"
[Credentials]
Username =
Password =
Gemini_API_Key =
Pinecone_API_Key =

[Settings]
Default_Download_Folder = $DefaultDownloadDir
Additional_Local_Folder = $DefaultVerifiedDir

[Advanced]
log_dir = $DefaultLogDir
"@ | Set-Content -Path $ConfigPath -Encoding UTF8
    }

    $script = @'
import configparser
import os
import sys

cfg_path, download_dir, verified_dir, log_dir = sys.argv[1:5]
cfg = configparser.ConfigParser()
cfg.read(cfg_path)

if "Credentials" not in cfg:
    cfg["Credentials"] = {}
if "Settings" not in cfg:
    cfg["Settings"] = {}
if "Advanced" not in cfg:
    cfg["Advanced"] = {}

def normalize(v):
    return os.path.abspath(os.path.expanduser((v or "").strip()))

download_dir = normalize(download_dir)
verified_dir = normalize(verified_dir)
log_dir = normalize(log_dir)

download_raw = cfg["Settings"].get("Default_Download_Folder", "")
if (not download_raw.strip()) or "/absolute/path/" in download_raw:
    cfg["Settings"]["Default_Download_Folder"] = download_dir

verified_raw = cfg["Settings"].get("Additional_Local_Folder", "")
if (not verified_raw.strip()) or "/absolute/path/" in verified_raw:
    cfg["Settings"]["Additional_Local_Folder"] = verified_dir

log_raw = cfg["Advanced"].get("log_dir", "")
if (not log_raw.strip()) or "/absolute/path/" in log_raw:
    cfg["Advanced"]["log_dir"] = log_dir

with open(cfg_path, "w", encoding="utf-8") as fh:
    cfg.write(fh)
'@
    $tmpScript = Join-Path $RuntimeDir "ensure_config.py"
    Set-Content -Path $tmpScript -Value $script -Encoding UTF8
    try {
        Invoke-Python $Py @($tmpScript, $ConfigPath, $DefaultDownloadDir, $DefaultVerifiedDir, $DefaultLogDir) | Out-Null
    } finally {
        Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Venv([hashtable]$Py) {
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $requirementsPath = Join-Path $ProjectDir "requirements.txt"
    $stampPath = Join-Path $RuntimeDir "requirements.sha256"
    $requirementsHash = (Get-FileHash -Path $requirementsPath -Algorithm SHA256).Hash

    if (-not (Test-Path $venvPython)) {
        Write-Host "[NjordHR] Creating local Python runtime..."
        Invoke-Python $Py @("-m", "venv", $VenvDir) | Out-Null
    }

    $needsInstall = $true
    if (Test-Path $stampPath) {
        $currentHash = (Get-Content -Path $stampPath -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($currentHash -eq $requirementsHash) {
            $needsInstall = $false
        }
    }

    if ($needsInstall) {
        Write-Host "[NjordHR] Installing Python dependencies..."
        & $venvPython -m pip install --upgrade pip setuptools wheel | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to upgrade pip/setuptools/wheel in local runtime."
        }
        & $venvPython -m pip install -r $requirementsPath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Python requirements from $requirementsPath."
        }
        Set-Content -Path $stampPath -Value $requirementsHash -Encoding ASCII
    }

    return $venvPython
}

try {
    $py = Get-PythonInvocation
    Write-Log "Python launcher selected: $($py.Display)"
    Ensure-Config $py
    $venvPython = Ensure-Venv $py
    Write-Log "Venv Python: $venvPython"

    $defaultBackendPort = if ($env:NJORDHR_PORT) { [int]$env:NJORDHR_PORT } else { 5050 }
    $defaultAgentPort = if ($env:NJORDHR_AGENT_PORT) { [int]$env:NJORDHR_AGENT_PORT } else { 5051 }

    $backendPort = $defaultBackendPort
    $agentPort = $defaultAgentPort

    if (-not (Wait-Http "http://127.0.0.1:$backendPort/config/runtime" 1)) {
        if (Test-PortListening $backendPort) {
            $backendPort = Pick-Free-Port $defaultBackendPort
        }
    }
    if (-not (Wait-Http "http://127.0.0.1:$agentPort/health" 1)) {
        if (Test-PortListening $agentPort) {
            $agentPort = Pick-Free-Port $defaultAgentPort
        }
    }
    if ($backendPort -eq $agentPort) {
        $agentPort = Pick-Free-Port ($defaultAgentPort + 1)
    }

    $backendUrl = "http://127.0.0.1:$backendPort"
    $agentUrl = "http://127.0.0.1:$agentPort"
    $pythonLauncherForCmd = ('"' + $venvPython + '"').Trim()
    $provisionedEnv = Read-EnvFile $DefaultRuntimeEnvPath
    if ($provisionedEnv.Count -gt 0) {
        Write-Log "Loaded provisioned runtime defaults from $DefaultRuntimeEnvPath"
    } else {
        Write-Log "No provisioned runtime defaults found at $DefaultRuntimeEnvPath"
    }

    $runtimeEnvPath = Join-Path $RuntimeDir "runtime.env"
    $runtimeVars = @{}
    $runtimeVars["NJORDHR_BACKEND_PORT"] = "$backendPort"
    $runtimeVars["NJORDHR_AGENT_RUNTIME_PORT"] = "$agentPort"
    $runtimeVars["NJORDHR_SERVER_URL"] = $backendUrl
    $runtimeVars["NJORDHR_AGENT_URL"] = $agentUrl
    $runtimeVars["PYTHON_BASIC_REPL"] = "1"
    $runtimeVars["NJORDHR_CONFIG_PATH"] = $ConfigPath
    $runtimeVars["NJORDHR_RUNTIME_DIR"] = $RuntimeDir

    $provisionKeys = @(
        "USE_SUPABASE_DB",
        "USE_SUPABASE_READS",
        "USE_DUAL_WRITE",
        "USE_LOCAL_AGENT",
        "NJORDHR_AUTH_MODE",
        "NJORDHR_PASSWORD_HASH_METHOD",
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY"
    )
    foreach ($k in $provisionKeys) {
        if ($provisionedEnv.ContainsKey($k)) {
            $runtimeVars[$k] = $provisionedEnv[$k]
        }
    }

    if (-not $runtimeVars.ContainsKey("USE_LOCAL_AGENT")) {
        $runtimeVars["USE_LOCAL_AGENT"] = "true"
    }

    foreach ($k in $runtimeVars.Keys) {
        Set-Item -Path "Env:$k" -Value ([string]$runtimeVars[$k])
    }
    ($runtimeVars.Keys | Sort-Object | ForEach-Object { "$_=$($runtimeVars[$_])" }) | Set-Content -Path $runtimeEnvPath -Encoding utf8

    $backendOut = Join-Path $RuntimeDir "backend.out"
    $backendErr = Join-Path $RuntimeDir "backend.err"
    $agentOut = Join-Path $RuntimeDir "agent.out"
    $agentErr = Join-Path $RuntimeDir "agent.err"

    if (Wait-Http "$backendUrl/config/runtime" 1) {
        Write-Log "Backend already running at $backendUrl"
    } else {
        Write-Log "Starting backend at $backendUrl"
        $backendVars = @{}
        foreach ($k in $runtimeVars.Keys) {
            $backendVars[$k] = $runtimeVars[$k]
        }
        $backendVars["NJORDHR_PORT"] = "$backendPort"
        $backendPrefix = Build-CmdEnvPrefix $backendVars
        $backendCmd = "$backendPrefix&& $pythonLauncherForCmd backend_server.py"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c $backendCmd" -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
        if (-not (Wait-Http "$backendUrl/config/runtime" 100)) {
            throw "Backend failed to start. Check $backendErr"
        }
    }

    if (Wait-Http "$agentUrl/health" 1) {
        Write-Log "Agent already running at $agentUrl"
    } else {
        Write-Log "Starting local agent at $agentUrl"
        $agentVars = @{}
        foreach ($k in $runtimeVars.Keys) {
            $agentVars[$k] = $runtimeVars[$k]
        }
        $agentVars["NJORDHR_AGENT_HOST"] = "127.0.0.1"
        $agentVars["NJORDHR_AGENT_PORT"] = "$agentPort"
        $agentPrefix = Build-CmdEnvPrefix $agentVars
        $agentCmd = "$agentPrefix&& $pythonLauncherForCmd agent_server.py"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c $agentCmd" -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $agentOut -RedirectStandardError $agentErr | Out-Null
        if (-not (Wait-Http "$agentUrl/health" 100)) {
            throw "Agent failed to start. Check $agentErr"
        }
    }

    try {
        $payload = @{ api_base_url = $backendUrl; cloud_sync_enabled = $true } | ConvertTo-Json
        Invoke-RestMethod -Method Put -Uri "$agentUrl/settings" -ContentType "application/json" -Body $payload | Out-Null
    } catch {
        # best effort
    }

    if (-not $NoOpen) {
        Start-Process $backendUrl | Out-Null
    }

    Write-Log "Ready."
    Write-Log "Backend: $backendUrl"
    Write-Log "Agent: $agentUrl"
    Write-Log "Config: $ConfigPath"
    Write-Log "Logs: $RuntimeDir"
} catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    Write-Log "Stack: $($_.ScriptStackTrace)"
    throw
} finally {
    Cleanup-Lock
}
