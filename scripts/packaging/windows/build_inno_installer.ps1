Param(
    [string]$AppVersion = "1.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$BuildDir = Join-Path $ProjectDir "build\windows"
$StageDir = Join-Path $BuildDir "inno_stage"
$IssPath = Join-Path $BuildDir "NjordHR.iss"
$DefaultRuntimeEnvPath = Join-Path $StageDir "default_runtime.env"

function Get-DefaultEnv([string]$Name, [string]$Fallback = "") {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Fallback
    }
    return $value
}

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
if (-not $iscc) {
    Write-Host "[NjordHR] Inno Setup (iscc.exe) not found."
    Write-Host "[NjordHR] Install Inno Setup, then re-run."
    exit 1
}

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

Write-Host "[NjordHR] Preparing clean staging directory..."
robocopy $ProjectDir $StageDir /E /NFL /NDL /NJH /NJS /NP `
    /XD ".git" "__pycache__" ".pytest_cache" "build" "release" "Verified_Resumes" "logs" "AI_Search_Results" `
    /XF ".env" ".env.*" "config.ini" "*.db" "*.db-journal" "*.sqlite" "*.sqlite3" "*.csv" | Out-Null

# Build-time provisioning for seamless first-run in internal deployments.
# Set these environment variables before build to point every install to the same Supabase/auth config:
#   NJORDHR_DEFAULT_SUPABASE_URL
#   NJORDHR_DEFAULT_SUPABASE_SECRET_KEY
#   NJORDHR_DEFAULT_AUTH_MODE (default: cloud)
#   NJORDHR_DEFAULT_USE_SUPABASE_DB (default: true)
#   NJORDHR_DEFAULT_USE_SUPABASE_READS (default: true)
#   NJORDHR_DEFAULT_USE_DUAL_WRITE (default: false)
#   NJORDHR_DEFAULT_USE_LOCAL_AGENT (default: true)
#   NJORDHR_DEFAULT_PASSWORD_HASH_METHOD (default: pbkdf2:sha256:600000)
#   NJORDHR_DEFAULT_SUPABASE_SERVICE_ROLE_KEY
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

$escapedStage = $StageDir -replace "\\", "\\"

@"
[Setup]
AppName=NjordHR
AppVersion=$AppVersion
DefaultDirName={localappdata}\NjordHR
DefaultGroupName=NjordHR
PrivilegesRequired=lowest
OutputBaseFilename=NjordHR-$AppVersion-setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "$escapedStage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NjordHR"; Filename: "{app}\start_njordhr.bat"
Name: "{commondesktop}\NjordHR"; Filename: "{app}\start_njordhr.bat"
"@ | Set-Content -Path $IssPath -Encoding ASCII

& $iscc.Source $IssPath

Write-Host "[NjordHR] Inno installer build complete. Check:"
Write-Host "  $BuildDir"
