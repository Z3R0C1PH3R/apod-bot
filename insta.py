"""Publish a video as an Instagram Reel via the Facebook Graph API."""

import logging
import os
from datetime import datetime
from sys import argv
from time import sleep

import requests

from utils import retry

log = logging.getLogger(__name__)

GRAPH_VERSION = "v20.0"
USER_ID = os.getenv("INSTAGRAM_USER_ID", "17841467200020320")

# How long to wait for Instagram to finish ingesting the video before publishing.
MAX_PUBLISH_ATTEMPTS = 15
PUBLISH_POLL_SECONDS = 20


class InstagramError(RuntimeError):
    """Raised when the Instagram Graph API returns an error we cannot recover from."""


@retry(exceptions=(requests.RequestException,), tries=3, delay=5.0)
def _post(path: str, data: dict) -> dict:
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{path}", data=data, timeout=60
    )
    return resp.json()


def upload_reel(url: str, caption: str) -> str:
    """Create and publish an Instagram Reel from a public video URL.

    Returns the published media id. Raises InstagramError on failure.
    """
    access_token = os.environ["INSTAGRAM_ACCESS_TOKEN"]

    log.info("Verifying video is reachable: %s", url)
    head = requests.head(url, timeout=30, allow_redirects=True)
    head.raise_for_status()

    log.info("Creating Instagram media container")
    resp = _post(
        f"{USER_ID}/media",
        {
            "video_url": url,
            "caption": caption,
            "media_type": "REELS",
            "access_token": access_token,
        },
    )
    if "error" in resp or "id" not in resp:
        raise InstagramError(f"Failed to create media container: {resp}")

    creation_id = resp["id"]
    log.info("Media container %s created; waiting for ingestion", creation_id)

    last = None
    for attempt in range(1, MAX_PUBLISH_ATTEMPTS + 1):
        sleep(PUBLISH_POLL_SECONDS)
        last = _post(
            f"{USER_ID}/media_publish",
            {"creation_id": creation_id, "access_token": access_token},
        )
        if "error" not in last and "id" in last:
            log.info("Published reel: %s", last["id"])
            return last["id"]
        log.info(
            "Not ready yet (attempt %d/%d): %s",
            attempt,
            MAX_PUBLISH_ATTEMPTS,
            last.get("error", {}).get("error_user_msg", last),
        )

    raise InstagramError(f"Reel did not publish in time; last response: {last}")


if __name__ == "__main__":
    from utils import setup_logging

    setup_logging()
    if len(argv) == 2:
        date = argv[1]
    else:
        date = datetime.today().strftime("%Y-%m-%d")

    host = os.environ.get("VIDEO_HOST_URL", "").rstrip("/")
    if not host:
        raise SystemExit("Set VIDEO_HOST_URL to the public base URL for videos")
    upload_reel(
        f"{host}/{date}.mp4",
        f"NASA's APOD of {date}. #nasa #apod #space #astronomy #astrophotography #explore #reels #fyp #foryoupage",
    )
