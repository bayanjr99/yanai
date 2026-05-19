@echo off
REM Wrapper for tunnel_watchdog.ps1 — used by Task Scheduler at user logon.
REM Keeps Streamlit + Cloudflare tunnel alive forever.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tunnel_watchdog.ps1"
