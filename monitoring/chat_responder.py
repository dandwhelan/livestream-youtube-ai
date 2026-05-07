"""
Chat responder.

Polls YouTube live chat, detects nest-related questions or @-mentions of the
bot, and posts a Gemini-generated one-line reply. Skips its own messages,
rate-limits responses, and caps the daily reply count to protect the chat
quota and the YouTube Data API quota.

YouTube Data API quota note: liveChatMessages.list costs API quota for
every poll, so the polling interval is configurable. Default 60s keeps
the cost modest. Disable via settings.chat_responder_enabled if quota is
tight.
"""

import json
import logging
import re
import threading
from datetime import date, datetime
from pathlib import Path

from config.settings import settings, current_stage

logger = logging.getLogger(__name__)

# Words that, when seen with a question mark, mean "this is for me to answer".
_NEST_KEYWORDS = (
    "feed", "fed", "feeding",
    "chick", "chicks",
    "egg", "eggs", "hatch", "hatched", "hatching",
    "mum", "mom", "dad", "parent",
    "today", "now", "many", "count", "status", "alarm", "alert",
    "great tit", "tit",
    "stage", "nesting",
    "bird", "nest", "box",
    "stream", "alive",
)


def _is_question_for_bot(text: str, bot_handle: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower().strip()
    # Direct @mention always triggers, even without a question mark.
    if bot_handle and f"@{bot_handle.lower()}" in lowered:
        return True
    if "?" not in lowered:
        return False
    return any(kw in lowered for kw in _NEST_KEYWORDS)


class ChatResponder:
    def __init__(self, youtube_chapters, describer, log_path: Path | None = None):
        self._youtube = youtube_chapters
        self._describer = describer
        self._log_path = log_path or settings.activity_log_path
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._page_token: str | None = None
        self._seen_message_ids: set[str] = set()
        self._responses_today: int = 0
        self._responses_today_date: date | None = None
        self._last_response_at: datetime | None = None
        self._initialised = False

    def start(self) -> None:
        if not settings.chat_responder_enabled:
            logger.info("Chat responder disabled.")
            return
        self._thread = threading.Thread(target=self._loop, name="ChatResponder", daemon=True)
        self._thread.start()
        logger.info(
            "Chat responder armed: poll every %ds, max %d replies/day, %ds between replies.",
            settings.chat_responder_poll_seconds,
            settings.chat_responder_max_per_day,
            settings.chat_responder_min_seconds_between_responses,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Chat responder tick failed")
            self._stop_event.wait(max(15, settings.chat_responder_poll_seconds))

    def _tick(self) -> None:
        if not self._youtube or not self._youtube.is_ready():
            return

        # First poll: drain history without responding so we don't reply to
        # week-old messages as if they were fresh. Just learn the latest pageToken.
        if not self._initialised:
            _, next_token, _ = self._youtube.read_chat_messages()
            self._page_token = next_token
            self._initialised = True
            logger.info("Chat responder primed; will respond to messages from now on.")
            return

        messages, next_token, _ = self._youtube.read_chat_messages(self._page_token)
        self._page_token = next_token

        if not messages:
            return

        my_id = self._youtube.my_channel_id()
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in self._seen_message_ids:
                continue
            self._seen_message_ids.add(msg_id)

            snippet = msg.get("snippet") or {}
            author = msg.get("authorDetails") or {}
            if author.get("channelId") == my_id:
                continue  # don't reply to ourselves

            text = (snippet.get("displayMessage")
                    or snippet.get("textMessageDetails", {}).get("messageText")
                    or "")
            if not text:
                continue

            handle = author.get("displayName") or "viewer"
            bot_handle = self._bot_handle()
            if not _is_question_for_bot(text, bot_handle):
                continue

            if not self._can_respond_now():
                logger.info("Chat responder: would reply to %s but rate-limited.", handle)
                continue

            self._respond(text, handle)

    def _bot_handle(self) -> str | None:
        """Returns the bot's display name (best-effort)."""
        # YouTubeChapters doesn't expose this directly; we sniff it from a
        # one-shot channels.list (cached via my_channel_id() side effect).
        return None  # @-mention matching disabled until we look it up; keyword + ? still works

    def _can_respond_now(self) -> bool:
        today = date.today()
        if self._responses_today_date != today:
            self._responses_today = 0
            self._responses_today_date = today
        if self._responses_today >= settings.chat_responder_max_per_day:
            return False
        min_gap = settings.chat_responder_min_seconds_between_responses
        if self._last_response_at is not None:
            since = (datetime.now() - self._last_response_at).total_seconds()
            if since < min_gap:
                return False
        return True

    def _respond(self, viewer_message: str, viewer_name: str) -> None:
        stats = self._current_stats()
        reply = self._describer.respond_to_chat(viewer_message, viewer_name, stats)
        if not reply:
            logger.info("Chat responder: AI returned nothing for %s", viewer_name)
            return
        if self._youtube.post_message(reply):
            self._responses_today += 1
            self._last_response_at = datetime.now()
            logger.info("Chat responder replied to %s: %s", viewer_name, reply)
        else:
            logger.info("Chat responder: post_message refused (likely dedup or quota)")

    def _current_stats(self) -> dict:
        try:
            entries = (
                json.loads(self._log_path.read_text(encoding="utf-8"))
                if self._log_path.exists() else []
            )
        except Exception:
            entries = []
        today_str = date.today().isoformat()
        today = [e for e in entries if e.get("event_start", "").startswith(today_str)]
        feeds = sum(1 for e in today if e.get("motion_direction") == "entering")
        ai_confirmed = sum(1 for e in today if e.get("is_key_moment"))
        last_visit = "—"
        if today:
            try:
                dt = datetime.fromisoformat(today[-1].get("event_start", ""))
                if dt.tzinfo:
                    dt = dt.astimezone()
                last_visit = dt.strftime("%H:%M")
            except Exception:
                pass

        chick_age_days = None
        if settings.hatch_date:
            try:
                hatch = date.fromisoformat(settings.hatch_date)
                age = (date.today() - hatch).days
                if 0 <= age <= 30:
                    chick_age_days = age
            except ValueError:
                pass

        chick_counts = [e.get("chick_count") for e in today if e.get("chick_count") is not None]
        latest_chick_count = chick_counts[-1] if chick_counts else None

        return {
            "feeds_today": feeds,
            "ai_confirmed": ai_confirmed,
            "last_visit": last_visit,
            "stage": current_stage(),
            "chick_age_days": chick_age_days,
            "latest_chick_count": latest_chick_count,
            "eggs_total": settings.eggs_total,
            "known_chick_deaths": settings.known_chick_deaths,
        }
