# Stop the running dashboard (Streamlit + ngrok)
$ErrorActionPreference = "Continue"
$stopped = @()

Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | ForEach-Object {
    $stopped += "ngrok ($($_.Id))"
    Stop-Process -Id $_.Id -Force
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "streamlit" } |
    ForEach-Object {
        $stopped += "python/streamlit ($($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force
    }

if ($stopped.Count -eq 0) {
    Write-Host "Nothing was running." -ForegroundColor DarkGray
} else {
    Write-Host ("Stopped: " + ($stopped -join ", ")) -ForegroundColor Yellow
}
