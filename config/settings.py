from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import json
import os
from dotenv import load_dotenv

load_dotenv()

_OVERRIDES_PATH = Path("config/overrides.json")
_TUNING_KEYS = (
    "motion_threshold",
    "motion_min_area",
    "motion_min_area_nest",
    "entrance_zone_bottom",
    "stream_restart_hours",
)

# Great Tit phenology used for auto-stage transitions when hatch_date is set.
# Day 0 = hatch day. Negative = pre-hatch.
_STAGE_BOUNDARIES = [
    (-30, "nest_building"),
    (-20, "egg_laying"),
    (-14, "incubation"),
    (0,   "nestling"),
    (16,  "fledging"),
    (24,  "empty"),
]

# Per-stage AI cooldowns: (day_seconds, night_seconds).
# Day = 06:00–22:00 local, night otherwise.
_STAGE_COOLDOWNS = {
    "nest_building": (300, 3600),
    "egg_laying":    (300, 3600),
    "incubation":    (300, 3600),
    "nestling":      (120, 1800),  # parents feed every few minutes — sample more often
    "fledging":       (90, 1800),  # don't miss the actual fledge
    "empty":         (600, 3600),
}


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

    # Exclusion zones: list of [x1, y1, x2, y2] in frame fractions (0.0–1.0).
    # Contour centroids inside any zone are silently ignored.
    exclusion_zones: list = field(default_factory=list)

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

    # Nesting stage drives the AI prompt context, motion thresholds and cooldowns.
    # If hatch_date is set (YYYY-MM-DD via NEST_HATCH_DATE env var), the stage is
    # derived automatically from days since hatch and this manual value is ignored.
    nesting_stage: str = "nestling"
    hatch_date: str = field(
        default_factory=lambda: os.environ.get("NEST_HATCH_DATE", "")
    )

    # Fledge-watch: during the 'fledging' stage we lower thresholds so we don't
    # miss the actual fledge moment. Multipliers applied to the base values.
    fledge_threshold_multiplier: float = 0.6
    fledge_min_area_multiplier: float = 0.5

    # Chick-count estimator: re-uses Gemini to count visible chicks when mum
    # leaves the nest (i.e. an entrance event with direction=leaving).
    chick_count_enabled: bool = True

    # Daily summary: posts a Gemini-generated recap to YouTube live chat.
    daily_summary_enabled: bool = True
    daily_summary_hour: int = 21  # local time, 24h

    # Silent alarm: warn if no key moment seen for this many minutes during
    # daylight while in the nestling stage.
    silent_alarm_minutes: int = 90
    silent_alarm_enabled: bool = True

    # Hourly stats post to YouTube live chat. Reports today's "feeds"
    # (count of entering events — see monitoring/hourly_stats.py for why
    # this is a more reliable count than AI-confirmed key moments).
    hourly_stats_enabled: bool = True
    hourly_stats_interval_minutes: int = 60

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

    # Auto-restart the YouTube relay (FFmpeg) every N hours. 0 = disabled.
    # The relay reads this from config/overrides.json on each iteration.
    stream_restart_hours: int = 0


settings = Settings()


def current_stage() -> str:
    """Resolve the active nesting stage.

    If `hatch_date` is set we derive it from days since hatch using
    standard Great Tit phenology; otherwise fall back to the manual
    `nesting_stage` setting.
    """
    if settings.hatch_date:
        try:
            hatch = date.fromisoformat(settings.hatch_date)
            days = (date.today() - hatch).days
            stage = _STAGE_BOUNDARIES[0][1]
            for boundary, name in _STAGE_BOUNDARIES:
                if days >= boundary:
                    stage = name
            return stage
        except ValueError:
            pass
    return settings.nesting_stage


def cooldown_seconds(now: datetime | None = None) -> int:
    """AI cooldown for the current stage and time of day."""
    now = now or datetime.now()
    is_night = now.hour >= 22 or now.hour <= 5
    day_cd, night_cd = _STAGE_COOLDOWNS.get(current_stage(), (300, 3600))
    return night_cd if is_night else day_cd


def effective_motion_thresholds() -> tuple[int, int, int]:
    """Returns (threshold, min_area_entrance, min_area_nest) for the current stage.
    During fledging we lower everything so wing-flaps near the entrance trigger."""
    threshold = settings.motion_threshold
    min_area = settings.motion_min_area
    min_area_nest = settings.motion_min_area_nest
    if current_stage() == "fledging":
        threshold = max(5, int(threshold * settings.fledge_threshold_multiplier))
        min_area = max(50, int(min_area * settings.fledge_min_area_multiplier))
        min_area_nest = max(500, int(min_area_nest * settings.fledge_min_area_multiplier))
    return threshold, min_area, min_area_nest


def load_overrides() -> None:
    """Apply saved tuning + exclusion zones from config/overrides.json (if it exists)."""
    if not _OVERRIDES_PATH.exists():
        return
    try:
        data = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
        for key in _TUNING_KEYS:
            if key in data:
                setattr(settings, key, type(getattr(settings, key))(data[key]))
        if "exclusion_zones" in data:
            settings.exclusion_zones = data["exclusion_zones"]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not load overrides: %s", exc)


def save_overrides() -> None:
    """Persist current tuning + exclusion zones to config/overrides.json."""
    data = {key: getattr(settings, key) for key in _TUNING_KEYS}
    data["exclusion_zones"] = settings.exclusion_zones
    try:
        _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not save overrides: %s", exc)
