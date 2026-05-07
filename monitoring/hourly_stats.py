"""
Hourly stats post.

Once an hour, posts a single-line summary of today's nest activity to
YouTube live chat. "Feeds" is reported as the count of motion events with
direction == "entering", because motion detection runs on every frame
while the AI describer is gated by a stage-aware cooldown — so the entering
count is the most reliable proxy for "how many visits today".
"""

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

from config.settings import settings, current_stage

logger = logging.getLogger(__name__)


class HourlyStats:
    def __init__(self, youtube_chapters, log_path: Path | None = None):
        self._youtube = youtube_chapters
        self._log_path = log_path or settings.activity_log_path
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.hourly_stats_enabled:
            logger.info("Hourly stats disabled.")
            return
        self._thread = threading.Thread(target=self._loop, name="HourlyStats", daemon=True)
        self._thread.start()
        logger.info(
            "Hourly stats armed: posting to YouTube chat every %d minutes.",
            settings.hourly_stats_interval_minutes,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        interval = max(1, settings.hourly_stats_interval_minutes) * 60
        while not self._stop_event.wait(interval):
            try:
                self._post_once()
            except Exception:
                logger.exception("Hourly stats post failed")

    def _post_once(self) -> None:
        if not self._youtube:
            return

        today = self._read_today_entries()
        feeds = sum(1 for e in today if e.get("motion_direction") == "entering")
        out_count = sum(1 for e in today if e.get("motion_direction") == "leaving")
        key = sum(1 for e in today if e.get("is_key_moment"))

        last_visit = "—"
        if today:
            try:
                dt = datetime.fromisoformat(today[-1].get("event_start", ""))
                if dt.tzinfo:
                    dt = dt.astimezone()
                last_visit = dt.strftime("%H:%M")
            except Exception:
                pass

        now_label = datetime.now().strftime("%H:%M")
        stage = current_stage()
        msg = (
            f"Hourly update {now_label} — Feeds today: {feeds} "
            f"(in {feeds} / out {out_count}), AI-confirmed: {key}, "
            f"last visit: {last_visit}, stage: {stage}"
        )
        logger.info("Hourly stats: %s", msg)
        self._youtube.post_message(msg)

    def _read_today_entries(self) -> list[dict]:
        try:
            entries = (
                json.loads(self._log_path.read_text(encoding="utf-8"))
                if self._log_path.exists()
                else []
            )
        except Exception:
            return []
        today_str = date.today().isoformat()
        return [e for e in entries if e.get("event_start", "").startswith(today_str)]
