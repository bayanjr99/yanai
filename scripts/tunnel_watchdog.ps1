# tunnel_watchdog.ps1 ג€” keeps the Cloudflare tunnel + Streamlit alive.
# Runs in a loop: every 60 seconds it checks both processes and restarts
# anything that died. Designed to be launched at user logon via Task Scheduler
# and run for the lifetime of the session.

$ErrorActionPreference = "Continue"   # never crash the watchdog
$ProjRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $ProjRoot "logs"
$CfExe  = "C:\Tools\cloudflared\cloudflared.exe"
$UrlFile = Join-Path $LogDir "public_url.txt"
$WdLog  = Join-Path $LogDir "watchdog.log"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Log($msg) {
    $line = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] $msg"
    Add-Content -Path $WdLog -Value $line -Encoding UTF8
}

function Is-StreamlitRunning {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "streamlit" }
    return [bool]$p
}

function Is-TunnelRunning {
    return [bool](Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue)
}

function Start-Streamlit {
    Log "Starting Streamlit..."
    Start-Process -FilePath "python" `
        -ArgumentList "-m","streamlit","run","app_gpt_dashboard.py","--server.port=8501","--server.headless=true","--server.address=127.0.0.1","--server.enableCORS=false","--server.enableXsrfProtection=false","--browser.gatherUsageStats=false" `
        -WorkingDirectory $ProjRoot `
        -RedirectStandardOutput "$LogDir\streamlit.log" `
        -RedirectStandardError "$LogDir\streamlit.err" `
        -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 8
}

function Start-Tunnel {
    Log "Starting Cloudflare tunnel..."
    $OutLog = "$LogDir\cf_tunnel.log"
    $ErrLog = "$LogDir\cf_tunnel.err"
    Remove-Item $OutLog, $ErrLog -Force -ErrorAction SilentlyContinue

    $proc = Start-Process -FilePath $CfExe `
        -ArgumentList "tunnel","--url","http://localhost:8501","--protocol","http2","--no-autoupdate" `
        -PassThru `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden

    # Wait up to 40 seconds for the URL to appear
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
            Log "Tunnel process died during startup"
            return
        }
    }

    if ($url) {
        $url | Out-File $UrlFile -Encoding UTF8
        Log "Tunnel up: $url"
    } else {
        Log "WARN: tunnel started but no URL captured"
    }
}

function Run-DailyBackup {
    $marker = Join-Path $LogDir "last_backup.txt"
    $today = (Get-Date).ToString("yyyy-MM-dd")
    $lastDate = ""
    if (Test-Path $marker) { $lastDate = (Get-Content $marker -ErrorAction SilentlyContinue).Trim() }
    if ($lastDate -eq $today) { return }   # already backed up today

    Log "Running daily backup..."
    $bkScript = Join-Path (Split-Path $MyInvocation.PSCommandPath -Parent) "backup.ps1"
    if (-not (Test-Path $bkScript)) {
        $bkScript = Join-Path $ProjRoot "scripts\backup.ps1"
    }
    if (Test-Path $bkScript) {
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bkScript 2>&1 | Out-Null
            $today | Out-File $marker -Encoding UTF8
            Log "Backup done."
        } catch {
            Log ("Backup failed: " + $_.Exception.Message)
        }
    } else {
        Log "WARN: backup.ps1 not found"
    }
}

# Main loop
Log "Watchdog starting (project: $ProjRoot)"

while ($true) {
    try {
        if (-not (Is-StreamlitRunning)) {
            Log "Streamlit not running"
            Start-Streamlit
        }
        if (-not (Is-TunnelRunning)) {
            Log "Tunnel not running"
            Start-Tunnel
        }
        # Daily backup check (only runs once per calendar day)
        Run-DailyBackup
    } catch {
        Log ("ERROR: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds 60
}

