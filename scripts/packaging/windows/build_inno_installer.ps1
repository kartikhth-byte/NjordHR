Param(
    [string]$AppVersion = "1.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$BuildDir = Join-Path $ProjectDir "build\windows"
$StageDir = Join-Path $BuildDir "inno_stage"
$IssPath = Join-Path $BuildDir "NjordHR.iss"

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
