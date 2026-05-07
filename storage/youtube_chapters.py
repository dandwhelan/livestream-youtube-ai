import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube"]
_TOKEN_FILE = Path("credentials/youtube_token.json")
_STATE_FILE = Path("logs/youtube_chapter_state.json")

# Matches both "M:SS label" and "H:MM:SS label" chapter lines that we previously wrote.
_CHAPTER_LINE_RE = re.compile(r"^(\d+):(\d{2})(?::(\d{2}))?\s+(.+)$")


def _fmt_timestamp(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_existing_chapters(description: str) -> list[tuple[int, str]]:
    """Parse chapter lines we previously wrote into the broadcast description.
    Returns a list of (offset_seconds, label) tuples, ordered as in the source."""
    parsed: list[tuple[int, str]] = []
    for raw_line in (description or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _CHAPTER_LINE_RE.match(line)
        if not m:
            continue
        a, b, c, label = m.groups()
        if c is not None:
            offset = int(a) * 3600 + int(b) * 60 + int(c)
        else:
            offset = int(a) * 60 + int(b)
        parsed.append((offset, label.strip()))
    return parsed


def _load_chapter_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_chapter_state(broadcast_id: str) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(_STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps({"broadcast_id": broadcast_id}), encoding="utf-8")
        tmp.replace(_STATE_FILE)
    except OSError:
        logger.exception("Could not persist chapter state")


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
        self._recent_posts: list[tuple[datetime, str]] = []  # (sent_at, message) for dedup
        self._my_channel_id: str | None = None
        self._lock = threading.Lock()
        self._enabled = False
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

    def is_ready(self) -> bool:
        return self._enabled and self._live_chat_id is not None

    def my_channel_id(self) -> str | None:
        """Returns this bot's own YouTube channel id, looked up once and cached."""
        if self._my_channel_id is not None or self._service is None:
            return self._my_channel_id
        try:
            resp = self._service.channels().list(part="id", mine=True).execute()
            items = resp.get("items", [])
            if items:
                self._my_channel_id = items[0]["id"]
                logger.info("Bot channel id: %s", self._my_channel_id)
        except Exception:
            logger.exception("Could not fetch bot channel id")
        return self._my_channel_id

    def read_chat_messages(self, page_token: str | None = None) -> tuple[list[dict], str | None, int]:
        """Returns (messages, next_page_token, polling_interval_millis).
        Empty list when chat is not yet attached or on transient API errors.
        Each message dict is the raw YouTube liveChatMessages.list item."""
        if not self.is_ready():
            return [], None, 5000
        try:
            kwargs = {
                "liveChatId": self._live_chat_id,
                "part": "id,snippet,authorDetails",
            }
            if page_token:
                kwargs["pageToken"] = page_token
            resp = self._service.liveChatMessages().list(**kwargs).execute()
            return (
                resp.get("items", []),
                resp.get("nextPageToken"),
                int(resp.get("pollingIntervalMillis", 5000)),
            )
        except Exception:
            logger.exception("Failed to read chat messages")
            return [], None, 5000

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
            self._original_description = ""
            self._live_chat_id = snippet.get("liveChatId")

            actual_start = snippet.get("actualStartTime")
            if actual_start:
                self._stream_start = datetime.fromisoformat(
                    actual_start.replace("Z", "+00:00")
                )
            else:
                self._stream_start = datetime.now(timezone.utc)

            # If we're re-attaching to the SAME broadcast (e.g. main.py restarted
            # mid-stream) parse our own chapter lines back out of the existing
            # description so the timeline stays continuous. For a NEW broadcast
            # we still start fresh, which keeps prior sessions from leaking in.
            saved_state = _load_chapter_state()
            same_broadcast = saved_state.get("broadcast_id") == self._broadcast_id
            existing = snippet.get("description", "")
            restored = _parse_existing_chapters(existing) if same_broadcast else []
            if restored:
                self._chapters = sorted(restored, key=lambda x: x[0])
                logger.info(
                    "Re-attached to broadcast %s — restored %d chapters from existing description.",
                    self._broadcast_id,
                    len(self._chapters),
                )
            else:
                self._chapters = [(0, settings.youtube_stream_title)]
                if same_broadcast:
                    logger.info(
                        "Same broadcast %s but no parseable chapters in description — starting fresh list.",
                        self._broadcast_id,
                    )
                else:
                    logger.info(
                        "New broadcast %s (was %s) — starting fresh chapter list.",
                        self._broadcast_id,
                        saved_state.get("broadcast_id"),
                    )

            _save_chapter_state(self._broadcast_id)

            self._enabled = True
            logger.info(
                "YouTube chapter markers enabled. Broadcast: %s, started: %s, liveChatId: %s",
                self._broadcast_id,
                self._stream_start.isoformat(),
                "yes" if self._live_chat_id else "no",
            )

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

    def post_message(self, message: str) -> bool:
        """Public: post an arbitrary message to YouTube live chat. Returns True if sent."""
        if not self._enabled or not self._live_chat_id or not message:
            return False
        try:
            return self._post_live_chat(message)
        except Exception:
            logger.exception("Failed to post live-chat message")
            return False

    def _post_live_chat(self, message: str) -> bool:
        if self._chat_messages_today >= 200:
            logger.warning("YouTube Chat quota limit reached for today. Skipping message.")
            return False

        if self._is_recent_duplicate(message):
            logger.info("Suppressed near-duplicate live-chat message: %s", message)
            return False

        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": message[:200]},
            }
        }
        self._service.liveChatMessages().insert(part="snippet", body=body).execute()
        self._chat_messages_today += 1
        self._remember_post(message)
        logger.info("Posted to YouTube Live Chat: %s (Usage: %d/200)", message, self._chat_messages_today)
        return True

    @staticmethod
    def _normalise(text: str) -> set[str]:
        """Lowercased word set, with very common stopwords stripped, for similarity checks."""
        stop = {"the", "a", "an", "is", "of", "to", "in", "on", "at", "and", "with", "for"}
        return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w and w not in stop}

    def _is_recent_duplicate(self, message: str) -> bool:
        """Suppress if a message with >=70% token overlap was posted in the last 30 minutes."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=30)
        with self._lock:
            self._recent_posts = [(t, m) for (t, m) in self._recent_posts if t >= cutoff]
            recent = list(self._recent_posts)

        new_tokens = self._normalise(message)
        if not new_tokens:
            return False
        for _, prev in recent:
            prev_tokens = self._normalise(prev)
            if not prev_tokens:
                continue
            overlap = len(new_tokens & prev_tokens) / max(len(new_tokens), len(prev_tokens))
            if overlap >= 0.7:
                return True
        return False

    def _remember_post(self, message: str) -> None:
        with self._lock:
            self._recent_posts.append((datetime.now(timezone.utc), message))

    _MAX_DESC_LEN = 5000

    def _push_description(self) -> None:
        with self._lock:
            chapters = list(self._chapters)

        prefix = f"{self._original_description}\n\n" if self._original_description else ""

        # Drop oldest non-title chapters until the description fits YouTube's limit.
        while len(chapters) > 1:
            chapter_lines = "\n".join(f"{_fmt_timestamp(t)} {label}" for t, label in chapters)
            if len(prefix) + len(chapter_lines) <= self._MAX_DESC_LEN:
                break
            chapters.pop(1)

        chapter_lines = "\n".join(f"{_fmt_timestamp(t)} {label}" for t, label in chapters)
        full_description = (prefix + chapter_lines)[: self._MAX_DESC_LEN]

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
        logger.debug("YouTube description updated (%d chapters, %d shown)", len(self._chapters), len(chapters))

    def stop(self) -> None:
        self._enabled = False
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
