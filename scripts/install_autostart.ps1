# =============================================================================
# Installs two Windows Scheduled Tasks:
#   1. BillingDashboard   — starts the dashboard at user logon
#   2. BillingDailyBackup — runs the daily backup at 02:00
#
# Re-run this script after any change to the .ps1 helpers; it deletes and
# recreates the tasks idempotently.
#
# Usage (run once, as the logged-in user):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjRoot  = Split-Path -Parent $ScriptDir

$startScript  = Join-Path $ScriptDir "start_dashboard.ps1"
$backupScript = Join-Path $ScriptDir "backup.ps1"
# Batch wrappers — schtasks parses /tr poorly when the path contains "-" or
# spaces, so we point it at short stable .bat files in scripts/.
$startBat  = Join-Path $ScriptDir "run_dashboard.bat"
$backupBat = Join-Path $ScriptDir "run_backup.bat"

foreach ($p in @($startScript, $backupScript, $startBat, $backupBat)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

# Helper: remove a task if it exists (silent if not)
# NOTE: PowerShell 5.1 wraps native-exe stderr in NativeCommandError records
# even with 2>$null, so we shell out via cmd.exe which swallows stderr cleanly.
function Remove-TaskIfPresent($name) {
    $null = cmd.exe /c "schtasks /query /tn `"$name`" >NUL 2>&1"
    if ($LASTEXITCODE -eq 0) {
        $null = cmd.exe /c "schtasks /delete /tn `"$name`" /f >NUL 2>&1"
        Write-Host "  removed existing task: $name" -ForegroundColor DarkGray
    }
}

# ---- 1) Dashboard at logon --------------------------------------------------
$taskA = "BillingDashboard"
Remove-TaskIfPresent $taskA
$null = cmd.exe /c "schtasks /create /sc onlogon /tn `"$taskA`" /tr `"$startBat`" /f >NUL"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Installed: $taskA (runs at user logon)" -ForegroundColor Green
} else {
    Write-Host "FAILED to install: $taskA (exit $LASTEXITCODE)" -ForegroundColor Red
}

# ---- 2) Daily backup at 02:00 ----------------------------------------------
$taskB = "BillingDailyBackup"
Remove-TaskIfPresent $taskB
$null = cmd.exe /c "schtasks /create /sc daily /st 02:00 /tn `"$taskB`" /tr `"$backupBat`" /f >NUL"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Installed: $taskB (runs daily at 02:00)" -ForegroundColor Green
} else {
    Write-Host "FAILED to install: $taskB (exit $LASTEXITCODE)" -ForegroundColor Red
}

Write-Host ""
Write-Host "To verify:   schtasks /query /tn BillingDashboard /v /fo LIST" -ForegroundColor DarkGray
Write-Host "To remove:   schtasks /delete /tn BillingDashboard /f"         -ForegroundColor DarkGray
Write-Host "To run now:  schtasks /run   /tn BillingDashboard"             -ForegroundColor DarkGray
