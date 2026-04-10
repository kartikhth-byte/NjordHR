Param(
    [string]$TaskName = "NjordHRLocalStartup"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LauncherPath = Join-Path $ProjectDir "start_njordhr.vbs"

if (-not (Test-Path $LauncherPath)) {
    throw "Launcher not found: $LauncherPath"
}

$quotedLauncher = '"' + $LauncherPath + '"'
$action = "wscript.exe $quotedLauncher"

schtasks /Create /TN $TaskName /TR $action /SC ONLOGON /RL LIMITED /F | Out-Null
Write-Host "[NjordHR] Startup task installed: $TaskName"
Write-Host "[NjordHR] It will run at user logon."
