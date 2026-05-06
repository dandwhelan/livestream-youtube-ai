"""
Silent-alarm watchdog.

During daylight in the nestling stage, parents normally feed every few minutes.
If no key-moment event has been seen for `silent_alarm_minutes`, log a warning
and (once per quiet stretch) post an alert to YouTube live chat.
"""

import logging
import threading
from datetime import datetime, timedelta

from config.settings import settings, current_stage

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60
_DAYLIGHT_HOURS = range(6, 22)  # 06:00–21:59 local


class SilentAlarm:
    def __init__(self, activity_log, youtube_chapters):
        self._log = activity_log
        self._youtube = youtube_chapters
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._alarm_active: bool = False  # de-bounces repeat posts
        self._started_at: datetime = datetime.now()

    def start(self) -> None:
        if not settings.silent_alarm_enabled:
            logger.info("Silent alarm disabled.")
            return
        self._thread = threading.Thread(target=self._loop, name="SilentAlarm", daemon=True)
        self._thread.start()
        logger.info("Silent alarm armed: %d-minute threshold.", settings.silent_alarm_minutes)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.wait(_CHECK_INTERVAL_SECONDS):
            try:
                self._tick()
            except Exception:
                logger.exception("Silent alarm tick failed")

    def _tick(self) -> None:
        now = datetime.now()
        if now.hour not in _DAYLIGHT_HOURS:
            self._alarm_active = False
            return
        if current_stage() != "nestling":
            self._alarm_active = False
            return

        last = self._log.last_key_moment_time()
        # If we've never seen a key moment yet, count from process start so we
        # don't fire instantly on a fresh log.
        reference = last or self._started_at
        gap = now - reference.replace(tzinfo=None) if reference.tzinfo else now - reference
        threshold = timedelta(minutes=settings.silent_alarm_minutes)

        if gap >= threshold and not self._alarm_active:
            self._alarm_active = True
            mins = int(gap.total_seconds() // 60)
            msg = f"⚠️ No feeding visit detected for {mins} minutes — keep an eye on the nest."
            logger.warning(msg)
            if self._youtube:
                self._youtube.post_message(msg)
        elif gap < threshold and self._alarm_active:
            # A new feed reset things — log all-clear once.
            self._alarm_active = False
            logger.info("Silent alarm cleared — feeding has resumed.")
