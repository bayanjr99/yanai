# =============================================================================
# Backup script — runs daily via Windows Task Scheduler.
#
# Snapshots the critical source data and cache to a date-stamped folder under
# ~/Meylon_Backups/, then prunes anything older than 30 days.
#
# What it backs up:
#   data/                — all .xlsx, .xls, .pdf, .csv files (source of truth)
#   output/cache/        — processed parquet caches (rebuildable but expensive)
#   .streamlit/          — secrets + theme (gitignored)
#   docs/                — generated reports
#
# What it skips:
#   .git/                — already in source control
#   __pycache__/         — regenerable
#   .claude/worktrees/   — temporary
#   *.log                — noise
#
# To install as a daily 02:00 task:
#   schtasks /create /sc daily /st 02:00 /tn "BillingSystemBackup" `
#     /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File '<path>\backup.ps1'"
# =============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # speeds up Compress-Archive

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjRoot  = Split-Path -Parent $ScriptDir
$BackupRoot = Join-Path $HOME "Meylon_Backups"
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$Target = Join-Path $BackupRoot "billing_$Stamp"

# Create destination
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Write-Host "Backing up $ProjRoot -> $Target" -ForegroundColor Cyan

# What to back up (per-tree)
$Trees = @(
    @{ Source = "data";          Filter = @("*.xlsx", "*.xls", "*.pdf", "*.csv") },
    @{ Source = "output\cache";  Filter = @("*.parquet", "*.json", "*.xlsx") },
    @{ Source = ".streamlit";    Filter = @("*") },
    @{ Source = "docs";          Filter = @("*") }
)

$totalSize = 0
foreach ($t in $Trees) {
    $srcPath = Join-Path $ProjRoot $t.Source
    if (-not (Test-Path $srcPath)) { continue }

    $dstPath = Join-Path $Target $t.Source
    New-Item -ItemType Directory -Path $dstPath -Force | Out-Null

    $items = foreach ($pat in $t.Filter) {
        Get-ChildItem -Path $srcPath -Filter $pat -Recurse -File -ErrorAction SilentlyContinue
    }
    $items = $items | Where-Object {
        $_.FullName -notmatch '\\__pycache__\\' -and
        $_.FullName -notmatch '\\\.git\\' -and
        $_.Extension -ne ".log"
    }

    $count = 0
    foreach ($f in $items) {
        $rel  = $f.FullName.Substring($srcPath.Length).TrimStart('\')
        $dest = Join-Path $dstPath $rel
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item $f.FullName -Destination $dest -Force
        $count++
        $totalSize += $f.Length
    }
    Write-Host ("  {0}: {1} files" -f $t.Source, $count) -ForegroundColor DarkGray
}

# Write a metadata file with run info
$meta = @{
    timestamp    = (Get-Date).ToString("o")
    project_root = $ProjRoot
    host         = $env:COMPUTERNAME
    user         = $env:USERNAME
    total_bytes  = $totalSize
} | ConvertTo-Json
Set-Content -Path (Join-Path $Target "backup_meta.json") -Value $meta -Encoding UTF8

Write-Host ""
Write-Host ("OK - backup size: {0:N1} MB" -f ($totalSize / 1MB)) -ForegroundColor Green

# Prune backups older than 30 days
$cutoff = (Get-Date).AddDays(-30)
$old = Get-ChildItem -Path $BackupRoot -Directory -ErrorAction SilentlyContinue |
       Where-Object { $_.Name -match '^billing_\d{4}-\d{2}-\d{2}' -and $_.LastWriteTime -lt $cutoff }
foreach ($o in $old) {
    Write-Host "Pruning old backup: $($o.Name)" -ForegroundColor DarkYellow
    Remove-Item $o.FullName -Recurse -Force
}

Write-Host "Done." -ForegroundColor Green
