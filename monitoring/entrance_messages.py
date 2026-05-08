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
    "She's back! Chick check incoming.",
    "Mum's back at the box.",
    "Parent just popped in.",
    "Welcome home — chicks about to get cosy.",
    "Back already — that was quick!",
    "She's home, fluffing the nest as usual.",
    "Mum's just landed at the entrance.",
    "Parent's back at the box.",
    "Visit time — settling in for a brood.",
    "She's in! Cosy time for the little ones.",
)

_LEAVING = (
    "Off she goes — back soon, hopefully with food!",
    "Mum's heading out for a forage.",
    "Parent just slipped out.",
    "Off again — those chicks won't feed themselves.",
    "She's away to find the next caterpillar.",
    "Parent off on a food run.",
    "Nest is briefly empty — mum's out.",
    "She's off — fly safe!",
    "Heading out for snacks.",
    "Parent leaving the box.",
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
