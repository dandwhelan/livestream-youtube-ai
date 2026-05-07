"""
Writes a single-line stats string to logs/overlay_stats.txt every N seconds.

The YouTube relay's FFmpeg `drawtext` filter reads this with `reload=1`, so
the text on the YouTube feed updates live without restarting the encode.
"""

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)


class OverlayWriter:
    def __init__(self, output_path: Path | None = None, interval_seconds: int = 10):
        self.output_path = output_path or Path("logs/overlay_stats.txt")
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        # Write once synchronously so the file exists before FFmpeg's drawtext
        # tries to read it.
        try:
            self._write_once()
        except Exception:
            logger.exception("OverlayWriter initial write failed")
        self._thread = threading.Thread(target=self._loop, name="OverlayWriter", daemon=True)
        self._thread.start()
        logger.info("OverlayWriter started -> %s", self.output_path)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._write_once()
            except Exception:
                logger.exception("OverlayWriter tick failed")
            self._stop_event.wait(self.interval_seconds)

    def _write_once(self) -> None:
        log_path = settings.activity_log_path
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        except Exception:
            entries = []

        today_str = date.today().isoformat()
        today_entries = [e for e in entries if e.get("event_start", "").startswith(today_str)]

        visits = len(today_entries)
        in_count = sum(1 for e in today_entries if e.get("motion_direction") == "entering")
        out_count = sum(1 for e in today_entries if e.get("motion_direction") == "leaving")
        key = sum(1 for e in today_entries if e.get("is_key_moment"))

        last_seen = "--:--"
        if today_entries:
            try:
                dt = datetime.fromisoformat(today_entries[-1].get("event_start", ""))
                if dt.tzinfo:
                    dt = dt.astimezone()
                last_seen = dt.strftime("%H:%M")
            except Exception:
                pass

        text = f"Visits {visits}  In {in_count}  Out {out_count}  Key {key}  Last {last_seen}"

        # Atomic write so drawtext's reload never reads a torn file.
        tmp_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(self.output_path)
