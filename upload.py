"""Upload a video to YouTube using a resumable, retrying upload."""

import logging
import time

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from Google import Create_Service

log = logging.getLogger(__name__)

CLIENT_SECRET_FILE = "client_secret_972392739439-jusfhmj94lvc65co2gnruul7geg6imnf.apps.googleusercontent.com.json"
API_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Retry the chunk upload on these transient HTTP statuses.
_RETRY_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 5


def upload_video(title, desc, filename, tags=("Shorts",), priv="public"):
    service = Create_Service(CLIENT_SECRET_FILE, API_NAME, API_VERSION, SCOPES)
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
