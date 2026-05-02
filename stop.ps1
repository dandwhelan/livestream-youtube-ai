# ============================================================
# Birdify - Stop All Services
# ============================================================
# Kills mediamtx, main.py, and dashboard.py processes.
#
# Usage:  .\stop.ps1
# ============================================================

Write-Host ""
Write-Host "  Stopping Birdify services..." -ForegroundColor Yellow
Write-Host ""

Get-Process -Name "mediamtx" -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "  [x] MediaMTX stopped" -ForegroundColor Red

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "main\.py|dashboard\.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "  [x] AI Pipeline stopped" -ForegroundColor Red
Write-Host "  [x] Dashboard stopped" -ForegroundColor Red

Write-Host ""
Write-Host "  All services stopped." -ForegroundColor Green
Write-Host ""
