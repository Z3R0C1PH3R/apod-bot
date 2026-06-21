"""Upload a video to YouTube using a resumable, retrying upload."""

import glob
import logging
import os
import time

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from Google import Create_Service

log = logging.getLogger(__name__)


def _client_secret_file() -> str:
    """Locate the Google OAuth client secret JSON.

    Prefers GOOGLE_CLIENT_SECRET_FILE, otherwise the first matching
    client_secret*.json in the working directory.
    """
    explicit = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
    if explicit:
        return explicit
    matches = sorted(glob.glob("client_secret*.json"))
    if not matches:
        raise FileNotFoundError(
            "No Google client secret found. Set GOOGLE_CLIENT_SECRET_FILE or place a "
            "client_secret*.json in the working directory."
        )
    return matches[0]


API_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Retry the chunk upload on these transient HTTP statuses.
_RETRY_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 5


def upload_video(title, desc, filename, tags=("Shorts",), priv="public"):
    service = Create_Service(_client_secret_file(), API_NAME, API_VERSION, SCOPES)
    if service is None:
        raise RuntimeError("Could not create YouTube service")

    request_body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": list(tags),
        },
        "status": {
            "privacyStatus": priv,
            "selfDeclaredMadeForKids": False,
        },
        "notifySubscribers": True,
    }

    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = service.videos().insert(
        part="snippet,status", body=request_body, media_body=media
    )

    log.info("Uploading %s to YouTube (%s)", filename, priv)
    response = None
    retries = 0
    while response is None:
        try:
            _status, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in _RETRY_STATUSES and retries < _MAX_RETRIES:
                retries += 1
                sleep = 2 ** retries
                log.warning(
                    "YouTube %s; retry %d/%d in %ds",
                    exc.resp.status,
                    retries,
                    _MAX_RETRIES,
                    sleep,
                )
                time.sleep(sleep)
                continue
            raise

    log.info("Uploaded video id=%s", response.get("id"))
    return response
