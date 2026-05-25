"""
Fledge key-moment watcher.

Runs only during the fledging stage. Polls the activity log and fires
chat announcements at two types of threshold:

  EXIT milestones — each time today's leaving-event count crosses 1, 2, 3, 4
  the watcher posts a KEY_MOMENT style message to chat. An exit during fledge
  week is not a foraging run; it's a potential departure from the nest for good.

  CHICK-COUNT drops — when the latest AI chick count falls below the previous
  confirmed count, the watcher fires a more urgent announcement. This is the
  closest thing to a confirmed fledge the system can detect automatically.

Both thresholds reset at midnight. The watcher silently does nothing outside
the fledging stage.
"""

import json
import logging
import threading
from datetime import date
from pathlib import Path

from config.settings import settings, current_stage

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 30

# Exit-count milestone → message.
# Kept deliberately low (1-4) because during fledging every exit matters.
_EXIT_MILESTONES: dict[int, str] = {
    1: "First exit from the box today. Fledge watch is officially on.",
    2: "Two departures logged today. Something's building.",
    3: "Three exits today. Whoever's still inside is running out of time.",
    4: "Four exits today — if those were chicks, the box might be empty before dark.",
}

# Chick-count-drop → message. Keyed by the NEW (lower) count.
_COUNT_DROP_MESSAGES: dict[int, str] = {
    3: "AI just confirmed 3 chicks visible — one appears to have fledged. The fledge has started.",
    2: "Only 2 chicks visible. Fledging is happening right now.",
    1: "Down to 1 chick in the box. The others have gone.",
    0: "Box is empty. They've all fledged. That's it. Season over.",
}


class FledgeWatcher:
    def __init__(self, youtube_chapters, log_path: Path | None = None):
        self._youtube = youtube_chapters
        self._log_path = log_path or settings.activity_log_path
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._announced_exits: set[int] = set()
        self._announced_count_drops: set[int] = set()
        self._last_check_date: date | None = None
        self._last_known_count: int | None = None

    def start(self) -> None:
        self._last_check_date = date.today()
        self._seed()
        self._thread = threading.Thread(target=self._loop, name="FledgeWatcher", daemon=True)
        self._thread.start()
        logger.info("FledgeWatcher armed.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _seed(self) -> None:
        """On (re)start, mark any already-passed thresholds as announced so we
        don't retroactively spam chat with milestones that happened earlier."""
        try:
            today = self._read_today_entries()
            exits = sum(1 for e in today if e.get("motion_direction") == "leaving")
            self._announced_exits = {t for t in _EXIT_MILESTONES if t <= exits}
            counts = [e.get("chick_count") for e in today if e.get("chick_count") is not None]
            if counts:
                self._last_known_count = counts[-1]
                self._announced_count_drops = {
                    c for c in _COUNT_DROP_MESSAGES if c >= counts[-1]
                }
            if self._announced_exits or self._announced_count_drops:
                logger.info(
                    "FledgeWatcher seeded — exits already at %d, count drops announced: %s",
                    exits, sorted(self._announced_count_drops),
                )
        except Exception:
            logger.exception("FledgeWatcher seed failed; will fire from scratch.")

    def _loop(self) -> None:
        while not self._stop_event.wait(_CHECK_INTERVAL_SECONDS):
            try:
                self._tick()
            except Exception:
                logger.exception("FledgeWatcher tick failed")

    def _tick(self) -> None:
        if current_stage() != "fledging":
            return

        today = date.today()
        if today != self._last_check_date:
            self._announced_exits.clear()
            self._announced_count_drops.clear()
            self._last_known_count = None
            self._last_check_date = today

        entries = self._read_today_entries()

        # --- Exit milestones ---
        exits = sum(1 for e in entries if e.get("motion_direction") == "leaving")
        for threshold in sorted(_EXIT_MILESTONES):
            if exits >= threshold and threshold not in self._announced_exits:
                msg = _EXIT_MILESTONES[threshold]
                logger.info("Fledge exit milestone %d: %s", threshold, msg)
                if self._youtube:
                    self._youtube.post_message(msg)
                self._announced_exits.add(threshold)

        # --- Chick-count drops ---
        counts = [e.get("chick_count") for e in entries if e.get("chick_count") is not None]
        if not counts:
            return
        latest = counts[-1]
        if self._last_known_count is None:
            self._last_known_count = latest
            return
        if latest < self._last_known_count:
            # Count dropped — fire the appropriate drop message (if not already announced)
            for target_count in sorted(_COUNT_DROP_MESSAGES, reverse=True):
                if latest <= target_count and target_count not in self._announced_count_drops:
                    msg = _COUNT_DROP_MESSAGES[target_count]
                    logger.info("Fledge chick-count drop to %d: %s", target_count, msg)
                    if self._youtube:
                        self._youtube.post_message(msg)
                    self._announced_count_drops.add(target_count)
        self._last_known_count = latest

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
