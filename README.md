# Birdify AI Stream Processor

An automated pipeline that monitors a Birdify RTMP stream, uses **Google Gemini Vision AI** to detect exciting events ("Key Moments" like feeding and hatching), automatically records clips to Google Drive, and posts to a YouTube Live Chat.

## Prerequisites
Before you begin, you must have the following installed:
1. **Python 3.10+** (Make sure to check "Add Python to PATH" during installation).
2. **MediaMTX:** Download the latest release from [bluenviron/mediamtx](https://github.com/bluenviron/mediamtx/releases) and place the `mediamtx.exe` file directly in this `birdify` directory.
3. **FFmpeg:** Download a full Windows build of FFmpeg from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or use `winget install ffmpeg`. Ensure `ffmpeg.exe` is in your system PATH (or installed to `C:\ffmpeg\bin\ffmpeg.exe`).

## Installation & Setup

You MUST run these commands in PowerShell inside the `birdify` folder to set up the environment and install dependencies like Flask and the Google API client:

```powershell
# 1. Create a virtual environment
python -m venv venv

# 2. Activate the virtual environment
.\venv\Scripts\activate

# 3. Install all required dependencies
pip install -r requirements.txt
```

## API Keys & Authentication
1. **Google Gemini:** Get an API key from Google AI Studio. Open `.env.example`, rename it to `.env`, and add `GEMINI_API_KEY=your_key`.
2. **Google Drive:** Run `python auth_drive.py` once to authenticate.
3. **YouTube:** Run `python auth_youtube.py` once to authenticate your channel.

## Nest Configuration (optional but recommended)

Add these to your `.env` for richer chat responses and automatic stage tracking:

```
NEST_HATCH_DATE=YYYY-MM-DD   # e.g. 2026-05-01 — drives stage transitions and chick age display
```

You can also tune egg and death counts in `config/settings.py`:
```python
eggs_total = 7          # total eggs laid
known_chick_deaths = 0  # bump this manually when a death is confirmed
```

## Quick Start (Automated)

You can launch all services (MediaMTX, AI Pipeline, Dashboard, and YouTube Relay) in one go using the provided scripts:

```powershell
# To start everything:
.\start.ps1

# To stop everything:
.\stop.ps1

# To hot-reload main.py + dashboard.py while the live stream stays up
# (mediamtx and the YouTube relay are not touched):
.\reload.ps1
```

## How to Run (Manual)

If you prefer separate terminals, you will need four PowerShell windows (make sure to run `.\venv\Scripts\activate` in the ones running python scripts!).

**Terminal 1: MediaMTX (RTMP Server)**
```powershell
.\mediamtx.exe mediamtx.yml
```
*(Configure your camera to stream to `rtmp://<your-local-ip>:1935/camera`)*

**Terminal 2: AI Pipeline**
```powershell
.\venv\Scripts\activate
python main.py
```

**Terminal 3: Dashboard**
```powershell
.\venv\Scripts\activate
python dashboard.py
```
*Open http://localhost:5000 to monitor activity.*

**Terminal 4: YouTube Relay**
```powershell
.\relay_youtube.ps1
```

## Key Features

- **Zone-Based Detection:** High sensitivity for the entrance (top 12%) and low sensitivity for the nest (bottom 88%) to ignore fidgeting while catching every arrival.
- **Intruder Alert:** Uses Gemini Vision AI to identify if a different species (like a sparrow or predator) has entered the box.
- **Direction Tracking:** Automatically detects if a bird is "Entering" or "Leaving" based on its movement path.
- **Chick Count Estimator:** When mum leaves the nest, Gemini re-runs on the latest frame and records how many chicks are visible.
- **Nesting Stage Awareness:** Set `NEST_HATCH_DATE` in `.env` and the system auto-transitions through Great Tit phenology (`incubation`, `nestling`, `fledging`, `empty`), adjusting the AI prompt and motion thresholds at each stage.
- **Fledge-Watch:** During the fledging window, motion thresholds drop automatically so the actual fledge moment isn't missed.
- **Activity Heatmap:** Hourly breakdown of nest activity visible on the local dashboard.
- **Daily Summary:** A Gemini-generated recap of the day's activity is posted to the live chat each evening.
- **Silent Alarm:** Warns in chat if no Key Moment has been seen for a configurable number of minutes during daylight.
- **Automated YouTube Highlights:** Posts real-time chapter markers and chat comments for "Key Moments" (feeding, hatching, etc.).
- **Live Motion Tuning:** A debug UI at `http://localhost:5001` shows the live MJPEG feed with motion overlays and lets you drag exclusion zones and tune thresholds without restarting.
- **Auto-Restart Watchdog:** A numeric input on the dashboard (`http://localhost:5000`) sets how often to recycle the YouTube relay (0 = disabled, max 24h) — useful as a defensive reset for long-running streams.
- **Hourly Chat Update:** A one-line summary posts to YouTube live chat each hour: feeds today (counted from "entering" motion events, which catches every visit even when the AI cooldown skipped one), AI-confirmed key moments, last visit time, current nesting stage, and how the last hour compares to today's running average.
- **Daily Milestone Celebrations:** Posts a one-liner to chat each time today's feed count crosses 25 / 50 / 100 / 150 / 200 / 250 / 300. Resets at midnight, restart-safe (won't re-fire thresholds already passed).
- **Conversational Chat Replies:** Reads viewer messages and posts a Gemini-generated one-line reply to nest-related questions or @-mentions of the bot. Rate-limited (default 30 replies/day, 60s between replies) and quota-aware. Replies are enriched with chick age, last AI chick count, egg total, known deaths, and a curated biology facts block — so questions about poo, hygiene, lifespan, and chick development get properly informed answers.
- **Great Tit Facts Poster:** Every 90 minutes a rotating "Did you know?" fact is posted to chat, covering fecal sacs (poo parcels the parents carry away), feeding rates (400–1,000 trips/day!), lifespan, chick development milestones, nest building, and more. 28 facts in the rotation before repeating.
- **ALERT Flood Suppression:** If Gemini flags the same welfare concern (e.g. a displaced chick) repeatedly, the application compares the text to the last posted ALERT using word-overlap similarity. Duplicate ALERTs within a 2-hour window are silently suppressed, keeping chat readable.
- **Chick Age in Hourly Update:** When `NEST_HATCH_DATE` is set in `.env`, the hourly stats line now includes "Day N since hatch" so viewers always know where the chicks are in their development.

## Testing Without Spamming Subscribers
If you want to test the bot without notifying your YouTube followers:
1. Start the stream in YouTube Studio.
2. Set Visibility to **Unlisted**.
3. Under stream settings, **uncheck "Notify subscribers"**.
The AI will successfully post its messages to the hidden live chat!

## Caveats & Limitations

A few things worth knowing before you leave this running unattended:

- **YouTube broadcast cap.** YouTube ends a single live broadcast after ~12 hours by default for most accounts. The auto-restart watchdog described above only recycles the *FFmpeg ingest connection* — it keeps the same broadcast/VOD alive. If you want a fresh broadcast (and a fresh VOD) every N hours, that needs the YouTube Data API to create and bind a new `liveBroadcast` programmatically, which is **not** implemented yet.
- **YouTube ends the broadcast after ~60s of no incoming video.** The relay's restart loop sleeps for 2s between FFmpeg runs, so brief blips are fine. But if your camera or network is offline for more than a minute, YouTube tears the broadcast down and the relay's reconnects will land on a dead ingest. You'll need to start a new broadcast in YouTube Studio.
- **PC crashes are not handled by this app.** If Windows itself crashes or reboots, nothing inside this repo can bring it back up. To recover automatically, register `start.ps1` as a Windows Scheduled Task with trigger "At log on" (and enable auto-login if the machine reboots overnight). Disable Windows sleep, USB selective suspend, and Wi-Fi power saving — those are the most common causes of an apparently-fine PC quietly dropping the stream after a few hours.
- **Live Chat quota is 200 messages/day.** `YouTubeChapters` enforces this hard cap. Once hit, no further Key Moment comments or daily summary will post until the quota resets at midnight Pacific time.
- **Stream encoding is `-c copy`.** The pipeline never re-encodes the camera feed, which keeps CPU low but means anything that needs pixel-level changes (e.g. burning stat overlays into the video that goes to YouTube) requires changing this and accepting the CPU cost.
- **"Feeds today" is counted from entering events, not AI calls.** Motion detection runs on every frame, but Gemini is gated by a stage-aware cooldown — so most visits never reach the AI. The hourly chat post uses the entering-event count (reliable) for "feeds today" and shows the AI-confirmed count separately.
- **Gemini API costs scale with motion.** Cooldowns protect against runaway costs, but a very busy nest in `nestling` stage (120s daytime cooldown) can still make ~30 calls/hour. Check your Google AI billing if you leave this running for weeks.
