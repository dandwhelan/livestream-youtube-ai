import cv2
import threading
import time
import logging
from collections import deque
from datetime import datetime
from typing import Callable

from config.settings import settings

logger = logging.getLogger(__name__)


class StreamReader:
    """
    Reads RTMP stream in a background thread.
    Maintains a rolling pre-roll buffer of (timestamp, frame) tuples.
    Thread-safe: lock guards buffer and latest_frame access.
    Auto-reconnects on failure.
    """

    def __init__(self, url: str = settings.camera_rtmp_url, fps_hint: float = 25.0):
        self.url = url
        self.fps_hint = fps_hint
        max_frames = int(settings.frame_buffer_seconds * fps_hint)
        self._buffer: deque[tuple[datetime, "cv2.Mat"]] = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_frame: "cv2.Mat | None" = None
        self._latest_timestamp: datetime | None = None
        self._frame_callback: Callable | None = None

    def set_frame_callback(self, cb: Callable[["cv2.Mat", datetime, list], None]) -> None:
        """Called from reader thread for each new frame: cb(frame, timestamp, buffer_snapshot)."""
        self._frame_callback = cb

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, name="StreamReader", daemon=True)
        self._thread.start()
        logger.info("StreamReader started: %s", self.url)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("StreamReader stopped")

    def get_latest_frame(self) -> tuple["cv2.Mat | None", "datetime | None"]:
        with self._lock:
            return self._latest_frame, self._latest_timestamp

    def get_buffer_snapshot(self) -> list[tuple[datetime, "cv2.Mat"]]:
        """Returns a shallow copy of the current buffer contents."""
        with self._lock:
            return list(self._buffer)

    def _read_loop(self) -> None:
        reconnect_delay = 2
        while not self._stop_event.is_set():
            logger.info("Connecting to stream: %s", self.url)
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                logger.warning("Failed to open stream. Retrying in %ds...", reconnect_delay)
                time.sleep(reconnect_delay)
                continue

            actual_fps = cap.get(cv2.CAP_PROP_FPS) or self.fps_hint
            frame_interval = 1.0 / actual_fps
            logger.info("Stream opened. FPS: %.1f", actual_fps)

            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Frame read failed — reconnecting...")
                    break

                ts = datetime.now()
                with self._lock:
                    self._buffer.append((ts, frame))
                    self._latest_frame = frame
                    self._latest_timestamp = ts

                if self._frame_callback is not None:
                    snapshot = self.get_buffer_snapshot()
                    try:
                        self._frame_callback(frame, ts, snapshot)
                    except Exception:
                        logger.exception("Frame callback error")

                # Gentle throttle to avoid CPU spin if stream delivers faster than real-time
                time.sleep(max(0, frame_interval - 0.001))

            cap.release()
            if not self._stop_event.is_set():
                logger.info("Reconnecting in %ds...", reconnect_delay)
                time.sleep(reconnect_delay)
