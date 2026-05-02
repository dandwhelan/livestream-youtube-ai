import logging
from PIL import Image
from google import genai
from google.genai import types
import cv2
import numpy as np

from config.settings import settings

logger = logging.getLogger(__name__)

PROMPT = (
    "You are a wildlife expert watching a live Great Tit nest box camera. "
    "A motion event was just detected. "
    "Analyze the image and describe exactly what is happening in 1 short sentence. "
    "Focus heavily on identifying KEY MOMENTS: is a bird entering/leaving, "
    "bringing food (grubs, insects), or are eggs hatching? "
    "If it's just the bird sitting still and brooding, state that. "
    "\n\n"
    "IMPORTANT — also check if there is a DIFFERENT bird or animal in the box. "
    "A Great Tit has a yellow-green body, black head with white cheeks, and a black stripe down the belly. "
    "If you see a different species (e.g. a Blue Tit, House Sparrow, or any other animal), "
    "start your response with 'INTRUDER: ' followed by what you see. "
    "\n\n"
    "If it's a key moment (feeding, hatching, bird returning with food), "
    "start your response with 'KEY_MOMENT: ' followed by a fun, engaging YouTube Live Chat comment. "
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
                    PROMPT,
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
