# start_tunnel.ps1 ג€” start a public tunnel to the dashboard via Cloudflare.
# Used because Sophos Intercept X blocks ngrok. cloudflared is Microsoft-signed
# and allowed by Sophos. The URL changes on each restart (it's a random
# Cloudflare Quick Tunnel); the current URL is written to logs/public_url.txt.

$ErrorActionPreference = "Stop"
$ProjRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $ProjRoot "logs"
$CfExe = "C:\Tools\cloudflared\cloudflared.exe"
$UrlFile = Join-Path $LogDir "public_url.txt"
$OutLog = Join-Path $LogDir "cf_tunnel.log"
$ErrLog = Join-Path $LogDir "cf_tunnel.err"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# Download cloudflared if missing
if (-not (Test-Path $CfExe)) {
    Write-Host "Downloading cloudflared..." -ForegroundColor Cyan
    $cfDir = Split-Path $CfExe
    New-Item -ItemType Directory -Path $cfDir -Force | Out-Null
    curl.exe -L -o $CfExe "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" --silent
}

# Make sure Streamlit is running
$stRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "streamlit" }
if (-not $stRunning) {
    Write-Host "Starting Streamlit..." -ForegroundColor Cyan
    Start-Process -FilePath "python" `
        -ArgumentList "-m","streamlit","run","app_gpt_dashboard.py","--server.port=8501","--server.headless=true","--server.address=127.0.0.1","--server.enableCORS=false","--server.enableXsrfProtection=false","--browser.gatherUsageStats=false" `
        -WorkingDirectory $ProjRoot `
        -RedirectStandardOutput "$LogDir\streamlit.log" `
        -RedirectStandardError "$LogDir\streamlit.err" `
        -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 8
}

# Kill any existing tunnel
Get-Process -Name "cloudflared","ssh" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Remove-Item $OutLog, $ErrLog -Force -ErrorAction SilentlyContinue
Write-Host "Starting Cloudflare Quick Tunnel..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $CfExe `
    -ArgumentList "tunnel","--url","http://localhost:8501","--protocol","http2","--no-autoupdate" `
    -PassThru `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden

# Poll for the URL
$deadline = (Get-Date).AddSeconds(40)
$url = $null
while ((Get-Date) -lt $deadline -and -not $url) {
    Start-Sleep -Seconds 2
    if (Test-Path $ErrLog) {
        $content = Get-Content $ErrLog -Raw -ErrorAction SilentlyContinue
        if ($content -match 'https://([a-z0-9\-]+\.trycloudflare\.com)') {
            $url = "https://" + $Matches[1]
        }
    }
    if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: cloudflared died" -ForegroundColor Red
        exit 1
    }
}

if (-not $url) {
    Write-Host "ERROR: cloudflared didn't return a URL" -ForegroundColor Red
    exit 1
}

$url | Out-File $UrlFile -Encoding UTF8
Write-Host ""
Write-Host "Dashboard live at: $url" -ForegroundColor Green
Write-Host "Local URL:         http://localhost:8501" -ForegroundColor DarkGray
Write-Host "cloudflared PID:   $($proc.Id)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "URL saved to: $UrlFile" -ForegroundColor DarkGray

