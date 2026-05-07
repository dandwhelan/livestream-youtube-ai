import logging
from datetime import datetime, timedelta
from PIL import Image
from google import genai
from google.genai import types
import cv2
import numpy as np

from config.settings import settings, current_stage

logger = logging.getLogger(__name__)


STAGE_CONTEXT = {
    "nestling": (
        "STAGE CONTEXT — NESTLING PHASE: The eggs have hatched. "
        "Tiny pink/grey naked chicks with closed eyes may be visible under or beside the mother. "
        "Both parents now feed the chicks: when you see TWO Great Tits in the box together, "
        "that is the DAD arriving to feed — flag this as a KEY MOMENT. "
        "Expect frequent food deliveries (caterpillars, grubs, insects); every successful feeding is a key moment. "
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
) -> str:
    stage_text = STAGE_CONTEXT.get(stage, STAGE_CONTEXT["empty"])

    persona = (
        "You are the host of a beloved Great Tit nest box live stream. "
        "Your audience are fans of these birds — write warmly and conversationally, "
        "with light affection or playfulness when it fits. Avoid academic phrasing. "
        "Keep it punchy: ideally one short sentence. "
        "Once in a while (not often) it's nice to nudge viewers to chat back, "
        "e.g. 'anyone else holding their breath right now?' — but only when it actually feels natural."
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
        "(d) a non-Great-Tit species (sparrow, woodpecker, predator), "
        "(e) parts of a bird visible at the entrance hole. "
        "If you spot any of these, report it — it matters even if the main action is something else."
    )

    parts.append(stage_text)

    parts.append(
        "Output rules — start your response with EXACTLY ONE of these prefixes:\n"
        "  'ALERT: '       → a chick displaced from the nest cup, a motionless/limp body, "
        "an intruder species, a damaged egg, or any other welfare concern. "
        "Follow with a clear, urgent description naming WHERE in the frame the issue is "
        "(e.g. 'bottom-left corner', 'near the entrance'). This stays visible to viewers.\n"
        "  'KEY_MOMENT: '  → feeding, food delivery, dad visiting, eggshell/fecal-sac removal, "
        "hatching, first activity of the day, or anything else genuinely chat-worthy. "
        "Follow with a fun, engaging comment in the host's voice.\n"
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


_CHICK_COUNT_PROMPT = (
    "You are looking at a Great Tit nest box from above. The mother has just left "
    "and the chicks should now be visible in the nest cup. "
    "Count the number of chicks you can clearly see. "
    "Respond with ONLY a single integer (e.g. '5'). "
    "If you cannot see any chicks or cannot tell, respond with '0'."
)


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
        "Below is the chronological event log for the day. Write a short, warm, "
        "engaging recap (≤400 characters, suitable for YouTube live chat) covering: "
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

    def count_chicks(self, frame: np.ndarray) -> int | None:
        """Returns an integer chick count if Gemini can read it, else None."""
        if not self.client:
            return None
        try:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            response = self.client.models.generate_content(
                model=self.model,
                contents=[pil_img, _CHICK_COUNT_PROMPT],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = (response.text or "").strip()
            digits = "".join(ch for ch in text if ch.isdigit())
            if not digits:
                return None
            count = int(digits)
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
                model=self.model,
                contents=[_summary_prompt(events)],
                config=types.GenerateContentConfig(temperature=0.6),
            )
            text = (response.text or "").strip()
            return text or None
        except Exception as e:
            logger.exception("Daily summary generation failed: %s", e)
            return None

    def health_check(self) -> bool:
        """Returns True if the client is initialized."""
        if not self.client:
            logger.warning("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")
            return False
        return True
