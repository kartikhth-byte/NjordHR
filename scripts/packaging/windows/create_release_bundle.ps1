Param(
    [string]$Version = (Get-Date -Format "yyyy.MM.dd.HHmm")
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$ReleaseDir = Join-Path $ProjectDir ("release\" + $Version)
$InstallPath = Join-Path $ReleaseDir "INSTALL.md"

function Copy-LatestArtifact([string]$SourceDir, [string]$Pattern) {
    if (-not (Test-Path $SourceDir)) { return $null }
    $candidate = Get-ChildItem -Path $SourceDir -Filter $Pattern -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $candidate) { return $null }
    Copy-Item $candidate.FullName -Destination $ReleaseDir -Force
    Write-Host "[NjordHR] Added: $($candidate.Name)"
    return $candidate.Name
}

function Get-PythonInvocation {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Exe = $python.Source
            Args = @()
            Display = "python"
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            Exe = $py.Source
            Args = @("-3")
            Display = "py -3"
        }
    }

    throw "Python is required to build release metadata. Install Python 3 and ensure python or py is on PATH."
}

New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
Remove-Item -Path (Join-Path $ReleaseDir "checksums.txt"), (Join-Path $ReleaseDir "manifest.json"), $InstallPath -Force -ErrorAction SilentlyContinue

$macInstaller = Copy-LatestArtifact (Join-Path $ProjectDir "build\macos") "NjordHR-*-unsigned.pkg"
if (-not $macInstaller) {
    $macInstaller = Copy-LatestArtifact (Join-Path $ProjectDir "build\macos") "NjordHR-unsigned.pkg"
}
$windowsSetup = Copy-LatestArtifact (Join-Path $ProjectDir "build\windows") "NjordHR-*-setup.exe"
$windowsPortable = Copy-LatestArtifact (Join-Path $ProjectDir "build\windows") "NjordHR-*-portable.zip"
$electronSetup = Copy-LatestArtifact (Join-Path $ProjectDir "build\electron") "NjordHR-Electron-*-win.exe"
$windowsSetupName = if ($windowsSetup) { $windowsSetup } else { "NjordHR-setup.exe" }

$artifacts = Get-ChildItem -Path $ReleaseDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notin @("checksums.txt", "manifest.json", "INSTALL.md") -and -not $_.Name.EndsWith(".sig") } |
    Sort-Object Name
if (-not $artifacts) {
    throw "[NjordHR] No artifacts found in build folders. Build installers first."
}

$releaseNotes = @'
# NjordHR Windows Validation Build Install Notes

This release folder contains unsigned validation artifacts for Windows.

## Windows

1. Remove any old install and runtime state:
   ```powershell
   Remove-Item -Recurse -Force "`$env:LOCALAPPDATA\Programs\NjordHR" -ErrorAction SilentlyContinue
   Remove-Item -Recurse -Force "`$env:APPDATA\NjordHR" -ErrorAction SilentlyContinue
   ```
2. Run the installer:
   ```powershell
   Start-Process ".\__WINDOWS_SETUP__" -Wait
   Start-Process "`$env:LOCALAPPDATA\Programs\NjordHR\NjordHR.exe"
   ```
3. Validate:
   ```powershell
   Get-Content "`$env:LOCALAPPDATA\Programs\NjordHR\resources\app\default_runtime.env"
   Get-Content "`$env:APPDATA\NjordHR\runtime\runtime.env"
   Invoke-WebRequest http://127.0.0.1:5050/runtime/ready -UseBasicParsing | Select-Object -ExpandProperty Content
   Invoke-WebRequest http://127.0.0.1:5051/health -UseBasicParsing | Select-Object -ExpandProperty Content
   ```

Expected:
- `NJORDHR_AUTH_MODE=cloud`
- `USE_SUPABASE_DB=true`
- `USE_SUPABASE_READS=true`
- backend `/runtime/ready` returns `auth_mode: cloud`
- agent `/health` returns `status: ok`

## Checksums

`checksums.txt` contains SHA-256 checksums for every artifact in this folder.
Verify them after copying artifacts to another machine.
'@

$releaseNotes = $releaseNotes.Replace("__WINDOWS_SETUP__", $windowsSetupName)

$releaseNotes | Set-Content -Path $InstallPath -Encoding UTF8

$python = Get-PythonInvocation
& $python.Exe @($python.Args + @(
    (Join-Path $ProjectDir "scripts\packaging\release_bundle_common.py"),
    "--release-dir", $ReleaseDir,
    "--version", $Version
))
if ($LASTEXITCODE -ne 0) {
    throw "Release bundle metadata generation failed."
}

Write-Host "[NjordHR] Release bundle created:"
Write-Host "  $ReleaseDir"
Write-Host "[NjordHR] Files:"
Get-ChildItem -Path $ReleaseDir -File | Sort-Object Name | ForEach-Object { Write-Host "  $($_.Name)" }
