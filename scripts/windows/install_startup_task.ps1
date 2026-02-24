Param(
    [string]$TaskName = "NjordHRLocalStartup"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LauncherPath = Join-Path $ProjectDir "scripts\windows\start_njordhr.ps1"

if (-not (Test-Path $LauncherPath)) {
    throw "Launcher not found: $LauncherPath"
}

$quotedLauncher = '"' + $LauncherPath + '"'
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $quotedLauncher -NoOpen"

schtasks /Create /TN $TaskName /TR $action /SC ONLOGON /RL LIMITED /F | Out-Null
Write-Host "[NjordHR] Startup task installed: $TaskName"
Write-Host "[NjordHR] It will run at user logon."

