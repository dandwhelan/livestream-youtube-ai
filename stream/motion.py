import cv2
import numpy as np
import threading
import logging
from datetime import datetime
from typing import Callable

from config.settings import settings

logger = logging.getLogger(__name__)


class MotionDetector:
    """
    Frame differencing motion detector with exponential background model
    and zone-based detection.

    Zones:
      - Entrance (top 12% of frame): very sensitive, catches arrivals/departures
      - Nest (bottom 88%): less sensitive, ignores mum fidgeting

    Features:
      - Direction detection: tracks if bird is entering or leaving via entrance
      - Predator alert: entrance motion at night flagged as suspicious
      - Zone logging: reports which zone triggered

    Emits:
      on_motion_start(frame, timestamp, buffer_snapshot, motion_info)
      on_motion_end(last_timestamp, motion_info)
    """

    def __init__(
        self,
        on_motion_start: Callable,
        on_motion_end: Callable,
    ):
        self.on_motion_start = on_motion_start
        self.on_motion_end = on_motion_end

        self._background: np.ndarray | None = None
        self._motion_active = False
        self._last_motion_time: datetime | None = None
        self._last_ai_call_time: datetime | None = None
        self._motion_end_timer: threading.Timer | None = None
        self._lock = threading.Lock()

        # Direction tracking for entrance zone
        self._entrance_centroids: list[int] = []  # y-positions during event
        self._current_zone: str | None = None

    def process_frame(
        self,
        frame: np.ndarray,
        timestamp: datetime,
        buffer_snapshot: list,
    ) -> bool:
        """
        Returns True if motion detected this frame.
        Uses zone-based detection with direction tracking.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._background is None or self._background.shape != gray.shape:
            self._background = gray.astype(np.float32)
            return False

        bg_uint8 = self._background.astype(np.uint8)
        diff = cv2.absdiff(bg_uint8, gray)
        _, thresh = cv2.threshold(diff, settings.motion_threshold, 255, cv2.THRESH_BINARY)
        dilated = cv2.dilate(thresh, None, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Zone boundary (in pixels)
        frame_height = frame.shape[0]
        entrance_cutoff = int(frame_height * settings.entrance_zone_bottom)

        # Check each contour against zones
        entrance_motion = False
        nest_motion = False
        entrance_centroid_y = None

        for c in contours:
            area = cv2.contourArea(c)
            _, y, _, h = cv2.boundingRect(c)
            center_y = y + h // 2

            if center_y < entrance_cutoff:
                # Entrance zone — low threshold (bird arriving/leaving)
                if area > settings.motion_min_area:
                    entrance_motion = True
                    # Track the largest contour's centroid for direction detection
                    if entrance_centroid_y is None or area > settings.motion_min_area:
                        entrance_centroid_y = center_y
            else:
                # Nest zone — high threshold (ignore fidgeting)
                if area > settings.motion_min_area_nest:
                    nest_motion = True

        motion_found = entrance_motion or nest_motion

        # Track entrance centroid for direction detection during active motion
        if entrance_motion and entrance_centroid_y is not None:
            self._entrance_centroids.append(entrance_centroid_y)

        if motion_found:
            self._last_motion_time = timestamp

            # HARD TIMEOUT: if the event has been going longer than max clip duration, force end it
            if self._motion_active and self._last_ai_call_time:
                elapsed_event_time = (timestamp - self._last_ai_call_time).total_seconds()
                if elapsed_event_time >= settings.max_clip_duration_seconds:
                    logger.warning("Event reached max duration (%ss) — forcing end", settings.max_clip_duration_seconds)
                    self._fire_motion_end(timestamp)

            self._reschedule_motion_end(timestamp)

            if not self._motion_active and self._should_call_ai():
                self._motion_active = True
                self._last_ai_call_time = timestamp
                self._entrance_centroids = []  # reset for new event
                if entrance_centroid_y is not None:
                    self._entrance_centroids.append(entrance_centroid_y)

                zone = "entrance" if entrance_motion else "nest"
                self._current_zone = zone

                motion_info = {
                    "zone": zone,
                }

                logger.info(
                    "Motion start at %s (zone: %s)",
                    timestamp.isoformat(),
                    zone,
                )
                try:
                    self.on_motion_start(frame, timestamp, buffer_snapshot, motion_info)
                except Exception:
                    logger.exception("on_motion_start callback error")

        # Always update background model to prevent lighting-change deadlocks.
        # Fast update during quiet (alpha=0.05), extremely slow during motion (alpha=0.001)
        alpha = 0.001 if motion_found else 0.05
        cv2.accumulateWeighted(gray.astype(np.float32), self._background, alpha=alpha)

        return motion_found

    def _determine_direction(self) -> str:
        """Determine bird direction from entrance centroid tracking."""
        if len(self._entrance_centroids) < 3:
            return "unknown"

        first_y = self._entrance_centroids[0]
        last_y = self._entrance_centroids[-1]
        delta = last_y - first_y

        if delta > 5:
            return "entering"  # moving downward into nest
        elif delta < -5:
            return "leaving"   # moving upward out of nest
        return "unknown"

    def _should_call_ai(self) -> bool:
        if self._last_ai_call_time is None:
            return True

        elapsed = (datetime.now() - self._last_ai_call_time).total_seconds()

        # Dynamic cooldown based on time of day (Night = 10 PM to 5 AM)
        hour = datetime.now().hour
        is_night = hour >= 22 or hour <= 5

        # 1 hour cooldown at night, 5 minute cooldown during day to save Gemini/YouTube quota
        required_cooldown = 3600 if is_night else 300

        return elapsed >= required_cooldown

    def _reschedule_motion_end(self, timestamp: datetime) -> None:
        with self._lock:
            if self._motion_end_timer is not None:
                self._motion_end_timer.cancel()
            self._motion_end_timer = threading.Timer(
                settings.post_event_seconds,
                self._fire_motion_end,
                args=(timestamp,),
            )
            self._motion_end_timer.daemon = True
            self._motion_end_timer.start()

    def _fire_motion_end(self, last_timestamp: datetime) -> None:
        with self._lock:
            self._motion_active = False
            self._motion_end_timer = None

        direction = self._determine_direction()
        zone = self._current_zone or "unknown"

        motion_info = {
            "zone": zone,
            "direction": direction,
        }

        logger.info(
            "Motion end at %s (zone: %s, direction: %s)",
            last_timestamp.isoformat(),
            zone,
            direction,
        )
        try:
            self.on_motion_end(last_timestamp, motion_info)
        except Exception:
            logger.exception("on_motion_end callback error")

        self._entrance_centroids = []
        self._current_zone = None

    def reset(self) -> None:
        with self._lock:
            if self._motion_end_timer is not None:
                self._motion_end_timer.cancel()
                self._motion_end_timer = None
            self._background = None
            self._motion_active = False
            self._last_motion_time = None
            self._last_ai_call_time = None
            self._entrance_centroids = []
            self._current_zone = None
