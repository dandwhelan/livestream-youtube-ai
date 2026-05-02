import subprocess
import threading
import logging
from datetime import datetime
from pathlib import Path
import shutil

from config.settings import settings

logger = logging.getLogger(__name__)


class ClipExtractor:
    """
    Records video clips from the RTMP analysis stream using FFmpeg.

    start_clip() launches FFmpeg as a subprocess reading from rtmp://localhost:1935/analysis.
    stop_clip() sends 'q' to FFmpeg stdin for a graceful stop (preserves moov atom).
    Thread-safe: only one active recording at a time.
    """

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._current_clip_path: Path | None = None
        self._lock = threading.Lock()

    def start_clip(self, event_start: datetime) -> Path:
        """
        Start recording. Returns the output path.
        If a clip is already recording, stops it first.
        """
        with self._lock:
            if self._process is not None:
                logger.warning("Clip already recording — stopping previous clip first")
                self._stop_process()

            settings.clips_dir.mkdir(parents=True, exist_ok=True)
            filename = event_start.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
            output_path = settings.clips_dir / filename

            ffmpeg_bin = shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe"
            
            # Fragmented MP4: writes moov at the start and self-contained
            # fragments throughout, so the file stays playable even if the
            # recording is interrupted. Plain +faststart with -c copy was
            # producing unreadable files because the final moov-rewrite pass
            # was being cut short by the graceful-quit timeout.
            cmd = [
                ffmpeg_bin,
                "-loglevel", "error",
                "-i", settings.camera_rtmp_url,
                "-c", "copy",
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                "-t", str(settings.max_clip_duration_seconds),
                "-y",
                str(output_path),
            ]

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._current_clip_path = output_path
            logger.info("Clip recording started: %s", output_path)
            return output_path

    def stop_clip(self) -> Path | None:
        """
        Gracefully stops recording by sending 'q' to FFmpeg stdin.
        Waits up to 15s for FFmpeg to finish writing the file.
        Returns the completed clip path, or None if nothing was recording.
        """
        with self._lock:
            if self._process is None:
                return None
            path = self._current_clip_path
            self._stop_process()
            return path

    def _stop_process(self) -> None:
        """Internal: stop FFmpeg process. Must be called with _lock held."""
        if self._process is None:
            return
        try:
            self._process.stdin.write(b"q\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg did not stop gracefully — terminating")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        stderr_output = b""
        try:
            stderr_output = self._process.stderr.read()
        except Exception:
            pass
        if self._process.returncode not in (0, None):
            logger.debug("FFmpeg exit %d: %s", self._process.returncode, stderr_output[-500:])

        # Explicitly close pipe handles so GC doesn't trip OSError 22 on Windows
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        self._process = None
        logger.info("Clip recording stopped: %s", self._current_clip_path)

    def is_recording(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None
