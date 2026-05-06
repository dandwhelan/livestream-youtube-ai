import logging
from PIL import Image
from google import genai
from google.genai import types
import cv2
import numpy as np

from config.settings import settings

logger = logging.getLogger(__name__)

STAGE_CONTEXT = {
    "nestling": (
        "STAGE CONTEXT — NESTLING PHASE: The eggs have hatched (or are hatching now). "
        "Tiny pink/grey naked chicks with closed eyes may be visible under or beside the mother. "
        "Both parents now feed the chicks: when you see TWO Great Tits in the box together, "
        "that is the DAD arriving to feed — this is a KEY MOMENT, NOT an intruder. "
        "Expect frequent food deliveries (caterpillars, grubs, insects) — every successful feeding is a key moment. "
        "Other key moments include: a chick visibly being fed, eggshells being removed, "
        "fecal sacs being carried out (parental hygiene), or a freshly hatched chick. "
        "Intruders are VERY UNLIKELY at this stage — only flag one if you are highly confident "
        "the bird is clearly NOT a Great Tit (e.g. obvious squirrel, woodpecker, large raptor). "
        "Do NOT call a second Great Tit an intruder."
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

def build_prompt(stage: str) -> str:
    stage_text = STAGE_CONTEXT.get(stage, STAGE_CONTEXT["empty"])
    return (
        "You are a wildlife expert watching a live Great Tit nest box camera. "
        "A motion event was just detected. "
        "Analyze the image and describe exactly what is happening in 1 short sentence.\n\n"
        f"{stage_text}\n\n"
        "Identification: A Great Tit has a yellow-green body, black head with white cheeks, "
        "and a black stripe down the belly. The female and male look very similar — "
        "the male's belly stripe is thicker, but in nest-cam footage assume any Great Tit "
        "is a parent, not an intruder.\n\n"
        "Output rules — start your response with EXACTLY ONE of these prefixes:\n"
        "  'KEY_MOMENT: '  → for feeding, hatching, food delivery, dad visiting, eggshell/fecal-sac removal, "
        "or anything notable for the live chat. Follow with a fun, engaging YouTube Live Chat comment.\n"
        "  'INTRUDER: '   → ONLY for a clearly non-Great-Tit animal (and remember: very unlikely at this stage).\n"
        "  (no prefix)    → routine activity (mum brooding, sitting still, minor adjustments).\n"
        "Be extremely concise."
    )

class BirdDescriber:
    """
    Calls Google Gemini API to describe a single frame.
    Returns a tuple: (description_string, is_key_moment, is_intruder)
    """

    def __init__(self):
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY is not set. AI descriptions will be disabled.")
            self.client = None
        else:
            self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model
        self.stage = settings.nesting_stage
        logger.info(f"BirdDescriber initialised for nesting stage: {self.stage}")

    def describe_frame(self, frame: np.ndarray) -> tuple[str | None, bool, bool]:
        if not self.client:
            return None, False, False

        try:
            # Convert OpenCV frame (BGR) to PIL Image (RGB)
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)

            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    pil_img,
                    build_prompt(self.stage),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.4,
                )
            )
            
            description = response.text.strip()
            is_key_moment = False
            is_intruder = False
            
            if description.startswith("INTRUDER:"):
                is_intruder = True
                is_key_moment = True  # intruders are always key moments
                description = description.replace("INTRUDER:", "").strip()
            elif description.startswith("KEY_MOMENT:"):
                is_key_moment = True
                description = description.replace("KEY_MOMENT:", "").strip()
                
            if description:
                if is_intruder:
                    prefix = "🚨 INTRUDER"
                elif is_key_moment:
                    prefix = "🌟 KEY MOMENT"
                else:
                    prefix = "Routine"
                logger.info(f"AI Description [{prefix}]: {description}")
                
            return description or None, is_key_moment, is_intruder
            
        except Exception as e:
            logger.exception(f"Gemini AI description failed: {e}")
            return None, False, False

    def health_check(self) -> bool:
        """Returns True if the client is initialized."""
        if not self.client:
            logger.warning("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")
            return False
        return True
