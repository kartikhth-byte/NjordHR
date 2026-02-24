Param(
    [string]$AppVersion = "1.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$BuildDir = Join-Path $ProjectDir "build\windows"
$IssPath = Join-Path $BuildDir "NjordHR.iss"

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
if (-not $iscc) {
    Write-Host "[NjordHR] Inno Setup (iscc.exe) not found."
    Write-Host "[NjordHR] Install Inno Setup, then re-run."
    exit 1
}

$escapedProject = $ProjectDir -replace "\\", "\\"

@"
[Setup]
AppName=NjordHR
AppVersion=$AppVersion
DefaultDirName={autopf}\NjordHR
DefaultGroupName=NjordHR
OutputBaseFilename=NjordHR-$AppVersion-setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "$escapedProject\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "$escapedProject\.git\*"; DestDir: "{app}\.git"; Flags: skipifsourcedoesntexist recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NjordHR"; Filename: "{app}\start_njordhr.bat"
Name: "{commondesktop}\NjordHR"; Filename: "{app}\start_njordhr.bat"

[Run]
Filename: "{app}\start_njordhr.bat"; Description: "Launch NjordHR"; Flags: nowait postinstall skipifsilent
"@ | Set-Content -Path $IssPath -Encoding ASCII

& $iscc.Source $IssPath

Write-Host "[NjordHR] Inno installer build complete. Check:"
Write-Host "  $BuildDir"

