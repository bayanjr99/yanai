@echo off
REM Wrapper for start_tunnel.ps1 — used by Task Scheduler.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_tunnel.ps1"
