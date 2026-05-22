"""
Great Tit facts poster.

Posts a rotating educational "Did you know?" fact about Great Tits to
YouTube live chat approximately every 90 minutes. Facts cover biology,
behaviour, chick development, poo hygiene, lifespan, and more — giving
viewers variety between the routine feed-count updates.
"""

import logging
import threading

from config.settings import settings

logger = logging.getLogger(__name__)

_FACTS = [
    "Did you know? Great Tit chicks produce their droppings in neat gelatinous 'fecal sacs' — like little poo parcels — which parents carry well away from the nest to keep it clean.",
    "Did you know? In the first week of life, a single nestling can produce up to 50 fecal sacs per day! The parents are on constant poo patrol.",
    "Did you know? Older chicks back up to the nest entrance and fire their fecal sac directly out of the hole — no parent collection needed. Chick hygiene, sorted.",
    "Did you know? Great Tit parents can make 400–1,000 feeding trips per day during peak nestling stage. It's a relentless, full-time operation.",
    "Did you know? Nestlings are fed almost entirely on caterpillars — soft, protein-packed and perfect for tiny growing beaks.",
    "Did you know? Great Tit chicks are born completely blind. Their eyes open at around 5–7 days old.",
    "Did you know? Chicks can't regulate their own body temperature for their first 10 days. That's why mum broods (sits on) them even after hatching — they'd chill rapidly without her.",
    "Did you know? Chicks start growing their first pin feathers at around 7–9 days old. Until then they are entirely naked and helpless.",
    "Did you know? Great Tits typically live 2–3 years in the wild, but the oldest ever recorded in the UK was 15 years old!",
    "Did you know? An adult Great Tit weighs just 14–22 grams — roughly the same as a few coins.",
    "Did you know? The black stripe running down a Great Tit's chest is wider in males. The wider the stripe, the more dominant the male.",
    "Did you know? The 'Great' in Great Tit simply means it's the largest of the UK tit family, which also includes the Blue Tit, Coal Tit, Long-tailed Tit and others.",
    "Did you know? The nest cup was built almost entirely by the female, using moss, grass and animal hair. She can spend up to two weeks constructing it.",
    "Did you know? The inner cup of a Great Tit nest is lined with soft hair, fur, wool and feathers — often collected from dead animals found nearby.",
    "Did you know? Parents remove broken eggshells from the nest immediately after hatching. The white interior could reflect light and alert predators to the nest's location.",
    "Did you know? Great Tit chicks hatch using a temporary 'egg tooth' — a tiny spike on their beak — to crack out of the shell. It disappears within a few days of hatching.",
    "Did you know? Even though a Great Tit lays one egg per day over a week or more, all the chicks hatch within 1–2 days of each other. Proper incubation only starts once the last egg is laid.",
    "Did you know? Great Tits are remarkable problem-solvers. In one famous study, they learned to open milk bottle tops by watching other birds do it — and the behaviour spread across the UK.",
    "Did you know? A typical Great Tit clutch contains 6–12 eggs. Our nest started with 7!",
    "Did you know? Chicks are fed from first light all the way to dusk — feeding only pauses overnight when visibility is too low to catch prey.",
    "Did you know? Both parents share feeding duties once the chicks hatch, though the female does most of the brooding to keep the brood warm.",
    "Did you know? Great Tit parents sometimes strip the hard head-capsules off caterpillars before feeding them to very young chicks — built-in food preparation!",
    "Did you know? After the chicks fledge, the parents continue feeding them outside the nest for another 2–3 weeks while they learn to find food for themselves.",
    "Did you know? Sparrowhawks are one of the Great Tit's biggest predators. The parents' sharp alarm calls alert the whole garden to danger.",
    "Did you know? In urban and suburban areas, Great Tits are almost entirely dependent on nest boxes like this one. Without them they would need a natural tree cavity — increasingly rare.",
    "Did you know? Great Tits sometimes cache seeds and insects under bark in autumn and return to eat them later. Their spatial memory for hidden food is surprisingly accurate.",
    "Did you know? A breeding pair defends a territory of roughly 1–2 hectares around their nest, though they travel much further afield to find enough food during nestling stage.",
    "Did you know? If a Great Tit pair successfully raises a first brood, they may attempt a second brood later in the season — sometimes back in the very same nest box!",
    # Off-topic palate cleansers — keep chat from feeling like a textbook.
    "Did you know? Octopuses have three hearts and blue blood. Completely unrelated to this nest box, but worth mentioning.",
    "Did you know? Bananas are berries. Strawberries are not. The Great Tit has no opinion on this.",
    "Did you know? A day on Venus is longer than its year. Meanwhile here, mum has been on the eggs for what feels like both.",
    "Did you know? Honey never spoils. Archaeologists have eaten 3,000-year-old honey from Egyptian tombs. Don't try that with the caterpillars.",
    "Did you know? Wombats produce cube-shaped poo. Great Tit chicks, frankly, can only dream.",
    "Did you know? There are more stars in the universe than grains of sand on Earth. And yet we're all here watching one bird.",
    "Did you know? The shortest war in history lasted 38 minutes (Anglo-Zanzibar, 1896). Most of mum's nest breaks are longer.",
    "Did you know? Cows have best friends and get stressed when separated. Make of that what you will while watching this bird sit alone in a box.",
    "Did you know? The inventor of the Pringles can is buried in one. No connection to Great Tits whatsoever — just thought you should know.",
    "Did you know? A group of flamingos is called a 'flamboyance'. A group of Great Tits is, disappointingly, just a 'banditry'. (Yes, really.)",
]


class FactsPoster:
    """Posts one rotating Great Tit fact to YouTube live chat on a timer."""

    def __init__(self, youtube_chapters):
        self._youtube = youtube_chapters
        self._index = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.facts_poster_enabled:
            logger.info("Facts poster disabled.")
            return
        self._thread = threading.Thread(target=self._loop, name="FactsPoster", daemon=True)
        self._thread.start()
        logger.info(
            "Facts poster armed: posting a Great Tit fact every %d minutes.",
            settings.facts_poster_interval_minutes,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        interval = max(1, settings.facts_poster_interval_minutes) * 60
        while not self._stop_event.wait(interval):
            try:
                self._post_next()
            except Exception:
                logger.exception("Facts poster tick failed")

    def _post_next(self) -> None:
        if not self._youtube:
            return
        fact = _FACTS[self._index % len(_FACTS)]
        self._index += 1
        logger.info("Posting Great Tit fact %d: %s", self._index, fact[:80])
        self._youtube.post_message(fact)
