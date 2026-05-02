# ============================================================
# Birdify - Start All Services
# ============================================================
# Launches mediamtx, the AI pipeline, the dashboard, and the
# YouTube relay in separate PowerShell windows.
#
# Usage:  .\start.ps1
# Stop:   .\stop.ps1  (or close the windows / Ctrl+C in each)
# ============================================================

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  === Birdify AI Stream Processor ===" -ForegroundColor Cyan
Write-Host ""

# --- Kill any leftover processes from a previous run ---------
Write-Host "Cleaning up old processes..." -ForegroundColor DarkGray
Get-Process -Name "mediamtx" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "main\.py|dashboard\.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# Kill any lingering ffmpeg relay processes
Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "rtmp://a\.rtmp\.youtube\.com" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# --- 1. MediaMTX (RTMP relay) --------------------------------
Write-Host "[1/4] Starting MediaMTX..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Birdify - MediaMTX'; Set-Location '$projectDir'; Write-Host '=== MediaMTX RTMP Server ===' -ForegroundColor Cyan; .\mediamtx.exe mediamtx.yml"
)

Start-Sleep -Seconds 3  # Give mediamtx time to bind all ports

# --- 2. Main AI Pipeline ------------------------------------
Write-Host "[2/4] Starting AI Pipeline..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Birdify - AI Pipeline'; Set-Location '$projectDir'; & .\venv\Scripts\activate.ps1; Write-Host '=== Birdify AI Pipeline ===' -ForegroundColor Cyan; python main.py"
)

Start-Sleep -Seconds 2

# --- 3. Dashboard --------------------------------------------
Write-Host "[3/4] Starting Dashboard..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'Birdify - Dashboard'; Set-Location '$projectDir'; & .\venv\Scripts\activate.ps1; Write-Host '=== Birdify Dashboard ===' -ForegroundColor Cyan; python dashboard.py"
)

Start-Sleep -Seconds 1

# --- 4. YouTube Relay ----------------------------------------
# Read YOUTUBE_ENABLED from .env
$ytEnabled = $true
$envFile = Join-Path $projectDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*YOUTUBE_ENABLED\s*=' } | ForEach-Object {
        $val = ($_ -split '=', 2)[1].Trim().ToLower()
        if ($val -eq "false") { $ytEnabled = $false }
    }
}

if ($ytEnabled) {
    Write-Host "[4/4] Starting YouTube Relay..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "`$Host.UI.RawUI.WindowTitle = 'Birdify - YouTube Relay'; Set-Location '$projectDir'; Write-Host '=== YouTube Live Relay ===' -ForegroundColor Cyan; & .\relay_youtube.ps1"
    )
} else {
    Write-Host "[4/4] YouTube Relay skipped (YOUTUBE_ENABLED=false)" -ForegroundColor DarkGray
}

Start-Sleep -Seconds 2

# --- 5. Open dashboard in browser ----------------------------
Write-Host ""
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:5000" -ForegroundColor Green
Write-Host ""
Start-Process "http://localhost:5000"
