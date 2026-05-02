from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Stream URLs
    camera_rtmp_url: str = "rtmp://localhost:1935/analysis"

    # Motion detection
    motion_threshold: int = 30       # pixel diff threshold (0-255); tune per camera
    motion_min_area: int = 500       # minimum contour area in pixels (entrance zone)
    motion_min_area_nest: int = 15000 # minimum contour area for nest zone (ignore fidgeting)
    motion_cooldown_seconds: int = 30
    post_event_seconds: int = 10     # seconds of quiet before clip ends
    frame_buffer_seconds: int = 10   # pre-roll buffer depth

    # Motion zones (as fraction of frame height, 0.0 = top, 1.0 = bottom)
    entrance_zone_bottom: float = 0.12  # top 12% of frame is the entrance

    # Clips & snapshots
    clips_dir: Path = Path("clips_output")
    snapshots_dir: Path = Path("snapshots")
    max_clip_duration_seconds: int = 60  # max length per clip

    # Logging
    activity_log_path: Path = Path("logs/activity_log.json")
    app_log_path: Path = Path("logs/birdbox.log")

    # Gemini
    gemini_api_key: str = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY", "")
    )
    gemini_model: str = "gemini-2.5-flash"  # Using 2.5-flash as it is fast and cheap


    # Google Drive
    drive_credentials_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("GOOGLE_DRIVE_CREDENTIALS", "credentials/service_account.json")
        )
    )
    drive_folder_name: str = "BirdBox"
    drive_clips_subfolder: str = "clips"
    drive_key_moments_subfolder: str = "Key Moments"
    drive_log_sync_interval: int = 300  # sync activity log to Drive every N seconds

    # YouTube stream key (used by mediamtx shell env, not Python directly)
    youtube_stream_key: str = field(
        default_factory=lambda: os.environ.get("YOUTUBE_STREAM_KEY", "")
    )
    youtube_enabled: bool = field(
        default_factory=lambda: os.environ.get("YOUTUBE_ENABLED", "true").lower() == "true"
    )
    youtube_stream_title: str = "Great Tit Nest"


settings = Settings()
