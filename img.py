"""Fetch the NASA Astronomy Picture of the Day and download its image."""

import json
import logging
import os
import shutil
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


_DIRECT_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")


@retry(exceptions=_TRANSIENT, tries=5, delay=5.0)
def download_file(url: str, path: str) -> None:
    log.info("Downloading file: %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "apod-bot/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out)
    log.info("Saved %.2f MB to %s", os.path.getsize(path) / 1_000_000, path)


def _is_direct_video(url: str) -> bool:
    return url.lower().split("?")[0].endswith(_DIRECT_VIDEO_EXTS)


def nasa_apod(date: str | None = None) -> tuple[str, dict]:
    """Return (local_media_path, apod_metadata) for the given date (or today).

    For direct-hosted video APODs the .mp4 is downloaded and data["use_clip"]
    is set True. For image APODs (and YouTube/Vimeo videos, which expose a
    thumbnail) a still image path is returned and use_clip stays False.
    """
    api_key = os.environ["NASA_API_KEY"]
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}&thumbs=true"
    if date:
        url += f"&date={date}"

    data = _fetch_json(url)

    if "code" in data or data.get("media_type") not in ("image", "video"):
        raise RuntimeError(f"NASA APOD request failed or unsupported media: {data}")

    log.info("APOD %s: %s (%s)", data["date"], data["title"], data["media_type"])

    os.makedirs(data["date"], exist_ok=True)
    data["use_clip"] = False

    if data["media_type"] == "video":
        if _is_direct_video(data["url"]):
            fname = f"{data['date']}/source.mp4"
            download_file(data["url"], fname)
            data["use_clip"] = True
            return fname, data
        # YouTube/Vimeo embed: not directly downloadable, fall back to its thumbnail.
        thumb = data.get("thumbnail_url") or ""
        if not thumb:
            raise RuntimeError(f"Video APOD has no downloadable source or thumbnail: {data}")
        fname = f"{data['date']}/{thumb.split('/')[-1]}"
        with open(fname, "wb") as opf:
            opf.write(download_image(thumb))
        log.info("Saved video thumbnail to %s", fname)
        return fname, data

    image_url = data.get("hdurl") or data["url"]
    fname = f"{data['date']}/{image_url.split('/')[-1]}"
    with open(fname, "wb") as opf:
        opf.write(download_image(image_url))
    log.info("Saved image to %s", fname)

    return fname, data
