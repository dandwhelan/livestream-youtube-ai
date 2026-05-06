# Persistent YouTube relay - loops forever, restarts FFmpeg on exit.
# Run AFTER mediamtx is up. Reads from analysis path (always available).
# Usage: .\relay_youtube.ps1

param([string]$StreamKey = $env:YOUTUBE_STREAM_KEY)

if (-not $StreamKey) {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | Where-Object { $_ -match '^\s*YOUTUBE_STREAM_KEY\s*=' } | ForEach-Object {
            $StreamKey = ($_ -split '=', 2)[1].Trim()
        }
    }
}

if (-not $StreamKey) {
    Write-Error "YOUTUBE_STREAM_KEY not set. Set it in .env or pass -StreamKey xxxx"
    exit 1
}

# Check YOUTUBE_ENABLED flag
$enabled = $true
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*YOUTUBE_ENABLED\s*=' } | ForEach-Object {
        $val = ($_ -split '=', 2)[1].Trim().ToLower()
        if ($val -eq "false") { $enabled = $false }
    }
}
if (-not $enabled) {
    Write-Host "YOUTUBE_ENABLED=false in .env - relay disabled."
    exit 0
}

$ffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"
$source = "rtmp://localhost:1935/analysis"
$dest   = "rtmp://a.rtmp.youtube.com/live2/$StreamKey"

Write-Host "YouTube relay starting. Ctrl+C to stop."

while ($true) {
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "$ts Connecting to $source ..."
    & $ffmpeg `
        -i $source `
        -c:v copy -c:a aac -ar 44100 -b:a 128k `
        -f flv $dest
    $exitCode = $LASTEXITCODE
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "$ts FFmpeg exited with code $exitCode. Restarting in 2s..."
    Start-Sleep -Seconds 2
}
