import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube"]
_TOKEN_FILE = Path("credentials/youtube_token.json")


def _fmt_timestamp(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class YouTubeChapters:
    """
    Maintains a live chapter list in the YouTube broadcast description.
    Adds a chapter entry each time the AI produces a bird description.

    One-time setup: run  python auth_youtube.py  to authorise and save the token.
    """

    def __init__(self):
        self._service = None
        self._broadcast_id: str | None = None
        self._stream_start: datetime | None = None
        self._original_description: str = ""
        self._chapters: list[tuple[int, str]] = []  # (offset_seconds, label)
        self._live_chat_id: str | None = None
        self._chat_messages_today: int = 0
        self._lock = threading.Lock()
        self._enabled = False
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

    def start(self) -> None:
        if not _TOKEN_FILE.exists():
            logger.warning(
                "YouTube token not found at %s. "
                "Run  python auth_youtube.py  once to authorise. "
                "Chapter markers disabled until then.",
                _TOKEN_FILE,
            )
            return

        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            self._service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        except ImportError:
            logger.error("google-api-python-client not installed. Run: pip install -r requirements.txt")
            return
        except Exception:
            logger.exception("Failed to initialise YouTube client")
            return

        # The YouTube relay starts after main.py in start.ps1, so the broadcast
        # is rarely "active" at this point. Poll in the background until it is,
        # otherwise chapters/live-chat stay disabled for the whole session.
        self._poll_thread = threading.Thread(
            target=self._wait_for_broadcast, name="YouTubeChapters", daemon=True
        )
        self._poll_thread.start()

    def _wait_for_broadcast(self) -> None:
        delay = 5
        max_delay = 60
        while not self._stop_event.is_set():
            try:
                resp = (
                    self._service.liveBroadcasts()
                    .list(part="id,snippet", broadcastStatus="active", broadcastType="all")
                    .execute()
                )
                items = resp.get("items", [])
                if items:
                    self._attach_broadcast(items[0])
                    return
                logger.info(
                    "No active YouTube broadcast yet — retrying in %ds.", delay
                )
            except Exception:
                logger.exception("Error while polling for active YouTube broadcast")

            if self._stop_event.wait(delay):
                return
            delay = min(delay * 2, max_delay)

    def _attach_broadcast(self, broadcast: dict) -> None:
        try:
            self._broadcast_id = broadcast["id"]
            snippet = broadcast["snippet"]
            self._original_description = snippet.get("description", "")
            self._live_chat_id = snippet.get("liveChatId")

            actual_start = snippet.get("actualStartTime")
            if actual_start:
                self._stream_start = datetime.fromisoformat(
                    actual_start.replace("Z", "+00:00")
                )
            else:
                self._stream_start = datetime.now(timezone.utc)

            self._enabled = True
            logger.info(
                "YouTube chapter markers enabled. Broadcast: %s, started: %s, liveChatId: %s",
                self._broadcast_id,
                self._stream_start.isoformat(),
                "yes" if self._live_chat_id else "no",
            )

            self._chapters = [(0, settings.youtube_stream_title)]
            self._push_description()
        except Exception:
            logger.exception("Failed to attach to YouTube broadcast")

    def add_chapter(self, timestamp: datetime, description: str, is_key_moment: bool = False) -> None:
        if not self._enabled or self._broadcast_id is None:
            return

        ts = timestamp.astimezone(timezone.utc)
        offset = max(0, int((ts - self._stream_start).total_seconds()))
        label = description[:80]  # YouTube description has a 5000 char limit overall

        with self._lock:
            self._chapters.append((offset, label))
            self._chapters.sort(key=lambda x: x[0])

        try:
            self._push_description()
        except Exception:
            logger.exception("Failed to update YouTube chapter markers")

        if is_key_moment and self._live_chat_id:
            try:
                self._post_live_chat(description)
            except Exception:
                logger.exception("Failed to post to YouTube live chat")

    def _post_live_chat(self, message: str) -> None:
        if self._chat_messages_today >= 200:
            logger.warning("YouTube Chat quota limit reached for today. Skipping message.")
            return

        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": message[:200]},
            }
        }
        self._service.liveChatMessages().insert(part="snippet", body=body).execute()
        self._chat_messages_today += 1
        logger.info("Posted to YouTube Live Chat: %s (Usage: %d/200)", message, self._chat_messages_today)

    def _push_description(self) -> None:
        with self._lock:
            chapter_lines = "\n".join(
                f"{_fmt_timestamp(t)} {label}" for t, label in self._chapters
            )

        if self._original_description:
            full_description = f"{self._original_description}\n\n{chapter_lines}"
        else:
            full_description = chapter_lines

        # Fetch current snippet to avoid clobbering other fields
        resp = (
            self._service.liveBroadcasts()
            .list(part="snippet", id=self._broadcast_id)
            .execute()
        )
        snippet = resp["items"][0]["snippet"]
        snippet["description"] = full_description

        self._service.liveBroadcasts().update(
            part="snippet",
            body={"id": self._broadcast_id, "snippet": snippet},
        ).execute()
        logger.debug("YouTube description updated (%d chapters)", len(self._chapters))

    def stop(self) -> None:
        self._enabled = False
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
