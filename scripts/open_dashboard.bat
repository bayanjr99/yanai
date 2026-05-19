@echo off
REM Click-to-launch the Yanai Personnel dashboard.
REM Self-contained launcher: uses powershell -Command (inline) so it is not
REM blocked by the file-based ExecutionPolicy on this system.
REM
REM Behavior:
REM   1. Probe http://localhost:8501
REM   2. If alive -> just open the browser
REM   3. If dead  -> start Streamlit hidden via start_dashboard.ps1,
REM                  poll up to 30s, then open the browser anyway

set "PROJ=%~dp0.."
set "URL=http://localhost:8501"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"

if %ERRORLEVEL%==0 (
    start "" "%URL%"
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','%PROJ%\scripts\start_dashboard.ps1' -WindowStyle Hidden"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(30); while ((Get-Date) -lt $deadline) { try { $r = Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 700 }; exit 1"

start "" "%URL%"
exit /b 0