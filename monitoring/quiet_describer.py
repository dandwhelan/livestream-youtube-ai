"""
Passive nest observer: during quiet periods (no active motion) randomly
captures a frame, sends it to Gemini, and posts the description to chat.

This gives viewers an occasional peek at what the chicks are doing between
feeding visits without requiring any motion trigger.
"""
import logging
import random
import threading
from datetime import datetime

from config.settings import settings

logger = logging.getLogger(__name__)

_DAYLIGHT_HOURS = range(6, 22)


class QuietDescriber:
    def __init__(self, reader, describer, youtube_chapters, is_motion_active_fn):
        self._reader = reader
        self._describer = describer
        self._youtube = youtube_chapters
        self._is_motion_active = is_motion_active_fn
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.quiet_describer_enabled:
            logger.info("Quiet describer disabled.")
            return
        self._thread = threading.Thread(
            target=self._loop, name="QuietDescriber", daemon=True
        )
        self._thread.start()
        logger.info(
            "Quiet describer started (interval: %d–%d min).",
            settings.quiet_describer_min_minutes,
            settings.quiet_describer_max_minutes,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            min_s = max(1, settings.quiet_describer_min_minutes) * 60
            max_s = max(min_s, settings.quiet_describer_max_minutes * 60)
            interval = random.uniform(min_s, max_s)
            if self._stop_event.wait(interval):
                return
            try:
                self._tick()
            except Exception:
                logger.exception("Quiet describer tick failed")

    def _tick(self) -> None:
        now = datetime.now()
        if now.hour not in _DAYLIGHT_HOURS:
            return

        if self._is_motion_active():
            logger.debug("Quiet describer: skipping — motion currently active")
            return

        frame, ts = self._reader.get_latest_frame()
        if frame is None:
            logger.debug("Quiet describer: no frame available yet")
            return

        description, is_key_moment = self._describer.describe_quiet_frame(frame)
        if not description:
            return

        logger.info("Quiet observation posted (key=%s): %s", is_key_moment, description[:80])
        timestamp = ts or now
        if self._youtube:
            self._youtube.add_chapter(timestamp, description, is_key_moment)
