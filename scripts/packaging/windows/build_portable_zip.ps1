Param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$BuildDir = Join-Path $ProjectDir "build\windows"
$StageDir = Join-Path $BuildDir "stage"
$ZipPath = Join-Path $BuildDir ("NjordHR-" + $Version + "-portable.zip")
$DefaultRuntimeEnvPath = Join-Path $StageDir "default_runtime.env"

function Get-DefaultEnv([string]$Name, [string]$Fallback = "") {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Fallback
    }
    return $value
}

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

Write-Host "[NjordHR] Preparing portable payload..."
robocopy $ProjectDir $StageDir /E /NFL /NDL /NJH /NJS /NP `
    /XD ".git" "__pycache__" ".pytest_cache" "build" "release" "Verified_Resumes" "logs" "AI_Search_Results" `
    /XF ".env" ".env.*" "config.ini" "*.db" "*.db-journal" "*.sqlite" "*.sqlite3" "*.csv" | Out-Null

@"
USE_SUPABASE_DB=$(Get-DefaultEnv "NJORDHR_DEFAULT_USE_SUPABASE_DB" "true")
USE_SUPABASE_READS=$(Get-DefaultEnv "NJORDHR_DEFAULT_USE_SUPABASE_READS" "true")
USE_DUAL_WRITE=$(Get-DefaultEnv "NJORDHR_DEFAULT_USE_DUAL_WRITE" "false")
USE_LOCAL_AGENT=$(Get-DefaultEnv "NJORDHR_DEFAULT_USE_LOCAL_AGENT" "true")
NJORDHR_AUTH_MODE=$(Get-DefaultEnv "NJORDHR_DEFAULT_AUTH_MODE" "cloud")
NJORDHR_PASSWORD_HASH_METHOD=$(Get-DefaultEnv "NJORDHR_DEFAULT_PASSWORD_HASH_METHOD" "pbkdf2:sha256:600000")
SUPABASE_URL=$(Get-DefaultEnv "NJORDHR_DEFAULT_SUPABASE_URL" "")
SUPABASE_SECRET_KEY=$(Get-DefaultEnv "NJORDHR_DEFAULT_SUPABASE_SECRET_KEY" "")
SUPABASE_SERVICE_ROLE_KEY=$(Get-DefaultEnv "NJORDHR_DEFAULT_SUPABASE_SERVICE_ROLE_KEY" "")
"@ | Set-Content -Path $DefaultRuntimeEnvPath -Encoding ASCII

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ZipPath

Write-Host "[NjordHR] Portable ZIP built:"
Write-Host "  $ZipPath"
Write-Host "[NjordHR] Run after extract:"
Write-Host "  start_njordhr.bat"
