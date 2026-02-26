Param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$BuildDir = Join-Path $ProjectDir "build\windows"
$StageDir = Join-Path $BuildDir "stage"
$ZipPath = Join-Path $BuildDir ("NjordHR-" + $Version + "-portable.zip")

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

Write-Host "[NjordHR] Preparing portable payload..."
robocopy $ProjectDir $StageDir /E /NFL /NDL /NJH /NJS /NP `
    /XD ".git" "__pycache__" ".pytest_cache" "build" "release" "Verified_Resumes" "logs" "AI_Search_Results" `
    /XF ".env" ".env.*" "config.ini" "*.db" "*.db-journal" "*.sqlite" "*.sqlite3" "*.csv" | Out-Null

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ZipPath

Write-Host "[NjordHR] Portable ZIP built:"
Write-Host "  $ZipPath"
Write-Host "[NjordHR] Run after extract:"
Write-Host "  start_njordhr.bat"
