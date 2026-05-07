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

**Hot-reload main.py + dashboard.py while the YouTube stream stays up:**
```
.\reload.ps1
```
Use after pulling code changes that don't touch `mediamtx.yml` or
`relay_youtube.ps1`. Restarts only the Python services; mediamtx and
the FFmpeg relay are left running, so YouTube viewers see no blip.

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

**Terminal 3 — Dashboard (http://localhost:5000):**
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
             MotionDetector  (zone-based: entrance vs nest)
                  ↓ on_motion_start / on_motion_end callbacks (dispatched off the reader thread)
             BirdDescriber    (Google Gemini 2.5 Flash Vision API)
                  ↓ determines is_key_moment, optional chick count
             ClipExtractor    (FFmpeg subprocess, rtmp://localhost:1935/analysis, -c copy)
             YouTubeChapters  (posts comments to live chat for Key Moments)
             ActivityLog      (logs/activity_log.json)
             DriveUploader    (background queue; Key Moments routed to a special folder)

Sidecar services started by main.py:
  - DailySummary  → end-of-day Gemini recap posted to YouTube live chat
  - SilentAlarm   → warns to chat if no Key Moment seen for N minutes during daylight
  - HourlyStats   → posts a one-line "Hourly update" to YouTube chat each hour with feeds/AI-confirmed/last-visit/stage/chick age
  - MilestoneAnnouncer → posts a celebration to chat when today's feed count crosses 25/50/100/150/200/250/300
  - ChatResponder → reads viewer chat, replies to nest-related questions or @-mentions with a Gemini one-liner
  - FactsPoster   → posts a rotating "Did you know?" Great Tit fact every 90 minutes (biology, poo, lifespan, development)
  - debug_server  → motion-tuning UI on http://localhost:5001 (live MJPEG + sliders + exclusion zones)
```

**Key design decisions:**

- `BirdDescriber` uses Google Gemini to analyze frames and detect `is_key_moment`, plus a `count_chicks` call when mum leaves the nest.
- `BirdDescriber` has **application-level ALERT dedup**: if Gemini returns an ALERT whose content is ≥65% similar (Jaccard word overlap) to the last ALERT within 2 hours, it is silently suppressed and not posted. This prevents the same welfare concern flooding chat on every motion event.
- `MotionDetector` uses **zone-based detection**: the entrance (top 12% of frame) is very sensitive to catch arrivals/departures, while the nest zone (bottom 88%) requires much larger movement to ignore the mum fidgeting. Motion callbacks are dispatched on a background thread so the reader never falls behind.
- `MotionDetector` has a **stage- and time-of-day-aware cooldown** to protect the YouTube Live Chat 200 msgs/day quota. Cooldowns are looked up by current nesting stage in `_STAGE_COOLDOWNS` (e.g. `nestling` = 120s day / 1800s night, `incubation` = 300s / 3600s).
- **Nesting stage** is auto-derived from `NEST_HATCH_DATE` (env var, `YYYY-MM-DD`) using Great Tit phenology in `_STAGE_BOUNDARIES`. If unset, falls back to `settings.nesting_stage`. Stage drives both cooldowns and the AI prompt context.
- **ChatResponder** passes enriched stats to Gemini when answering viewer questions: chick age in days, last AI chick count, total eggs laid, and known chick deaths — plus a curated poo/biology facts block so answers about hygiene, lifespan, and development are properly informed.
- **Fledge-watch**: during the `fledging` stage, motion thresholds and minimum areas are multiplied down (`fledge_threshold_multiplier`, `fledge_min_area_multiplier`) so wing-flaps near the entrance still trigger.
- **Exclusion zones** (frame-fraction rectangles) silently drop contour centroids that land inside them. Tunable live via the debug server.
- `ClipExtractor` uses `-c copy` (stream copy) to preserve original camera quality without re-encoding.
- `DriveUploader` uses OAuth2 user credentials stored in `credentials/drive_token.json`. Key moments are routed to `BirdBox/Key Moments/`.
- `YouTubeChapters` extracts the `liveChatId` and pushes comments directly to the active live stream if the event is a Key Moment, while respecting a strict 200 message/day cutoff.
- `relay_youtube.ps1` runs FFmpeg in a forever loop and re-reads `config/overrides.json` on every restart, so live tuning changes (including `stream_restart_hours`) take effect on the next cycle.

## Configuration

All tunable knobs live in `config/settings.py`. Env vars are loaded from `.env` via `python-dotenv`. Live-tunable values are persisted to `config/overrides.json` (see `_TUNING_KEYS`).

Key settings:
| Setting | Effect |
|---|---|
| `motion_threshold` | Pixel diff sensitivity (default: 30) |
| `motion_min_area` | Min contour area for entrance zone (default: 500) |
| `motion_min_area_nest` | Min contour area for nest zone — higher to ignore fidgeting (default: 15000) |
| `entrance_zone_bottom` | Fraction of frame height that counts as entrance (default: 0.12 = top 12%) |
| `exclusion_zones` | List of `[x1,y1,x2,y2]` frame-fraction rects to ignore |
| `gemini_model` | The Gemini model used (e.g. `gemini-2.5-flash`) |
| `nesting_stage` / `NEST_HATCH_DATE` | Manual stage; or auto-derived from hatch date |
| `chick_count_enabled` | Re-prompt Gemini for chick count when mum leaves |
| `daily_summary_enabled` / `daily_summary_hour` | End-of-day recap to live chat |
| `silent_alarm_enabled` / `silent_alarm_minutes` | Warn if no Key Moment for N minutes during daylight |
| `hourly_stats_enabled` / `hourly_stats_interval_minutes` | Post a one-line stats update to YouTube live chat every N minutes (default 60). |
| `daily_milestones_enabled` | Celebrate in chat when today's feed count crosses thresholds (25/50/100/150/200/250/300). On main.py restart, already-passed thresholds are seeded as "announced" so they don't all fire at once. |
| `chat_responder_enabled` / `chat_responder_poll_seconds` / `chat_responder_max_per_day` / `chat_responder_min_seconds_between_responses` | Read viewer chat and reply via Gemini to nest-related questions or @-mentions. Default poll 60s, max 30 replies/day, 60s minimum between replies. Uses YouTube Data API quota for reads — bump the poll interval up if quota is tight. |
| `stream_restart_hours` | Auto-restart the YouTube relay every N hours (0 = off, max 24). Editable from the dashboard. |
| `drive_key_moments_subfolder` | Drive folder name for important clips |
| `eggs_total` | Total eggs laid at the start of the clutch (default: 7). Shown in chat responses to give context. |
| `known_chick_deaths` | Manually bumped when a chick death is confirmed; included in chat reply context. |
| `facts_poster_enabled` / `facts_poster_interval_minutes` | Post a rotating "Did you know?" Great Tit fact to chat every N minutes (default 90). 28 facts covering poo/fecal sacs, feeding rates, lifespan, chick development, and more. |

Live tuning UIs:
- **Dashboard** (`http://localhost:5000`) — activity log, heatmap, HLS preview, `stream_restart_hours` input.
- **Motion debug server** (`http://localhost:5001`) — sliders for thresholds, MJPEG preview with motion overlays, draw/delete exclusion zones.

## Outputs

- `clips_output/` — MP4 clips (per-event)
- `snapshots/` — JPEG snapshots taken at the start of each motion event
- `logs/activity_log.json` — append-only JSON array of all events, `is_key_moment`, `motion_zone`, `motion_direction`, `chick_count`, and Drive URLs
- `logs/birdbox.log` — rotating app log
- `config/overrides.json` — persisted live-tuning values
- Google Drive: `BirdBox/clips/` and `BirdBox/Key Moments/`
