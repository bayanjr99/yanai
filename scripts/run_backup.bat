@echo off
REM Wrapper for backup.ps1 — used by Windows Task Scheduler.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0backup.ps1"
