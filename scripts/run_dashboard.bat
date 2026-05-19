@echo off
REM Wrapper for start_dashboard.ps1 — used by Windows Task Scheduler.
REM Lives next to the .ps1 so the path is short and stable.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_dashboard.ps1"
