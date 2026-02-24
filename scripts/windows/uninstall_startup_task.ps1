Param(
    [string]$TaskName = "NjordHRLocalStartup"
)

$ErrorActionPreference = "Continue"
schtasks /Delete /TN $TaskName /F | Out-Null
Write-Host "[NjordHR] Startup task removed (if it existed): $TaskName"

