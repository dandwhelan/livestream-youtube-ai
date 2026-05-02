# CLAUDE.md / GEMINI.md / CODEX.md

This file provides guidance to AI assistants when working with code in this repository.

## Running

**Quick start (launches all 4 services in separate windows):**
```
.\start.ps1
```

**Stop everything:**
```
.\stop.ps1
```

**Manual start (if you prefer separate terminals):**

**Terminal 1 — mediamtx (RTMP relay + YouTube restream):**
```
.\mediamtx.exe mediamtx.yml
```

**Terminal 2 — Python processor:**
```
venv\Scripts\activate
python main.py
```

**Terminal 3 — Dashboard:**
```
venv\Scripts\activate
python dashboard.py
```

**Terminal 4 — YouTube relay (reads .env for stream key):**
```
.\relay_youtube.ps1
```

**One-time Auth Setup (run once in browser-capable terminal):**
```
python auth_drive.py
python auth_youtube.py
```

## Architecture

The pipeline is linear with callbacks wiring the stages:

```
RTMP camera → StreamReader (background thread)
                  ↓ frame callback (every frame)
             MotionDetector
                  ↓ on_motion_start / on_motion_end callbacks
             BirdDescriber (Google Gemini 2.5 Flash Vision API)
                  ↓ determines if it is a 'Key Moment'
             ClipExtractor (FFmpeg subprocess, rtmp://localhost:1935/analysis)
             YouTubeChapters (Posts to YouTube Live Chat if Key Moment)
             ActivityLog (JSON file, logs/activity_log.json)
             DriveUploader (background upload queue, routes Key Moments to special folder)
```

**Key design decisions:**

- `BirdDescriber` now uses the Google Gemini API to analyze frames and detect `is_key_moment`.
- `MotionDetector` uses **zone-based detection**: the entrance (top 12% of frame) is very sensitive to catch arrivals/departures, while the nest zone (bottom 88%) requires much larger movement to ignore the mum fidgeting.
- `MotionDetector` has a dynamic day/night cooldown to heavily protect the YouTube Live Chat Quota limit (200 msgs/day). During the day, it limits AI calls to every 5 minutes. At night, every 1 hour.
- `ClipExtractor` uses `-c copy` (stream copy) to preserve original camera quality without re-encoding.
- `DriveUploader` uses OAuth2 user credentials stored in `credentials/drive_token.json`. Key moments are routed to `BirdBox/Key Moments/`.
- `YouTubeChapters` extracts the `liveChatId` and pushes comments directly to the active live stream if the event is a Key Moment, while respecting a strict 200 message/day cutoff.

## Configuration

All tunable knobs live in `config/settings.py`. Env vars are loaded from `.env` via `python-dotenv`.

Key settings:
| Setting | Effect |
|---|---|
| `motion_threshold` | Pixel diff sensitivity (default: 30) |
| `motion_min_area` | Min contour area for entrance zone (default: 500) |
| `motion_min_area_nest` | Min contour area for nest zone — higher to ignore fidgeting (default: 15000) |
| `entrance_zone_bottom` | Fraction of frame height that counts as entrance (default: 0.12 = top 12%) |
| `gemini_model` | The Gemini model used (e.g. gemini-2.5-flash) |
| `drive_key_moments_subfolder` | Drive folder name for important clips |

## Outputs

- `clips_output/` — MP4 clips
- `snapshots/` — JPEG snapshots of nest activity
- `logs/activity_log.json` — append-only JSON array of all events, `is_key_moment`, and Drive URLs
- `logs/birdbox.log` — rotating app log
- Google Drive: `BirdBox/clips/` and `BirdBox/Key Moments/`
