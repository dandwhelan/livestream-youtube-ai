# ============================================================
# Birdify - Hot-reload AI Pipeline + Dashboard
# ============================================================
# Restarts main.py and dashboard.py only. Does NOT touch
# mediamtx or the YouTube relay, so the live YouTube stream
# stays up across the reload.
#
# Use this after pulling code changes to:
#   - main.py / config / monitoring / storage / ai / stream / clips
#   - dashboard.py
#
# If you changed mediamtx.yml or relay_youtube.ps1, use the
# normal start.ps1 / stop.ps1 instead - those changes need a
# full restart and will cause a YouTube blip.
#
# Usage:  .\reload.ps1
# ============================================================

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  === Birdify hot-reload (stream stays up) ===" -ForegroundColor Cyan
Write-Host ""

# --- Sanity check: is the stream actually running? ----------
$mediamtxRunning = $null -ne (Get-Process -Name "mediamtx" -ErrorAction SilentlyContinue)
$relayRunning    = $null -ne (Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "rtmp://a\.rtmp\.youtube\.com" })

if (-not $mediamtxRunning) {
    Write-Host "Warning: mediamtx is not running. This script is for hot-reload while" -ForegroundColor Yellow
    Write-Host "         the stream is live. If you're starting from cold, use start.ps1." -ForegroundColor Yellow
    Write-Host ""
}
if (-not $relayRunning) {
    Write-Host "Warning: YouTube relay (ffmpeg) is not running." -ForegroundColor Yellow
    Write-Host ""
}

# --- Stop only the AI Pipeline + Dashboard ------------------
Write-Host "Stopping main.py and dashboard.py..." -ForegroundColor DarkGray

# Kill the python processes by command line match (precise: only main.py / dashboard.py).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "main\.py|dashboard\.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Close the old PowerShell host windows that were running them, so we don't
# pile up dead windows on every reload. Match by title - never touches the
# MediaMTX or YouTube Relay windows.
Get-Process powershell -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match "^Birdify - (AI Pipeline|Dashboard)$" } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 1

# --- Start AI Pipeline --------------------------------------
Write-Host "[1/2] Starting AI Pipeline..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Birdify - AI Pipeline'; Set-Location '$projectDir'; & .\venv\Scripts\activate.ps1; Write-Host '=== Birdify AI Pipeline ===' -ForegroundColor Cyan; python main.py"
)

Start-Sleep -Seconds 2

# --- Start Dashboard ----------------------------------------
Write-Host "[2/2] Starting Dashboard..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Birdify - Dashboard'; Set-Location '$projectDir'; & .\venv\Scripts\activate.ps1; Write-Host '=== Birdify Dashboard ===' -ForegroundColor Cyan; python dashboard.py"
)

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "  Reload complete." -ForegroundColor Green
Write-Host "  mediamtx + YouTube relay were left untouched." -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:5000" -ForegroundColor Green
Write-Host ""
