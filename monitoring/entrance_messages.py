"""
Templated chat messages for entrance-zone motion events.

Entry/exit at the box is unambiguous from motion alone — zone tells us
the bird is at the entrance, the direction tracker tells us in or out.
We used to spend a Gemini vision call here just to produce flavour text,
but the AI's classification was unreliable and the cost added up. This
module provides a rotating pool of warm, host-style messages instead.

Variety comes from round-robin rotation per direction, so we don't
repeat the same line twice in a row.
"""

import threading


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

_UNKNOWN = (
    "Movement at the entrance.",
    "Parent at the box.",
    "Bird visiting the entrance.",
)

_lock = threading.Lock()
_indices: dict[str, int] = {"entering": 0, "leaving": 0, "unknown": 0}


def message_for(direction: str) -> str:
    """Returns the next templated message for the given direction.
    Round-robin per direction to keep variety without repeating."""
    pool = {
        "entering": _ENTERING,
        "leaving": _LEAVING,
    }.get(direction, _UNKNOWN)
    key = direction if direction in _indices else "unknown"
    with _lock:
        idx = _indices[key]
        _indices[key] = (idx + 1) % len(pool)
    return pool[idx % len(pool)]
