@echo off
setlocal
set SCRIPT_DIR=%~dp0
if exist "%SCRIPT_DIR%start_njordhr.vbs" (
  wscript.exe "%SCRIPT_DIR%start_njordhr.vbs"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%scripts\windows\start_njordhr.ps1"
)
endlocal
