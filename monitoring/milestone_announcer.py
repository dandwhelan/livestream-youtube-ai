"""
Daily feed-count milestone announcer.

Posts a one-line celebration to YouTube live chat each time today's
"feeds today" count (entering motion events) crosses a milestone
threshold. Resets at local midnight.

Why entering events count as "feeds": motion detection runs on every
frame while the AI describer is gated by a stage-aware cooldown, so
the entering count is the most reliable proxy for actual visits.
See monitoring/hourly_stats.py for the same rationale.
"""

import json
import logging
import threading
from datetime import date
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60

# Threshold -> message. Tuned so a busy nestling-stage day fires roughly
# 3-5 milestones across daylight hours, not one every 5 minutes.
_MILESTONES: dict[int, str] = {
    25:  "25 feeds in the bag today — these chicks are not going hungry!",
    50:  "50 feeds today! Mum and Dad are smashing it.",
    100: "100 feeds today — what a shift! Solid parenting on display.",
    150: "150 feeds today and counting. These chicks must be growing visibly.",
    200: "200 feeds today! Heroic effort from the Great Tit team.",
    250: "250 feeds today — record-breaking work.",
    300: "300 feeds today. Absolute legends.",
}


class MilestoneAnnouncer:
    def __init__(self, youtube_chapters, log_path: Path | None = None):
        self._youtube = youtube_chapters
        self._log_path = log_path or settings.activity_log_path
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._announced_today: set[int] = set()
        self._last_check_date: date | None = None

    def start(self) -> None:
        if not settings.daily_milestones_enabled:
            logger.info("Daily milestones disabled.")
            return
        # Seed already-passed milestones so a mid-day restart doesn't fire them
        # all at once. We don't know what was previously announced — assume
        # any milestone already crossed has been covered.
        self._last_check_date = date.today()
        try:
            seed = self._count_feeds_today(self._last_check_date)
            self._announced_today = {t for t in _MILESTONES if t <= seed}
            if self._announced_today:
                logger.info(
                    "MilestoneAnnouncer seeded — already past thresholds: %s (current count %d)",
                    sorted(self._announced_today), seed,
                )
        except Exception:
            logger.exception("Failed to seed milestone announcer; will fire from scratch.")

        self._thread = threading.Thread(target=self._loop, name="MilestoneAnnouncer", daemon=True)
        self._thread.start()
        logger.info("Daily milestones armed: %s", sorted(_MILESTONES))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.wait(_CHECK_INTERVAL_SECONDS):
            try:
                self._tick()
            except Exception:
                logger.exception("MilestoneAnnouncer tick failed")

    def _tick(self) -> None:
        today = date.today()
        if today != self._last_check_date:
            self._announced_today.clear()
            self._last_check_date = today

        feeds = self._count_feeds_today(today)
        for threshold in sorted(_MILESTONES):
            if feeds < threshold or threshold in self._announced_today:
                continue
            msg = _MILESTONES[threshold]
            logger.info("Milestone %d crossed (%d feeds): %s", threshold, feeds, msg)
            if self._youtube:
                self._youtube.post_message(msg)
            # Mark as announced even if posting failed — avoids retrying every
            # minute when the chat quota is exhausted or chapters aren't ready.
            self._announced_today.add(threshold)

    def _count_feeds_today(self, today: date) -> int:
        try:
            entries = json.loads(self._log_path.read_text(encoding="utf-8")) if self._log_path.exists() else []
        except Exception:
            return 0
        today_str = today.isoformat()
        return sum(
            1 for e in entries
            if e.get("event_start", "").startswith(today_str)
            and e.get("motion_direction") == "entering"
        )
