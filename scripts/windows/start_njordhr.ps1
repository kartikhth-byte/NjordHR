Param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AppDataDir = Join-Path $env:APPDATA "NjordHR"
$RuntimeDir = Join-Path $AppDataDir "runtime"
$VenvDir = Join-Path $RuntimeDir "venv"
$ConfigPath = Join-Path $AppDataDir "config.ini"
$DefaultDownloadDir = Join-Path $env:USERPROFILE "Downloads\NjordHR"
$DefaultVerifiedDir = Join-Path $AppDataDir "Verified_Resumes"
$DefaultLogDir = Join-Path $AppDataDir "logs"
New-Item -Path $RuntimeDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultDownloadDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultVerifiedDir -ItemType Directory -Force | Out-Null
New-Item -Path $DefaultLogDir -ItemType Directory -Force | Out-Null

$lockPath = Join-Path $env:TEMP "njordhr-launch.lock"
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-Host "[NjordHR] Launcher already running. Try again in a few seconds."
    exit 0
}

function Cleanup-Lock {
    if ($lockStream) {
        $lockStream.Close()
    }
    if (Test-Path $lockPath) {
        Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
    }
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
    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) {
        return @{
            Exe = $python3.Source
            PrefixArgs = @()
            Display = "python3"
        }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Exe = $py.Source
            PrefixArgs = @("-3")
            Display = "py -3"
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Exe = $python.Source
            PrefixArgs = @()
            Display = "python"
        }
    }
    throw "Python runtime not found. Install Python 3.11+ and ensure it is on PATH."
}

function Invoke-Python([hashtable]$Py, [string[]]$Args) {
    & $Py.Exe @($Py.PrefixArgs + $Args)
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Py.Display) $($Args -join ' ')"
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

    $script = @"
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
"@
    Invoke-Python $Py @("-c", $script, $ConfigPath, $DefaultDownloadDir, $DefaultVerifiedDir, $DefaultLogDir) | Out-Null
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
    Write-Host "[NjordHR] Python launcher: $($py.Display)"
    Ensure-Config $py
    $venvPython = Ensure-Venv $py

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

    $runtimeEnvPath = Join-Path $RuntimeDir "runtime.env"
    @(
        "NJORDHR_BACKEND_PORT=$backendPort"
        "NJORDHR_AGENT_RUNTIME_PORT=$agentPort"
        "NJORDHR_SERVER_URL=$backendUrl"
        "NJORDHR_AGENT_URL=$agentUrl"
    ) | Set-Content -Path $runtimeEnvPath -Encoding utf8

    $backendOut = Join-Path $RuntimeDir "backend.out"
    $backendErr = Join-Path $RuntimeDir "backend.err"
    $agentOut = Join-Path $RuntimeDir "agent.out"
    $agentErr = Join-Path $RuntimeDir "agent.err"

    if (Wait-Http "$backendUrl/config/runtime" 1) {
        Write-Host "[NjordHR] Backend already running at $backendUrl"
    } else {
        Write-Host "[NjordHR] Starting backend at $backendUrl"
        $backendCmd = "set `"NJORDHR_PORT=$backendPort`"&& set `"NJORDHR_SERVER_URL=$backendUrl`"&& set `"NJORDHR_CONFIG_PATH=$ConfigPath`"&& set `"NJORDHR_RUNTIME_DIR=$RuntimeDir`"&& set `"USE_LOCAL_AGENT=true`"&& $pythonLauncherForCmd backend_server.py"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c $backendCmd" -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
        if (-not (Wait-Http "$backendUrl/config/runtime" 100)) {
            throw "Backend failed to start. Check $backendErr"
        }
    }

    if (Wait-Http "$agentUrl/health" 1) {
        Write-Host "[NjordHR] Agent already running at $agentUrl"
    } else {
        Write-Host "[NjordHR] Starting local agent at $agentUrl"
        $agentCmd = "set `"NJORDHR_CONFIG_PATH=$ConfigPath`"&& set `"NJORDHR_AGENT_HOST=127.0.0.1`"&& set `"NJORDHR_AGENT_PORT=$agentPort`"&& $pythonLauncherForCmd agent_server.py"
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

    Write-Host "[NjordHR] Ready."
    Write-Host "[NjordHR] Backend: $backendUrl"
    Write-Host "[NjordHR] Agent:   $agentUrl"
    Write-Host "[NjordHR] Config:  $ConfigPath"
    Write-Host "[NjordHR] Logs:    $RuntimeDir"
} finally {
    Cleanup-Lock
}
