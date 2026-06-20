"""Fetch the NASA Astronomy Picture of the Day and download its image."""

import json
import logging
import os
import urllib.error
import urllib.request

from utils import retry

log = logging.getLogger(__name__)

_TRANSIENT = (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError)


@retry(exceptions=_TRANSIENT, tries=6, delay=5.0)
def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "apod-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


@retry(exceptions=_TRANSIENT, tries=5, delay=5.0)
def download_image(url: str) -> bytes:
    log.info("Downloading image: %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "apod-bot/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = response.read()
    log.info("Downloaded %.2f MB", len(data) / 1_000_000)
    return data


def _media_url(data: dict) -> str:
    if data["media_type"] == "video":
        return data["thumbnail_url"]
    return data.get("hdurl") or data["url"]


def nasa_apod(date: str | None = None) -> tuple[str, dict]:
    """Return (local_image_path, apod_metadata) for the given date (or today)."""
    api_key = os.environ["NASA_API_KEY"]
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}&thumbs=true"
    if date:
        url += f"&date={date}"

    data = _fetch_json(url)

    if "code" in data or data.get("media_type") not in ("image", "video"):
        raise RuntimeError(f"NASA APOD request failed or unsupported media: {data}")

    log.info("APOD %s: %s (%s)", data["date"], data["title"], data["media_type"])

    os.makedirs(data["date"], exist_ok=True)
    image_url = _media_url(data)
    fname = f"{data['date']}/{image_url.split('/')[-1]}"

    with open(fname, "wb") as opf:
        opf.write(download_image(image_url))
    log.info("Saved image to %s", fname)

    return fname, data
