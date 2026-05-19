# Scripts — Auto-start & Backup

This folder contains the helpers that keep the dashboard running and the data
backed up, plus the one-time installer that wires them into Windows Task
Scheduler.

## Files

| File | Purpose |
|---|---|
| [start_dashboard.ps1](start_dashboard.ps1) | Launches Streamlit + ngrok in the background |
| [stop_dashboard.ps1](stop_dashboard.ps1) | Kills the Streamlit and ngrok processes |
| [backup.ps1](backup.ps1) | Snapshots `data/`, `output/cache/`, `.streamlit/`, `docs/` to `~/Meylon_Backups/` |
| [run_dashboard.bat](run_dashboard.bat) | Thin wrapper used by Task Scheduler (it doesn't parse PowerShell paths well) |
| [run_backup.bat](run_backup.bat) | Same, for the backup task |
| [install_autostart.ps1](install_autostart.ps1) | Registers two scheduled tasks — **run once, as Administrator** |

## How to install (one-time, ~30 seconds)

1. Open **Start → search "PowerShell" → right-click → "Run as administrator"**
2. Paste:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Meylon\Meylon - Documents\ביאן - הנהחש ולקוחות\billing_system\scripts\install_autostart.ps1"
   ```
3. Confirm both lines show "Installed: …" in green.

## What gets installed

| Task name | Trigger | Action |
|---|---|---|
| **BillingDashboard** | At user logon | Runs `start_dashboard.ps1` → dashboard is up automatically when you log in |
| **BillingDailyBackup** | Daily 02:00 | Runs `backup.ps1` → snapshot to `%USERPROFILE%\Meylon_Backups\` (keeps 30 days, prunes older) |

## Common operations

```powershell
# See tasks
schtasks /query /tn BillingDashboard /v /fo LIST
schtasks /query /tn BillingDailyBackup /v /fo LIST

# Run now (test)
schtasks /run /tn BillingDashboard
schtasks /run /tn BillingDailyBackup

# Disable temporarily
schtasks /change /tn BillingDashboard /disable
schtasks /change /tn BillingDashboard /enable

# Remove entirely
schtasks /delete /tn BillingDashboard   /f
schtasks /delete /tn BillingDailyBackup /f
```

## Manual run (without scheduler)

```powershell
# Start the dashboard
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_dashboard.ps1"

# Stop it
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\stop_dashboard.ps1"

# Run a backup now
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\backup.ps1"
```

## Where to find the backups

`C:\Users\Administrator\Meylon_Backups\billing_YYYY-MM-DD_HHmm\` — one folder per
run. The script keeps the most recent 30 days and automatically deletes older
folders.

Each backup folder contains:
- `data/` — every `.xlsx / .xls / .pdf / .csv` from the source tree
- `output/cache/` — the canonical `processed_data.parquet`, `income.parquet`, etc.
- `.streamlit/` — `secrets.toml` (gitignored), theme config
- `docs/` — generated audit reports
- `backup_meta.json` — timestamp, host, user, total size
