Param(
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeDir = Join-Path $ProjectDir "logs\runtime"
New-Item -Path $RuntimeDir -ItemType Directory -Force | Out-Null

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

try {
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
        $backendCmd = "set NJORDHR_PORT=$backendPort&& set NJORDHR_SERVER_URL=$backendUrl&& set USE_LOCAL_AGENT=true&& python3 backend_server.py"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c $backendCmd" -WorkingDirectory $ProjectDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
        if (-not (Wait-Http "$backendUrl/config/runtime" 100)) {
            throw "Backend failed to start. Check $backendErr"
        }
    }

    if (Wait-Http "$agentUrl/health" 1) {
        Write-Host "[NjordHR] Agent already running at $agentUrl"
    } else {
        Write-Host "[NjordHR] Starting local agent at $agentUrl"
        $agentCmd = "set NJORDHR_AGENT_HOST=127.0.0.1&& set NJORDHR_AGENT_PORT=$agentPort&& python3 agent_server.py"
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
    Write-Host "[NjordHR] Logs:    $RuntimeDir"
} finally {
    Cleanup-Lock
}

