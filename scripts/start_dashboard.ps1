# =============================================================================
# Start the BI dashboard: Streamlit + ngrok tunnel.
#
# Reads NGROK_AUTHTOKEN, NGROK_DOMAIN, DASHBOARD_PASSWORD from .env (if
# present) or environment variables. Spawns both processes in the background
# and logs to logs/.
#
# Run by Task Scheduler at user logon (see scripts/install_autostart.ps1) so
# the dashboard is always available without manual intervention.
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjRoot  = Split-Path -Parent $ScriptDir
Set-Location $ProjRoot

# ---- Load .env if present (KEY=VALUE per line) ------------------------------
$envFile = Join-Path $ProjRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$') {
            Set-Item -Path "env:$($Matches[1])" -Value $Matches[2]
        }
    }
}

# ---- Find ngrok ------------------------------------------------------------
$ngrok = "$env:LOCALAPPDATA\ngrok\ngrok.exe"
if (-not (Test-Path $ngrok)) {
    Write-Host "WARNING: ngrok not found at $ngrok — installing via pyngrok"
    python -c "from pyngrok import ngrok; ngrok.install_ngrok()" | Out-Null
}

# ---- Configure ngrok authtoken if needed ------------------------------------
if ($env:NGROK_AUTHTOKEN) {
    & $ngrok config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null
}

# ---- Stop any previous instances --------------------------------------------
Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "streamlit" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# ---- Prepare logs dir -------------------------------------------------------
$logDir = Join-Path $ProjRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# ---- Start Streamlit --------------------------------------------------------
Write-Host "Starting Streamlit on http://localhost:8501 ..." -ForegroundColor Cyan
$stProc = Start-Process -FilePath "python" `
    -ArgumentList "-m","streamlit","run","app_gpt_dashboard.py",`
                  "--server.port=8501",`
                  "--server.headless=true",`
                  "--server.address=127.0.0.1",`
                  "--browser.gatherUsageStats=false" `
    -WorkingDirectory $ProjRoot `
    -PassThru `
    -RedirectStandardOutput "$logDir\streamlit.log" `
    -RedirectStandardError "$logDir\streamlit.err" `
    -WindowStyle Hidden
Write-Host "Streamlit PID: $($stProc.Id)" -ForegroundColor DarkGray

Start-Sleep -Seconds 6

# ---- Start ngrok (only if NGROK_DOMAIN is set) ------------------------------
if ($env:NGROK_DOMAIN) {
    Write-Host "Starting ngrok tunnel -> https://$env:NGROK_DOMAIN ..." -ForegroundColor Cyan
    $ngProc = Start-Process -FilePath $ngrok `
        -ArgumentList "http","--url=$env:NGROK_DOMAIN","8501" `
        -WorkingDirectory $ProjRoot `
        -PassThru `
        -RedirectStandardOutput "$logDir\ngrok.log" `
        -RedirectStandardError "$logDir\ngrok.err" `
        -WindowStyle Hidden
    Write-Host "ngrok PID: $($ngProc.Id)" -ForegroundColor DarkGray
} else {
    Write-Host "NGROK_DOMAIN not set — running localhost only." -ForegroundColor Yellow
}

Start-Sleep -Seconds 3

# ---- Sanity check -----------------------------------------------------------
try {
    $r = Invoke-WebRequest "http://127.0.0.1:8501" -UseBasicParsing -TimeoutSec 5
    Write-Host "OK — Streamlit responding (HTTP $($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "WARNING — Streamlit not responding yet. Check $logDir\streamlit.log" -ForegroundColor Red
}

Write-Host ""
Write-Host "Dashboard is live!" -ForegroundColor Green
if ($env:NGROK_DOMAIN) {
    Write-Host "  Public URL: https://$env:NGROK_DOMAIN" -ForegroundColor Green
}
Write-Host "  Local URL : http://localhost:8501" -ForegroundColor Green
Write-Host "  To stop:    .\scripts\stop_dashboard.ps1" -ForegroundColor DarkGray
