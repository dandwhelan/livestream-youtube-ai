import logging
from datetime import datetime, timedelta
from PIL import Image
from google import genai
from google.genai import types
import cv2
import numpy as np

from config.settings import settings, current_stage


_ALERT_REPOST_HOURS = 2.0  # suppress duplicate ALERTs for this many hours


def _word_overlap(a: str, b: str) -> float:
    """Jaccard similarity of lowercased content words (ignores common stop words)."""
    _STOP = {"a", "an", "the", "is", "in", "at", "of", "and", "or", "to", "as",
             "it", "on", "are", "has", "be", "was", "for", "its", "with", "this"}
    wa = {w for w in a.lower().split() if w not in _STOP and len(w) > 2}
    wb = {w for w in b.lower().split() if w not in _STOP and len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)

logger = logging.getLogger(__name__)


STAGE_CONTEXT = {
    "nestling": (
        "STAGE CONTEXT — NESTLING PHASE: The eggs have hatched. "
        "Tiny pink/grey naked chicks with closed eyes may be visible under or beside the mother. "
        "Both parents now feed and brood the chicks. ANY parent in the box during this stage is a KEY MOMENT — "
        "feeding visits AND brooding visits are both chat-worthy. Do NOT downgrade a parent visit to routine "
        "just because you can't see food in the beak; caterpillars are tiny and often hidden. "
        "When you see TWO Great Tits in the box together, that's especially exciting — usually the DAD bringing food. "
        "Other key moments: a chick visibly being fed, eggshells being removed, "
        "fecal sacs being carried out (parental hygiene), or a freshly hatched chick.\n"
        "WELFARE WATCH: Newly hatched chicks are fragile and can be accidentally pushed out of the nest cup "
        "by a parent's feet or wings. A chick lying alone in a corner of the box, away from the nest cup or "
        "its siblings, is in serious trouble — it cannot regulate its own temperature. Treat this as an ALERT, "
        "not a routine event."
    ),
    "incubation": (
        "STAGE CONTEXT — INCUBATION: The mother is sitting on eggs almost continuously. "
        "Key moments are her leaving/returning, the dad bringing food to her, or eggs hatching. "
        "Most footage will be her sitting still — that is routine, not a key moment."
    ),
    "egg_laying": (
        "STAGE CONTEXT — EGG LAYING: The female lays one egg per day, usually early morning. "
        "Key moments are a newly visible egg, the female arriving to lay, or arranging the nest cup."
    ),
    "nest_building": (
        "STAGE CONTEXT — NEST BUILDING: Birds bring moss, grass, and feathers. "
        "Key moments are nest material being delivered or shaped into the cup."
    ),
    "fledging": (
        "STAGE CONTEXT — FLEDGING: Chicks are feathered and nearly ready to leave. "
        "Key moments are chicks at the entrance, wing-flapping/exercising, or actually fledging out of the box."
    ),
    "empty": (
        "STAGE CONTEXT — EMPTY/UNKNOWN: Any bird visit is a key moment."
    ),
}


def _time_label(now: datetime) -> str:
    h = now.hour
    if 4 <= h < 7:    return "early morning"
    if 7 <= h < 11:   return "morning"
    if 11 <= h < 14:  return "midday"
    if 14 <= h < 17:  return "afternoon"
    if 17 <= h < 20:  return "evening"
    if 20 <= h < 22:  return "dusk"
    return "night"


def _format_duration(seconds: int) -> str:
    if seconds < 15:
        return f"a quick {seconds}-second pop-in"
    if seconds < 60:
        return f"about {seconds} seconds"
    mins = seconds // 60
    secs = seconds % 60
    if secs:
        return f"{mins}m {secs}s"
    return f"{mins} minutes"


def build_prompt(
    stage: str,
    *,
    time_label: str = "",
    is_first_event_today: bool = False,
    last_visit_duration: int | None = None,
    avoid_phrasings: list[str] | None = None,
    dead_chick_note: str = "",
    quiet_period: bool = False,
) -> str:
    stage_text = STAGE_CONTEXT.get(stage, STAGE_CONTEXT["empty"])

    persona = (
        "You are the host of a Great Tit nest box live stream. "
        "Your tone is friendly but grounded — knowledgeable, occasionally dry, never gushing. "
        "BANNED WORDS — never use: snuggle, cuddle, cosy, lovely, sweet, adorable, "
        "'little ones', 'tiny ones', toasty, snug. "
        "Say 'chicks' or 'nestlings' (not 'little ones'). Say 'brooding' or 'warming' (not 'snuggling'). "
        "When it adds real context, weave in a brief biological fact — e.g. typical visit rate, "
        "what the behaviour means, chick development stage — but don't force it every time. "
        "For routine or repetitive events, a dry observation is fine: "
        "'Back again. She hasn't stopped all morning.' or 'Another delivery. Fourth this hour.' "
        "Keep it punchy: ideally one short sentence. Avoid exclamation marks on every line."
    )

    parts = [persona]

    # Time-of-day context
    time_lines = []
    if time_label:
        time_lines.append(f"It's currently {time_label}.")
    if is_first_event_today:
        time_lines.append(
            "This is the FIRST activity of the day — that's chat-worthy in itself."
        )
    if time_lines:
        parts.append("TIME CONTEXT: " + " ".join(time_lines))

    # Previous visit duration context
    if last_visit_duration is not None:
        parts.append(
            f"PREVIOUS COMPLETED VISIT lasted {_format_duration(last_visit_duration)}. "
            "If a contrast feels natural ('back already!' / 'much longer this time') you can use it, "
            "but don't force it."
        )

    # Scan-the-frame instructions
    parts.append(
        "BEFORE DESCRIBING: scan the ENTIRE frame edge-to-edge — top, bottom, all four corners — "
        "not just where the obvious motion is. Look specifically for: "
        "(a) any chick lying outside the main nest cup or away from its siblings, "
        "(b) any motionless or limp body anywhere in the frame, "
        "(c) damaged or cracked eggs, "
        "(d) a non-Great-Tit BIRD species entering through the hole (e.g. sparrow, blue tit, woodpecker, predator bird), "
        "(e) parts of a bird visible at the entrance hole. "
        "If you spot any of these, report it — it matters even if the main action is something else.\n"
        "IMPORTANT — intruder species: the entrance hole is a small circular opening only large enough for small birds. "
        "Mice, rodents, or other mammals CANNOT enter this nest box. Do NOT report a mammal intruder under any circumstances. "
        "Only flag a non-Great-Tit intruder if you can clearly identify it as a bird species."
    )

    parts.append(stage_text)

    if dead_chick_note:
        parts.append(dead_chick_note)

    if quiet_period:
        parts.append(
            "CONTEXT: No motion has been detected recently — this is a passive observation check "
            "during a quiet period. Describe the current state of the nest as it is right now. "
            "Do NOT manufacture drama or imply change. A calm, factual snapshot is the goal."
        )

    parts.append(
        "POSITION GUIDANCE: When describing locations, left/right are as seen by the camera looking down into the box "
        "(i.e. from the viewer's perspective on screen). Double-check before writing 'left' or 'right' — "
        "a wrong direction confuses viewers who are watching live.\n\n"
        "Output rules — start your response with EXACTLY ONE of these prefixes:\n"
        "  'ALERT: '       → a chick displaced from the nest cup, a motionless/limp body, "
        "an intruder bird species, a damaged egg, or any other welfare concern. "
        "Follow with a clear, urgent description naming WHERE in the frame the issue is "
        "(e.g. 'bottom-left corner', 'near the entrance'). This stays visible to viewers.\n"
        "  'KEY_MOMENT: '  → feeding, food delivery, dad visiting, eggshell/fecal-sac removal, "
        "hatching, first activity of the day, or anything else genuinely chat-worthy. "
        "Follow with a clear, grounded comment in the host's voice — factual where possible, dry humour fine.\n"
        "  (no prefix)     → routine activity (mum brooding, sitting still, minor adjustments)."
    )

    # Anti-repetition
    if avoid_phrasings:
        avoid_lines = "\n".join(f"  - {p[:90]}" for p in avoid_phrasings[-8:])
        parts.append(
            "AVOID REPETITION — these are your most recent messages. Do NOT open with the same "
            "words, do NOT echo their phrasing or rhythm:\n"
            f"{avoid_lines}\n"
            "Find a fresh way in."
        )

    return "\n\n".join(parts)


def _chick_count_prompt() -> str:
    base = (
        "You are looking at a Great Tit nest box from above. The mother has just left "
        "and the chicks should now be visible in the nest cup. "
        "Count only the LIVING chicks you can clearly see. "
        "Respond with ONLY a single integer (e.g. '5'). "
        "If you cannot see any chicks or cannot tell, respond with '0'."
    )
    note = settings.dead_chick_note
    if note:
        base = f"{note}\n\n{base}"
    return base


def _summary_prompt(events: list[dict]) -> str:
    lines = []
    for e in events:
        ts = (e.get("event_start") or "")[11:16]  # HH:MM
        desc = (e.get("ai_description") or "").strip()
        flag = " [KEY]" if e.get("is_key_moment") else ""
        if desc:
            lines.append(f"{ts}{flag} {desc}")
    log_text = "\n".join(lines) or "(no events)"
    return (
        "You are writing the daily wrap-up for a Great Tit nest box live stream. "
        f"Today's stage is '{current_stage()}'. "
        "Below is the chronological event log for the day. Write a short, factual "
        "recap (≤400 characters, suitable for YouTube live chat) covering: "
        "total feeding visits, the longest quiet gap, and one notable highlight. "
        "Do not invent details that aren't in the log.\n\n"
        f"EVENTS:\n{log_text}"
    )


class BirdDescriber:
    """
    Calls Google Gemini API to describe a single frame.
    Returns (description_string, is_key_moment).
    """

    def __init__(self, activity_log=None):
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY is not set. AI descriptions will be disabled.")
            self.client = None
        else:
            self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model
        self._log = activity_log
        # Rolling buffer of recent descriptions, fed back into the prompt as
        # "do not echo these" so Gemini stops opening every message the same way.
        self._recent_descriptions: list[str] = []
        self._max_recent_descriptions = 10
        # Application-level ALERT dedup: suppress near-duplicate alerts for
        # _ALERT_REPOST_HOURS to stop the same welfare concern flooding chat.
        self._last_alert_text: str | None = None
        self._last_alert_time: datetime | None = None
        # Cost control: chick counts are stable hour-to-hour, so we don't need
        # to spend a vision call on every "leaving" event.
        self._last_chick_count_time: datetime | None = None
        logger.info("BirdDescriber initialised; current stage: %s", current_stage())

    def _recent_context(self, minutes: int = 60, max_items: int = 5) -> str:
        """Returns a short bullet list of the most recent ALERT / KEY_MOMENT
        descriptions so the AI can comment on whether their status has changed."""
        if not self._log:
            return ""
        try:
            cutoff = datetime.now() - timedelta(minutes=minutes)
            events = self._log.events_since(cutoff)
        except Exception:
            return ""
        notable = [
            e for e in events
            if e.get("ai_description") and e.get("is_key_moment")
        ]
        if not notable:
            return ""
        lines = []
        for e in notable[-max_items:]:
            ts = (e.get("event_start") or "")[11:16]  # HH:MM
            desc = (e.get("ai_description") or "").strip().replace("\n", " ")[:140]
            lines.append(f"- {ts} {desc}")
        return "\n".join(lines)

    def _today_context(self) -> tuple[bool, int | None]:
        """Returns (is_first_event_today, last_completed_visit_duration_seconds)."""
        if not self._log:
            return False, None
        try:
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            events = self._log.events_since(today_start)
        except Exception:
            return False, None
        is_first = len(events) == 0
        last_duration: int | None = None
        for e in reversed(events):
            end = e.get("event_end")
            start = e.get("event_start")
            if not (end and start):
                continue
            try:
                s = datetime.fromisoformat(start)
                t = datetime.fromisoformat(end)
                last_duration = int((t - s).total_seconds())
                break
            except (ValueError, TypeError):
                continue
        return is_first, last_duration

    def describe_frame(self, frame: np.ndarray) -> tuple[str | None, bool]:
        if not self.client:
            return None, False

        try:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)

            is_first_event, last_duration = self._today_context()
            prompt = build_prompt(
                current_stage(),
                time_label=_time_label(datetime.now()),
                is_first_event_today=is_first_event,
                last_visit_duration=last_duration,
                avoid_phrasings=list(self._recent_descriptions),
                dead_chick_note=settings.dead_chick_note,
            )
            recent = self._recent_context()
            if recent:
                prompt = (
                    f"{prompt}\n\n"
                    "ALREADY POSTED IN THE LAST 60 MINUTES (most recent last). "
                    "Viewers have ALREADY SEEN these — do NOT repeat them:\n"
                    f"{recent}\n\n"
                    "Rules of engagement:\n"
                    "  - If the current frame clearly shows a prior ALERT has been RESOLVED "
                    "(chick back with siblings, intruder has left, motionless body removed), "
                    "report it as a KEY_MOMENT (good news), NOT as another ALERT.\n"
                    "  - If a prior ALERT situation is UNCHANGED, do NOT mention it at all. "
                    "Describe only the new activity in this frame.\n"
                    "  - Only use ALERT for a NEW welfare concern that is not already in the list above.\n"
                    "  - Vary your phrasing across messages — do not open consecutive messages "
                    "with the same words. Avoid repeating exact phrases from the list above."
                )

            response = self.client.models.generate_content(
                model=self.model,
                contents=[pil_img, prompt],
                config=types.GenerateContentConfig(temperature=0.4),
            )

            description = response.text.strip()
            is_key_moment = False
            label = "Routine"

            if description.startswith("ALERT:"):
                # Welfare concern — keep the "ALERT:" prefix in the message so
                # viewers see the warning prominently.
                is_key_moment = True
                label = "ALERT"
                description = "⚠️ " + description
                # Suppress duplicate ALERTs: if the same welfare situation was
                # already alerted within _ALERT_REPOST_HOURS, don't flood chat.
                if self._last_alert_text and self._last_alert_time:
                    hours_since = (datetime.now() - self._last_alert_time).total_seconds() / 3600
                    overlap = _word_overlap(description, self._last_alert_text)
                    if hours_since < _ALERT_REPOST_HOURS and overlap > 0.65:
                        logger.info(
                            "Suppressing duplicate ALERT (%.1fh ago, %.0f%% overlap): %s",
                            hours_since, overlap * 100, description[:80],
                        )
                        return None, False
                self._last_alert_text = description
                self._last_alert_time = datetime.now()
            elif description.startswith("KEY_MOMENT:"):
                is_key_moment = True
                label = "KEY MOMENT"
                description = description.replace("KEY_MOMENT:", "").strip()

            if description:
                logger.info("AI Description [%s]: %s", label, description)
                # Track for anti-repetition (strip emoji/prefix so we focus on phrasing)
                phrasing = description.replace("⚠️", "").replace("ALERT:", "").strip()
                if phrasing:
                    self._recent_descriptions.append(phrasing)
                    if len(self._recent_descriptions) > self._max_recent_descriptions:
                        self._recent_descriptions = self._recent_descriptions[-self._max_recent_descriptions:]

            return description or None, is_key_moment

        except Exception as e:
            logger.exception("Gemini AI description failed: %s", e)
            return None, False

    def describe_quiet_frame(self, frame: np.ndarray) -> tuple[str | None, bool]:
        """Like describe_frame but signals to Gemini this is a passive quiet-period check."""
        if not self.client:
            return None, False
        try:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            prompt = build_prompt(
                current_stage(),
                time_label=_time_label(datetime.now()),
                avoid_phrasings=list(self._recent_descriptions),
                dead_chick_note=settings.dead_chick_note,
                quiet_period=True,
            )
            recent = self._recent_context()
            if recent:
                prompt = (
                    f"{prompt}\n\n"
                    "ALREADY POSTED RECENTLY (do NOT repeat):\n"
                    f"{recent}"
                )
            response = self.client.models.generate_content(
                model=self.model,
                contents=[pil_img, prompt],
                config=types.GenerateContentConfig(temperature=0.4),
            )
            description = response.text.strip()
            is_key_moment = False
            label = "Quiet/Routine"

            if description.startswith("ALERT:"):
                is_key_moment = True
                label = "ALERT"
                description = "⚠️ " + description
                if self._last_alert_text and self._last_alert_time:
                    hours_since = (datetime.now() - self._last_alert_time).total_seconds() / 3600
                    overlap = _word_overlap(description, self._last_alert_text)
                    if hours_since < _ALERT_REPOST_HOURS and overlap > 0.65:
                        logger.info(
                            "Suppressing duplicate ALERT from quiet check (%.1fh ago, %.0f%% overlap)",
                            hours_since, overlap * 100,
                        )
                        return None, False
                self._last_alert_text = description
                self._last_alert_time = datetime.now()
            elif description.startswith("KEY_MOMENT:"):
                is_key_moment = True
                label = "KEY MOMENT"
                description = description.replace("KEY_MOMENT:", "").strip()

            if description:
                logger.info("Quiet AI Description [%s]: %s", label, description)
                phrasing = description.replace("⚠️", "").replace("ALERT:", "").strip()
                if phrasing:
                    self._recent_descriptions.append(phrasing)
                    if len(self._recent_descriptions) > self._max_recent_descriptions:
                        self._recent_descriptions = self._recent_descriptions[-self._max_recent_descriptions:]

            return description or None, is_key_moment

        except Exception as e:
            logger.exception("Gemini quiet-period description failed: %s", e)
            return None, False

    def count_chicks(self, frame: np.ndarray) -> int | None:
        """Returns an integer chick count if Gemini can read it, else None.
        Throttled to one call per hour to keep the per-day vision spend down —
        chick counts don't change minute-to-minute."""
        if not self.client:
            return None
        if self._last_chick_count_time is not None:
            since = (datetime.now() - self._last_chick_count_time).total_seconds()
            if since < 3600:
                return None
        try:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            response = self.client.models.generate_content(
                model=self.model,
                contents=[pil_img, _chick_count_prompt()],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = (response.text or "").strip()
            digits = "".join(ch for ch in text if ch.isdigit())
            if not digits:
                return None
            count = int(digits)
            self._last_chick_count_time = datetime.now()
            logger.info("Chick count estimate: %d", count)
            return count
        except Exception as e:
            logger.exception("Chick count failed: %s", e)
            return None

    def generate_daily_summary(self, events: list[dict]) -> str | None:
        """Generates a YouTube-live-chat-friendly daily recap from the activity log."""
        if not self.client:
            return None
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_model_text,
                contents=[_summary_prompt(events)],
                config=types.GenerateContentConfig(temperature=0.6),
            )
            text = (response.text or "").strip()
            return text or None
        except Exception as e:
            logger.exception("Daily summary generation failed: %s", e)
            return None

    def respond_to_chat(self, viewer_message: str, viewer_name: str, stats: dict) -> str | None:
        """Generates a one-line warm reply to a viewer's chat message.
        `stats` should include feeds_today, ai_confirmed, last_visit, stage,
        chick_age_days, latest_chick_count, eggs_total, known_chick_deaths."""
        if not self.client:
            return None

        chick_age_days = stats.get("chick_age_days")
        latest_chick_count = stats.get("latest_chick_count")
        eggs_total = stats.get("eggs_total", "?")
        known_chick_deaths = stats.get("known_chick_deaths", 0)

        chick_age_line = (
            f"  - chicks are Day {chick_age_days} old\n" if chick_age_days is not None else ""
        )
        chick_count_line = (
            f"  - last AI chick count visible: {latest_chick_count}\n"
            if latest_chick_count is not None else ""
        )
        egg_lines = (
            f"  - eggs laid: {eggs_total}\n"
            f"  - known chick deaths: {known_chick_deaths}\n"
        )

        prompt = (
            "You are the host of a Great Tit nest box live stream replying in YouTube chat. "
            "A viewer just asked or said something — write ONE short, grounded reply. "
            "Be friendly but factual; avoid gushing. Reference today's nest data when relevant. "
            "Keep it under 180 characters. No hashtags, no emojis, no @ mentions. "
            "Don't pretend to know things you weren't told. "
            "Vary your phrasing — avoid leading with the feed count every time.\n\n"
            f"VIEWER ({viewer_name}): {viewer_message}\n\n"
            "TODAY'S DATA:\n"
            f"  - feeds today: {stats.get('feeds_today', 0)}\n"
            f"  - AI-confirmed key moments: {stats.get('ai_confirmed', 0)}\n"
            f"  - last visit: {stats.get('last_visit', '—')}\n"
            f"  - current nesting stage: {stats.get('stage', 'unknown')}\n"
            f"{chick_age_line}"
            f"{chick_count_line}"
            f"{egg_lines}"
            "\nUSEFUL FACTS (use only if directly relevant to the viewer's question):\n"
            "  - Chicks produce fecal sacs (gelatinous poo parcels) that parents carry away.\n"
            "  - A nestling can produce ~50 fecal sacs per day in week 1.\n"
            "  - Older chicks back up to the entrance and eject the sac out of the hole.\n"
            "  - Parents make 400-1000 feeding trips per day at peak nestling stage.\n"
            "  - Chicks cannot regulate temperature until ~day 10, hence brooding.\n"
            "  - Great Tit lifespan: typically 2-3 years in the wild; UK record is 15 years.\n"
            "  - Adults weigh just 14-22g — about the weight of a few coins.\n\n"
            "Reply (one line, no quotes):"
        )
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_model_text,
                contents=[prompt],
                config=types.GenerateContentConfig(temperature=0.7),
            )
            text = (response.text or "").strip().strip('"').strip("'")
            # Gemini sometimes emits a leading "Reply:" or similar — trim that.
            for prefix in ("Reply:", "REPLY:", "Host:"):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
            return text[:200] or None
        except Exception:
            logger.exception("Chat response generation failed")
            return None

    def health_check(self) -> bool:
        """Returns True if the client is initialized."""
        if not self.client:
            logger.warning("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")
            return False
        return True
