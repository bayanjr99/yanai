# =============================================================================
# Click-to-launch wrapper around start_dashboard.ps1.
#
# What this does (in order):
#   1. Check if Streamlit is already serving on http://localhost:8501.
#      - YES → just open the browser to it (instant, no restart).
#      - NO  → call start_dashboard.ps1 to spin it up (hidden window), wait
#              a few seconds for it to come up, THEN open the browser.
#
# Bound to the Hebrew-named Desktop shortcut "ינאי פרסונל בעמ -
# מערכת ניתוח עלויות.lnk". Hidden via the .bat wrapper so the user
# never sees a console window flash open.
# =============================================================================

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjRoot  = Split-Path -Parent $ScriptDir
$Url       = "http://localhost:8501"

function Test-StreamlitUp {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (Test-StreamlitUp) {
    # Already running — just open browser.
    Start-Process $Url
    exit 0
}

# Not running — start it. start_dashboard.ps1 lives next to this file.
$starter = Join-Path $ScriptDir "start_dashboard.ps1"
if (-not (Test-Path $starter)) {
    [System.Windows.Forms.MessageBox]::Show(
        "Cannot find start_dashboard.ps1 at:`n$starter",
        "Yanai Dashboard",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

# Launch the starter (hidden). It runs Streamlit + (optionally) ngrok in
# the background and returns within ~10s.
& powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -WindowStyle Hidden -File $starter | Out-Null

# Poll for the server to actually accept connections — at most 30s.
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    if (Test-StreamlitUp) { break }
    Start-Sleep -Milliseconds 700
}

# Open the browser whether Streamlit responded or not. If it didn't, the
# browser will show an error and the user knows to check the logs.
Start-Process $Url
