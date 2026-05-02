# Birdify AI Stream Processor

An automated pipeline that monitors a Birdify RTMP stream, uses **Google Gemini Vision AI** to detect exciting events ("Key Moments" like feeding and hatching), automatically records clips to Google Drive, and posts to a YouTube Live Chat.

## Key Features
* **Zone-Based Motion Detection**: The entrance (top 12%) is highly sensitive to catch arrivals/departures, while the nest zone ignores mum's subtle movements to prevent false alarms.
* **Smart AI Intuder Detection**: Gemini Vision analyzes motion events to determine if a predator or different bird species entered the box.
* **Direction Tracking**: Automatically logs whether the bird is arriving (moving down) or leaving (moving up).
* **Automated Archival**: Extracts perfectly-timed clips using FFmpeg stream copy and uploads Key Moments to Google Drive.
* **Activity Dashboard**: A real-time web UI to monitor the camera, view hourly activity heatmaps, and see snapshot thumbnails of recent events.

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

## How to Run

**The Easy Way (Recommended):**
Just run the included launch script to start all services (MediaMTX, AI Pipeline, Dashboard, and YouTube Relay) in separate windows automatically:
```powershell
.\start.ps1
```
To shut everything down cleanly, run:
```powershell
.\stop.ps1
```

**Manual Start:**
If you prefer running them manually, open four separate PowerShell windows (remember to activate the virtual environment `.\venv\Scripts\activate` in the python ones):
1. `.\mediamtx.exe mediamtx.yml`
2. `python main.py`
3. `python dashboard.py` (then open http://localhost:5000)
4. `.\relay_youtube.ps1`

*(Configure your Birdify app to stream to `rtmp://<your-local-ip>:1935/camera`)*

## Testing Without Spamming Subscribers
If you want to test the bot without notifying your YouTube followers:
1. Start the stream in YouTube Studio.
2. Set Visibility to **Unlisted**.
3. Under stream settings, **uncheck "Notify subscribers"**.
The AI will successfully post its messages to the hidden live chat!
