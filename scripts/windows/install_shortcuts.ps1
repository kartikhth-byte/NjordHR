Param(
    [switch]$DesktopOnly
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Launcher = Join-Path $ProjectDir "start_njordhr.vbs"
$IconPath = Join-Path $ProjectDir "electron\buildResources\NjordHR.ico"
if (-not (Test-Path $Launcher)) {
    throw "Launcher not found: $Launcher"
}

$WshShell = New-Object -ComObject WScript.Shell

function New-Shortcut([string]$path) {
    $shortcut = $WshShell.CreateShortcut($path)
    $shortcut.TargetPath = $Launcher
    $shortcut.WorkingDirectory = $ProjectDir
    $shortcut.WindowStyle = 7
    $shortcut.Description = "Open NjordHR"
    if (Test-Path $IconPath) {
        $shortcut.IconLocation = $IconPath
    }
    $shortcut.Save()
}

$desktopPath = [Environment]::GetFolderPath("Desktop")
$desktopShortcut = Join-Path $desktopPath "NjordHR.lnk"
New-Shortcut $desktopShortcut
Write-Host "[NjordHR] Desktop shortcut created:"
Write-Host "  $desktopShortcut"

if (-not $DesktopOnly) {
    $startMenuPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    $startMenuShortcut = Join-Path $startMenuPath "NjordHR.lnk"
    New-Shortcut $startMenuShortcut
    Write-Host "[NjordHR] Start Menu shortcut created:"
    Write-Host "  $startMenuShortcut"
}

Write-Host "[NjordHR] Users can open NjordHR from icon; no localhost URL typing needed."
