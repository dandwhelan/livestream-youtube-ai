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

        # Debug view state — populated each frame when debug is enabled
        self._debug_enabled: bool = False
        self._debug_lock = threading.Lock()
        self._latest_debug_jpeg: bytes | None = None
        self._latest_stats: dict = {
            "contours": 0,
            "max_area_entrance": 0,
            "max_area_nest": 0,
            "entrance_motion": False,
            "nest_motion": False,
            "ai_cooldown_remaining": 0,
        }

    def set_debug_enabled(self, enabled: bool) -> None:
        with self._debug_lock:
            self._debug_enabled = enabled
            if not enabled:
                self._latest_debug_jpeg = None

    def is_debug_enabled(self) -> bool:
        with self._debug_lock:
            return self._debug_enabled

    def get_debug_jpeg(self) -> bytes | None:
        with self._debug_lock:
            return self._latest_debug_jpeg

    def get_stats(self) -> dict:
        with self._debug_lock:
            return dict(self._latest_stats)

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
        max_area_entrance = 0
        max_area_nest = 0
        contour_info: list[tuple] = []  # (x, y, w, h, area, zone, passed) — for debug overlay

        for c in contours:
            area = cv2.contourArea(c)
            x, y, w, h = cv2.boundingRect(c)
            center_y = y + h // 2

            if center_y < entrance_cutoff:
                zone_label = "entrance"
                passed = area > settings.motion_min_area
                if area > max_area_entrance:
                    max_area_entrance = int(area)
                if passed:
                    entrance_motion = True
                    if entrance_centroid_y is None or area > settings.motion_min_area:
                        entrance_centroid_y = center_y
            else:
                zone_label = "nest"
                passed = area > settings.motion_min_area_nest
                if area > max_area_nest:
                    max_area_nest = int(area)
                if passed:
                    nest_motion = True

            contour_info.append((x, y, w, h, int(area), zone_label, passed))

        motion_found = entrance_motion or nest_motion

        # Track entrance centroid for direction detection during active motion
        if entrance_motion and entrance_centroid_y is not None:
            self._entrance_centroids.append(entrance_centroid_y)

        if motion_found:
            self._last_motion_time = timestamp
            
            # 1. Hard timeout: if event exceeds max_clip_duration_seconds, force end it.
            # This ensures we don't get infinite "pending" clips if the bird is fidgety.
            if self._motion_active and self._last_ai_call_time:
                elapsed = (timestamp - self._last_ai_call_time).total_seconds()
                if elapsed >= settings.max_clip_duration_seconds:
                    logger.warning("Event reached max duration (%ss) - forcing end", settings.max_clip_duration_seconds)
                    self._fire_motion_end(timestamp)
                    self._motion_active = False # Will restart a new clip immediately if still moving

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
        
        # 2. Always update background model to prevent lighting change deadlocks.
        # Use a very slow alpha during motion to avoid 'erasing' the bird.
        alpha = 0.001 if motion_found else 0.02
        cv2.accumulateWeighted(gray.astype(np.float32), self._background, alpha=alpha)

        # 3. Debug overlay (only when enabled) — annotate frame and stash JPEG
        with self._debug_lock:
            debug_on = self._debug_enabled
        if debug_on:
            self._render_debug(
                frame, entrance_cutoff, contour_info,
                entrance_motion, nest_motion, motion_found,
                max_area_entrance, max_area_nest,
            )

        return motion_found

    def _render_debug(
        self,
        frame: np.ndarray,
        entrance_cutoff: int,
        contour_info: list[tuple],
        entrance_motion: bool,
        nest_motion: bool,
        motion_found: bool,
        max_area_entrance: int,
        max_area_nest: int,
    ) -> None:
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        # Zone divider
        cv2.line(annotated, (0, entrance_cutoff), (w, entrance_cutoff), (0, 200, 255), 2)
        cv2.putText(annotated, "ENTRANCE", (8, max(14, entrance_cutoff - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
        cv2.putText(annotated, "NEST", (8, entrance_cutoff + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)

        # Contour boxes — green if passed threshold, dim red if rejected
        for cx, cy, cw, ch, area, zone_label, passed in contour_info:
            color = (60, 220, 60) if passed else (60, 60, 200)
            cv2.rectangle(annotated, (cx, cy), (cx + cw, cy + ch), color, 1)
            label = f"{zone_label[0].upper()} {area}"
            cv2.putText(annotated, label, (cx, max(10, cy - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

        # HUD: current thresholds
        hud = [
            f"thresh={settings.motion_threshold}",
            f"min_entrance={settings.motion_min_area}",
            f"min_nest={settings.motion_min_area_nest}",
            f"zone_split={settings.entrance_zone_bottom:.2f}",
            f"max_area E/N={max_area_entrance}/{max_area_nest}",
        ]
        for i, line in enumerate(hud):
            cv2.putText(annotated, line, (8, h - 10 - (len(hud) - 1 - i) * 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        # Motion badge
        if motion_found:
            badge_text = "MOTION: " + ("entrance" if entrance_motion else "") \
                         + ("+nest" if entrance_motion and nest_motion else "nest" if nest_motion else "")
            cv2.rectangle(annotated, (w - 240, 8), (w - 8, 36), (0, 0, 200), -1)
            cv2.putText(annotated, badge_text, (w - 232, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        # AI cooldown indicator
        cooldown_remaining = 0
        if self._last_ai_call_time is not None:
            hour = datetime.now().hour
            is_night = hour >= 22 or hour <= 5
            required = 3600 if is_night else 300
            elapsed = (datetime.now() - self._last_ai_call_time).total_seconds()
            cooldown_remaining = max(0, int(required - elapsed))
        if cooldown_remaining > 0:
            cv2.putText(annotated, f"AI cooldown: {cooldown_remaining}s",
                        (w - 240, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (180, 180, 180), 1, cv2.LINE_AA)

        ok, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return

        with self._debug_lock:
            self._latest_debug_jpeg = jpeg.tobytes()
            self._latest_stats = {
                "contours": len(contour_info),
                "max_area_entrance": max_area_entrance,
                "max_area_nest": max_area_nest,
                "entrance_motion": entrance_motion,
                "nest_motion": nest_motion,
                "ai_cooldown_remaining": cooldown_remaining,
            }

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
