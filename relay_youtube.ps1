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
$overridesPath = Join-Path $PSScriptRoot "config/overrides.json"
$overlayFile   = Join-Path $PSScriptRoot "logs/overlay_stats.txt"
$fontFile      = "C:/Windows/Fonts/arialbd.ttf"

function Get-Override {
    param([string]$Name, $Default)
    if (-not (Test-Path $overridesPath)) { return $Default }
    try {
        $data = Get-Content $overridesPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($data.PSObject.Properties.Name -contains $Name) {
            return $data.$Name
        }
    } catch {}
    return $Default
}

function Escape-DrawtextPath {
    param([string]$Path)
    # FFmpeg drawtext uses ':' as the option separator, so any colon in a Windows
    # path (e.g. "C:/...") must be escaped as "\:". Forward slashes are fine.
    return ($Path -replace '\\', '/') -replace ':', '\:'
}

Write-Host "YouTube relay starting. Ctrl+C to stop."

while ($true) {
    $restartHours   = [int](Get-Override 'stream_restart_hours' 0)
    if ($restartHours -lt 0) { $restartHours = 0 }
    $overlayEnabled = [bool](Get-Override 'stream_overlay_enabled' $false)

    $ts = Get-Date -Format 'HH:mm:ss'
    $ffmpegArgs = @('-i', $source)

    if ($overlayEnabled) {
        # Make sure the file exists so drawtext doesn't error on first read.
        if (-not (Test-Path $overlayFile)) {
            New-Item -Path $overlayFile -ItemType File -Force | Out-Null
            Set-Content -Path $overlayFile -Value "Visits 0  In 0  Out 0  Key 0  Last --:--" -Encoding UTF8
        }
        $textPath = Escape-DrawtextPath $overlayFile
        $fontPath = Escape-DrawtextPath $fontFile
        $drawtext = "drawtext=textfile=${textPath}:reload=1:fontfile=${fontPath}:fontsize=24:fontcolor=black:bordercolor=white:borderw=2:x=20:y=20"
        $ffmpegArgs += @(
            '-vf', $drawtext,
            '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
            '-pix_fmt', 'yuv420p', '-b:v', '4500k', '-maxrate', '4500k',
            '-bufsize', '9000k', '-g', '60',
            '-c:a', 'aac', '-ar', '44100', '-b:a', '128k'
        )
    } else {
        $ffmpegArgs += @('-c:v', 'copy', '-c:a', 'aac', '-ar', '44100', '-b:a', '128k')
    }

    $modeMsg = if ($overlayEnabled) { 'overlay re-encode' } else { 'stream copy' }
    if ($restartHours -gt 0) {
        $ffmpegArgs += @('-t', ($restartHours * 3600))
        Write-Host "$ts Connecting to $source ($modeMsg, auto-restart every ${restartHours}h) ..."
    } else {
        Write-Host "$ts Connecting to $source ($modeMsg) ..."
    }
    $ffmpegArgs += @('-f', 'flv', $dest)
    & $ffmpeg @ffmpegArgs
    $exitCode = $LASTEXITCODE
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "$ts FFmpeg exited with code $exitCode. Restarting in 2s..."
    Start-Sleep -Seconds 2
}
