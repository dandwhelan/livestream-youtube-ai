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

## Quick Start (Automated)

You can launch all services (MediaMTX, AI Pipeline, Dashboard, and YouTube Relay) in one go using the provided scripts:

```powershell
# To start everything:
.\start.ps1

# To stop everything:
.\stop.ps1
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
- **Activity Heatmap:** Hourly breakdown of nest activity visible on the local dashboard.
- **Automated YouTube Highlights:** Posts real-time chapter markers and chat comments for "Key Moments" (feeding, hatching, etc.).

## Testing Without Spamming Subscribers
If you want to test the bot without notifying your YouTube followers:
1. Start the stream in YouTube Studio.
2. Set Visibility to **Unlisted**.
3. Under stream settings, **uncheck "Notify subscribers"**.
The AI will successfully post its messages to the hidden live chat!
