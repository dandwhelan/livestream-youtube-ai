import logging
import threading
import time
from pathlib import Path
from queue import Queue, Empty

from config.settings import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_TOKEN_FILE = Path("credentials/drive_token.json")


class DriveUploader:
    """
    Background upload queue for Google Drive.
    Uses OAuth2 with a stored refresh token (files owned by your Google account,
    not a service account — avoids storageQuotaExceeded on personal Drive).

    One-time setup: run  python auth_drive.py  to authorise and save the token.
    After that, main.py runs headlessly using the saved refresh token.
    """

    def __init__(self, activity_log):
        self._queue: Queue = Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._service = None
        self._folder_ids: dict[str, str] = {}
        self.activity_log = activity_log
        self._last_log_sync = 0.0
        self._enabled = False

    def start(self) -> None:
        if not _TOKEN_FILE.exists():
            logger.warning(
                "Drive token not found at %s. "
                "Run  python auth_drive.py  once to authorise. "
                "Clips will only be saved locally until then.",
                _TOKEN_FILE,
            )
            return

        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
            self._enabled = True
        except ImportError:
            logger.error("google-api-python-client not installed. Run: pip install -r requirements.txt")
            return
        except Exception:
            logger.exception("Failed to initialise Google Drive client")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._upload_worker, name="DriveUploader", daemon=True
        )
        self._thread.start()
        logger.info("DriveUploader started")

    def enqueue(self, clip_path: Path, entry_id: str, is_key_moment: bool = False) -> None:
        if not self._enabled:
            return
        self._queue.put((clip_path, entry_id, is_key_moment))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _get_or_create_folder(self, name: str, parent_id: str | None = None) -> str:
        if name in self._folder_ids:
            return self._folder_ids[name]

        query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = (
            self._service.files()
            .list(q=query, fields="files(id, name)", spaces="drive")
            .execute()
        )
        files = results.get("files", [])

        if files:
            folder_id = files[0]["id"]
        else:
            metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                metadata["parents"] = [parent_id]
            folder = self._service.files().create(body=metadata, fields="id").execute()
            folder_id = folder["id"]
            logger.info("Created Drive folder: %s (%s)", name, folder_id)

        self._folder_ids[name] = folder_id
        return folder_id

    def _upload_file(self, local_path: Path, folder_id: str, mimetype: str) -> str:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(local_path), mimetype=mimetype, resumable=True)
        file_metadata = {"name": local_path.name, "parents": [folder_id]}
        file = (
            self._service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        file_id = file["id"]
        return f"https://drive.google.com/file/d/{file_id}/view"

    def _upload_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                clip_path, entry_id, is_key_moment = self._queue.get(timeout=5)
            except Empty:
                self._maybe_sync_log()
                continue

            if not clip_path.exists():
                logger.warning("Clip not found, skipping upload: %s", clip_path)
                self.activity_log.update_drive_url(entry_id, "", "failed")
                self._queue.task_done()
                continue

            try:
                root_id = self._get_or_create_folder(settings.drive_folder_name)
                
                if is_key_moment:
                    folder_id = self._get_or_create_folder(
                        settings.drive_key_moments_subfolder, parent_id=root_id
                    )
                else:
                    folder_id = self._get_or_create_folder(
                        settings.drive_clips_subfolder, parent_id=root_id
                    )
                    
                drive_url = self._upload_file(clip_path, folder_id, "video/mp4")
                self.activity_log.update_drive_url(entry_id, drive_url, "uploaded")
                logger.info("Uploaded %s → %s", clip_path.name, drive_url)
            except Exception:
                logger.exception("Drive upload failed for %s", clip_path)
                self.activity_log.update_drive_url(entry_id, "", "failed")

            self._queue.task_done()
            self._maybe_sync_log()

    def _maybe_sync_log(self) -> None:
        now = time.time()
        if now - self._last_log_sync < settings.drive_log_sync_interval:
            return
        self._last_log_sync = now
        try:
            root_id = self._get_or_create_folder(settings.drive_folder_name)
            log_path = settings.activity_log_path
            if log_path.exists():
                self._upload_file(log_path, root_id, "application/json")
                logger.debug("Activity log synced to Drive")
        except Exception:
            logger.debug("Log sync to Drive failed (non-fatal)")
