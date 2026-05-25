"""
Templated chat messages for entrance-zone motion events.

Entry/exit at the box is unambiguous from motion alone — zone tells us
the bird is at the entrance, the direction tracker tells us in or out.
We used to spend a Gemini vision call here just to produce flavour text,
but the AI's classification was unreliable and the cost added up. This
module provides a rotating pool of warm, host-style messages instead.

Variety comes from round-robin rotation per direction, so we don't
repeat the same line twice in a row.

During the fledging stage, the entrance is primarily an exit point —
any departure could be a chick fledging, not just a parent foraging.
Separate message pools are used for that stage.
"""

import threading

from config.settings import current_stage


_ENTERING = (
    "Parent back at the box.",
    "She's in — brood or feed, we'll see.",
    "Mum's returned. Chicks getting some warmth.",
    "Back again. Didn't hang about.",
    "Parent in. Chicks can stop shivering.",
    "She's home. The brooding shift continues.",
    "In she comes. Thermoregulation sorted.",
    "Parent just landed — typical turnaround.",
    "Mum's back. Nothing unusual so far.",
    "Another visit. Consistent work from this bird.",
    "She's back. At this rate she hasn't stopped all morning.",
    "Parent returned. The chicks will have noticed.",
)

_LEAVING = (
    "Off she goes.",
    "Mum's heading out — foraging run.",
    "Parent just left.",
    "Out for another food run.",
    "She's away. Chicks on their own for now.",
    "Parent off — back shortly with a caterpillar, most likely.",
    "Nest is empty. Won't be for long.",
    "She's out. Average absence is a few minutes at this stage.",
    "Off again — those caterpillars don't collect themselves.",
    "Parent heading out. Standard foraging interval.",
)

# During fledging the entrance is the exit. Every departure could be THE moment.
_FLEDGE_LEAVING = (
    "Something just left the box. Chick count updating shortly.",
    "Exit through the hole. Could be mum — could be a chick. Watch closely.",
    "Departure logged. Every exit this week is one to pay attention to.",
    "Out through the entrance. AI chick count incoming.",
    "Left the box. If that was a chick, fledge clock has started.",
    "Exit. One of these is going to be the actual fledge — this might be it.",
    "Something headed out. The count will update in a moment.",
    "Out the hole. Parents are running out of reasons to keep coming back.",
    "Departure. Four chicks, zero guarantees who's still inside.",
    "Gone. Checking who's left.",
)

_FLEDGE_ENTERING = (
    "Something came in. Still got some brave ones left in the box.",
    "Back through the hole — parent visit or a chick reconsidering.",
    "Parent at the entrance. May be food-teasing rather than going fully in.",
    "In. Whoever's still inside will have noticed.",
    "Returned to the box. Won't be long now.",
    "Entry logged. Fledging usually follows a few of these.",
)

_UNKNOWN = (
    "Movement at the entrance.",
    "Parent at the box.",
    "Bird visiting the entrance.",
)

_FLEDGE_UNKNOWN = (
    "Movement at the entrance. Could be anything at this stage.",
    "Entrance activity. Chick count will update shortly.",
    "Something at the hole.",
)

_lock = threading.Lock()
_indices: dict[str, int] = {
    "entering": 0, "leaving": 0, "unknown": 0,
    "fledge_entering": 0, "fledge_leaving": 0, "fledge_unknown": 0,
}


def message_for(direction: str) -> str:
    """Returns the next templated message for the given direction.
    Round-robin per direction to keep variety without repeating.
    During fledging, uses separate pools that treat exits as potential fledges."""
    fledging = current_stage() == "fledging"
    if fledging:
        pool = {
            "entering": _FLEDGE_ENTERING,
            "leaving": _FLEDGE_LEAVING,
        }.get(direction, _FLEDGE_UNKNOWN)
        key = f"fledge_{direction}" if direction in ("entering", "leaving") else "fledge_unknown"
    else:
        pool = {
            "entering": _ENTERING,
            "leaving": _LEAVING,
        }.get(direction, _UNKNOWN)
        key = direction if direction in ("entering", "leaving") else "unknown"
    with _lock:
        idx = _indices[key]
        _indices[key] = (idx + 1) % len(pool)
    return pool[idx % len(pool)]
