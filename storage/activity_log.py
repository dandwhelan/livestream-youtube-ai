import json
import threading
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivityLog:
    """
    Append-only JSON log of motion events.
    Stored as a JSON array. Thread-safe via lock.
    Entries are created on motion_start, updated when clip finishes and Drive upload completes.
    """

    def __init__(self, log_path: Path = settings.activity_log_path):
        self.log_path = log_path
        self._lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("[]", encoding="utf-8")

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, entries: list[dict]) -> None:
        self.log_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def append_event(
        self,
        event_start: datetime,
        ai_description: str | None,
        clip_path: Path | None,
        is_key_moment: bool = False,
        motion_zone: str = "unknown",
        is_intruder: bool = False,
        snapshot_path: Path | None = None,
    ) -> str:
        """
        Creates a new log entry. Returns the entry id.
        clip_path may be None if recording failed.
        """
        entry_id = str(uuid.uuid4())
        entry: dict[str, Any] = {
            "id": entry_id,
            "event_start": event_start.isoformat(),
            "event_end": None,
            "ai_description": ai_description,
            "is_key_moment": is_key_moment,
            "motion_zone": motion_zone,
            "motion_direction": None,
            "is_intruder": is_intruder,
            "clip_filename": clip_path.name if clip_path else None,
            "clip_local_path": str(clip_path) if clip_path else None,
            "snapshot_filename": snapshot_path.name if snapshot_path else None,
            "drive_clip_url": None,
            "drive_upload_status": "pending" if clip_path else "no_clip",
            "created_at": _utc_now(),
        }
        with self._lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries)
        logger.info("Activity log entry created: %s", entry_id)
        return entry_id

    def update_event_end(self, entry_id: str, event_end: datetime, direction: str = "unknown") -> None:
        with self._lock:
            entries = self._read()
            for e in entries:
                if e["id"] == entry_id:
                    e["event_end"] = event_end.isoformat()
                    e["motion_direction"] = direction
                    break
            self._write(entries)

    def update_drive_url(self, entry_id: str, drive_url: str, status: str) -> None:
        with self._lock:
            entries = self._read()
            for e in entries:
                if e["id"] == entry_id:
                    e["drive_clip_url"] = drive_url
                    e["drive_upload_status"] = status
                    break
            self._write(entries)

    def get_pending_uploads(self) -> list[dict]:
        with self._lock:
            entries = self._read()
        return [e for e in entries if e.get("drive_upload_status") == "pending"]

    def read_all(self) -> list[dict]:
        with self._lock:
            return self._read()
