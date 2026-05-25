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

from config.settings import settings, load_overrides
from stream.reader import StreamReader
from stream.motion import MotionDetector
from stream.debug_server import start as start_debug_server
from ai.describer import BirdDescriber
from clips.extractor import ClipExtractor
from storage.activity_log import ActivityLog
from storage.drive_uploader import DriveUploader
from storage.youtube_chapters import YouTubeChapters
from monitoring.daily_summary import DailySummary
from monitoring.silent_alarm import SilentAlarm
from monitoring.hourly_stats import HourlyStats
from monitoring.milestone_announcer import MilestoneAnnouncer
from monitoring.chat_responder import ChatResponder
from monitoring.facts_poster import FactsPoster
from monitoring.quiet_describer import QuietDescriber
from monitoring import entrance_messages
from monitoring.fledge_watcher import FledgeWatcher


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
_daily_summary: DailySummary | None = None
_silent_alarm: SilentAlarm | None = None
_hourly_stats: HourlyStats | None = None
_milestone_announcer: MilestoneAnnouncer | None = None
_chat_responder: ChatResponder | None = None
_facts_poster: FactsPoster | None = None
_quiet_describer: QuietDescriber | None = None
_fledge_watcher: FledgeWatcher | None = None

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
    For nest-zone motion: blocks ~5-15s on Gemini for a description.
    For entrance-zone motion: AI is skipped — motion alone tells us a parent
    is visiting, and the templated message is posted at motion-end once
    direction (in/out) is known.
    """
    global _current_entry_id, _current_clip_path, _current_is_key_moment, _current_motion_info

    if motion_info is None:
        motion_info = {}

    zone = motion_info.get("zone", "unknown")
    _current_motion_info = motion_info

    logger.info("=== Motion event started at %s (zone: %s) ===", timestamp.isoformat(), zone)

    # Entrance events are unambiguous from motion + direction tracking, so we
    # skip the vision call here and post a templated message at motion-end.
    if zone == "entrance":
        description = None
        is_key_moment = False
    else:
        description, is_key_moment = _describer.describe_frame(frame)
        if description:
            logger.info("Activity: %s", description)
        else:
            logger.info("AI description unavailable")

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

    # 4. Add YouTube chapter marker (entrance events get theirs at motion-end)
    if description:
        _youtube_chapters.add_chapter(timestamp, description, is_key_moment)

    # 5. Create activity log entry
    _current_entry_id = _activity_log.append_event(
        event_start=timestamp,
        ai_description=description,
        clip_path=clip_path,
        is_key_moment=is_key_moment,
        motion_zone=zone,
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

    # 2a. Entrance events: post templated message + add chapter now that
    # direction is known. We deliberately skipped Gemini at motion-start
    # for entrance-triggered events. Departures often start in the nest zone
    # (mum moves before reaching the hole), so we also catch "leaving" here
    # even when the initial motion was in the nest.
    starting_zone = (_current_motion_info or {}).get("zone")
    if starting_zone == "entrance" or direction == "leaving":
        message = entrance_messages.message_for(direction)
        logger.info("Entrance %s — posting templated message: %s", direction, message)
        if _current_entry_id:
            _activity_log.update_event_description(
                _current_entry_id, ai_description=message, is_key_moment=True
            )
        _current_is_key_moment = True
        if _youtube_chapters:
            _youtube_chapters.add_chapter(last_timestamp, message, is_key_moment=True)

    # 2b. Chick-count estimator: when mum has just left, the chicks should be
    # visible. Re-use Gemini on the latest frame and store the count.
    if (
        settings.chick_count_enabled
        and direction == "leaving"
        and _current_entry_id
        and _reader is not None
    ):
        latest_frame, _ = _reader.get_latest_frame()
        if latest_frame is not None:
            count = _describer.count_chicks(latest_frame)
            if count is not None:
                _activity_log.update_chick_count(_current_entry_id, count)

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
    if _daily_summary:
        _daily_summary.stop()
    if _silent_alarm:
        _silent_alarm.stop()
    if _hourly_stats:
        _hourly_stats.stop()
    if _milestone_announcer:
        _milestone_announcer.stop()
    if _chat_responder:
        _chat_responder.stop()
    if _facts_poster:
        _facts_poster.stop()
    if _quiet_describer:
        _quiet_describer.stop()
    if _fledge_watcher:
        _fledge_watcher.stop()
    logger.info("Goodbye.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    logger.info("Bird Box Stream Processor starting...")

    global _reader, _extractor, _describer, _activity_log, _drive_uploader, _youtube_chapters
    global _daily_summary, _silent_alarm, _hourly_stats, _milestone_announcer, _chat_responder
    global _facts_poster, _quiet_describer, _fledge_watcher

    # Apply any saved tuning / exclusion zones from config/overrides.json
    load_overrides()

    # Ensure output directories exist
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    settings.activity_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Instantiate components
    _activity_log = ActivityLog()
    _describer = BirdDescriber(activity_log=_activity_log)
    _extractor = ClipExtractor()
    _drive_uploader = DriveUploader(_activity_log)
    _drive_uploader.start()
    _youtube_chapters = YouTubeChapters()
    _youtube_chapters.start()

    _daily_summary = DailySummary(_describer, _activity_log, _youtube_chapters)
    _daily_summary.start()
    _silent_alarm = SilentAlarm(_activity_log, _youtube_chapters)
    _silent_alarm.start()

    _hourly_stats = HourlyStats(_youtube_chapters)
    _hourly_stats.start()

    _milestone_announcer = MilestoneAnnouncer(_youtube_chapters)
    _milestone_announcer.start()

    _chat_responder = ChatResponder(_youtube_chapters, _describer)
    _chat_responder.start()

    _facts_poster = FactsPoster(_youtube_chapters)
    _facts_poster.start()

    _fledge_watcher = FledgeWatcher(_youtube_chapters)
    _fledge_watcher.start()

    validate_environment()

    # Wire motion detector
    detector = MotionDetector(
        on_motion_start=on_motion_start,
        on_motion_end=on_motion_end,
    )

    # Motion-detection debug server (off by default; toggle via the page)
    start_debug_server(detector)

    # Wire stream reader
    _reader = StreamReader(url=settings.camera_rtmp_url)
    _reader.set_frame_callback(detector.process_frame)

    # Quiet describer needs the reader, so it's started after reader is created
    _quiet_describer = QuietDescriber(
        _reader,
        _describer,
        _youtube_chapters,
        is_motion_active_fn=lambda: _current_motion_info is not None,
    )
    _quiet_describer.start()

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
