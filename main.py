"""
Bird Box Stream Processor
=========================
Receives RTMP from Birdify camera via mediamtx, detects motion,
describes activity with Google Gemini, clips events, uploads to Google Drive.

Run after starting mediamtx:
  python main.py

Requirements: see requirements.txt
Prerequisites: see README / mediamtx.yml comments
"""

import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from config.settings import settings
from stream.reader import StreamReader
from stream.motion import MotionDetector
from ai.describer import BirdDescriber
from clips.extractor import ClipExtractor
from storage.activity_log import ActivityLog
from storage.drive_uploader import DriveUploader
from storage.youtube_chapters import YouTubeChapters


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    settings.app_log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        settings.app_log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global component instances (wired in main())
# ---------------------------------------------------------------------------

_reader: StreamReader | None = None
_extractor: ClipExtractor | None = None
_describer: BirdDescriber | None = None
_activity_log: ActivityLog | None = None
_drive_uploader: DriveUploader | None = None
_youtube_chapters: YouTubeChapters | None = None

# Tracks the current event so on_motion_end can update the log entry
_current_entry_id: str | None = None
_current_clip_path: Path | None = None
_current_is_key_moment: bool = False
_current_motion_info: dict | None = None


# ---------------------------------------------------------------------------
# Motion callbacks
# ---------------------------------------------------------------------------

def on_motion_start(frame, timestamp: datetime, buffer_snapshot: list, motion_info: dict = None) -> None:
    """
    Called by MotionDetector on first motion frame (rate-limited).
    Blocks ~5-15s for AI inference — intentional; acts as cooldown between events.
    FFmpeg clip recording starts AFTER AI call so the description is ready when
    we create the log entry.
    """
    global _current_entry_id, _current_clip_path, _current_is_key_moment, _current_motion_info

    if motion_info is None:
        motion_info = {}

    zone = motion_info.get("zone", "unknown")
    _current_motion_info = motion_info

    logger.info("=== Motion event started at %s (zone: %s) ===", timestamp.isoformat(), zone)

    # 1. Get AI description (blocking ~10s) — AI also detects intruders
    description, is_key_moment, is_intruder = _describer.describe_frame(frame)
    if description:
        logger.info("Activity: %s", description)
    else:
        logger.info("AI description unavailable")

    if is_intruder:
        logger.warning("🚨 INTRUDER DETECTED — different bird or animal in the box!")

    # 2. Save snapshot (useful when mum moves — eggs may be visible)
    snapshot_path = None
    try:
        import cv2
        settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
        snap_name = timestamp.strftime("%Y-%m-%d_%H-%M-%S") + ".jpg"
        snapshot_path = settings.snapshots_dir / snap_name
        cv2.imwrite(str(snapshot_path), frame)
        logger.info("Snapshot saved: %s", snapshot_path)
    except Exception:
        logger.exception("Failed to save snapshot")

    # 3. Start clip recording
    try:
        clip_path = _extractor.start_clip(timestamp)
        _current_clip_path = clip_path
    except Exception:
        logger.exception("Failed to start clip recording")
        _current_clip_path = None
        clip_path = None

    # 4. Add YouTube chapter marker
    if description:
        _youtube_chapters.add_chapter(timestamp, description, is_key_moment)

    # 5. Create activity log entry
    _current_entry_id = _activity_log.append_event(
        event_start=timestamp,
        ai_description=description,
        clip_path=clip_path,
        is_key_moment=is_key_moment,
        motion_zone=zone,
        is_intruder=is_intruder,
        snapshot_path=snapshot_path,
    )
    _current_is_key_moment = is_key_moment


def on_motion_end(last_timestamp: datetime, motion_info: dict = None) -> None:
    """
    Called by MotionDetector after post_event_seconds of quiet.
    Stops clip recording and queues upload.
    """
    global _current_entry_id, _current_clip_path, _current_is_key_moment, _current_motion_info

    if motion_info is None:
        motion_info = {}

    direction = motion_info.get("direction", "unknown")
    logger.info("=== Motion event ended at %s (direction: %s) ===", last_timestamp.isoformat(), direction)

    # 1. Stop clip
    finished_path = _extractor.stop_clip()

    # 2. Update log entry with end time and direction
    if _current_entry_id:
        _activity_log.update_event_end(_current_entry_id, last_timestamp, direction)

    # 3. Queue Drive upload
    if finished_path and finished_path.exists() and _current_entry_id:
        _drive_uploader.enqueue(finished_path, _current_entry_id, _current_is_key_moment)
        logger.info("Clip queued for upload: %s", finished_path)
    elif finished_path and not finished_path.exists():
        logger.warning("Clip file missing after recording: %s", finished_path)

    _current_entry_id = None
    _current_clip_path = None
    _current_is_key_moment = False
    _current_motion_info = None


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_environment() -> bool:
    ok = True

    if not settings.youtube_stream_key:
        logger.warning("YOUTUBE_STREAM_KEY not set in .env — YouTube relay will fail")

    if not _describer.health_check():
        logger.warning(
            "Google Gemini API key missing or invalid. AI descriptions disabled. "
            "Add GEMINI_API_KEY to your .env file."
        )

    try:
        import cv2  # noqa: F401
    except ImportError:
        logger.error("opencv-python not installed. Run: pip install -r requirements.txt")
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _shutdown(sig, frame) -> None:
    logger.info("Shutdown signal received — cleaning up...")
    if _extractor:
        _extractor.stop_clip()
    if _reader:
        _reader.stop()
    if _drive_uploader:
        _drive_uploader.stop()
    if _youtube_chapters:
        _youtube_chapters.stop()
    logger.info("Goodbye.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    logger.info("Bird Box Stream Processor starting...")

    global _reader, _extractor, _describer, _activity_log, _drive_uploader, _youtube_chapters

    # Ensure output directories exist
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    settings.activity_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Instantiate components
    _activity_log = ActivityLog()
    _describer = BirdDescriber()
    _extractor = ClipExtractor()
    _drive_uploader = DriveUploader(_activity_log)
    _drive_uploader.start()
    _youtube_chapters = YouTubeChapters()
    _youtube_chapters.start()

    validate_environment()

    # Wire motion detector
    detector = MotionDetector(
        on_motion_start=on_motion_start,
        on_motion_end=on_motion_end,
    )

    # Wire stream reader
    _reader = StreamReader(url=settings.camera_rtmp_url)
    _reader.set_frame_callback(detector.process_frame)

    # Signal handlers
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start reading
    logger.info("Connecting to stream: %s", settings.camera_rtmp_url)
    logger.info("Waiting for mediamtx to be running (mediamtx.exe mediamtx.yml)...")
    _reader.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(None, None)


if __name__ == "__main__":
    main()
