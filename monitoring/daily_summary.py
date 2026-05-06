"""
Daily wrap-up post.

At a configured local hour each day, reads today's events from the activity
log, asks Gemini to write a warm short recap, and posts it to YouTube live
chat (if connected). Disabled via settings.daily_summary_enabled.
"""

import logging
import threading
from datetime import datetime, timedelta

from config.settings import settings

logger = logging.getLogger(__name__)


class DailySummary:
    def __init__(self, describer, activity_log, youtube_chapters):
        self._describer = describer
        self._log = activity_log
        self._youtube = youtube_chapters
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_date = None  # date object of the last successful post

    def start(self) -> None:
        if not settings.daily_summary_enabled:
            logger.info("Daily summary disabled.")
            return
        self._thread = threading.Thread(target=self._loop, name="DailySummary", daemon=True)
        self._thread.start()
        logger.info("Daily summary scheduled for %02d:00 local.", settings.daily_summary_hour)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            target = now.replace(
                hour=settings.daily_summary_hour, minute=0, second=0, microsecond=0
            )
            if now >= target:
                target = target + timedelta(days=1)
            wait = (target - now).total_seconds()
            if self._stop_event.wait(wait):
                return
            try:
                self._run_once()
            except Exception:
                logger.exception("Daily summary failed")

    def _run_once(self) -> None:
        today = datetime.now().date()
        if self._last_run_date == today:
            return
        start_of_day = datetime.combine(today, datetime.min.time())
        events = self._log.events_since(start_of_day)
        if not events:
            logger.info("Daily summary: no events today, skipping.")
            self._last_run_date = today
            return
        summary = self._describer.generate_daily_summary(events)
        if not summary:
            logger.info("Daily summary: AI returned nothing, skipping.")
            return
        logger.info("Daily summary: %s", summary)
        if self._youtube and self._youtube.post_message(summary):
            logger.info("Daily summary posted to YouTube live chat.")
        self._last_run_date = today
