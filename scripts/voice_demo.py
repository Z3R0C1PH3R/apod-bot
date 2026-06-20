#!/usr/bin/env python3
"""Generate MP3 demos for recommended Azure TTS voices using real APOD speech text."""

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import azure_tts
from utils import setup_logging

DEFAULT_DATE = "2026-06-20"
OUT_DIR = ROOT / "voice-demo"

# Short list — the ones worth comparing for APOD narration
RECOMMENDED_VOICES = [
    "en-US-AndrewMultilingualNeural",   # warm male narrator (current default)
    "en-US-AvaMultilingualNeural",      # expressive female
    "en-US-EmmaMultilingualNeural",     # calm female
    "en-US-BrianMultilingualNeural",    # casual male
    "en-US-Andrew:DragonHDLatestNeural",  # best quality male
    "en-US-Ava:DragonHDLatestNeural",     # best quality female
]


def fetch_apod_speech_text(date: str) -> str:
    api_key = os.environ["NASA_API_KEY"]
    url = f"https://api.nasa.gov/planetary/apod?api_key={api_key}&thumbs=true&date={date}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    if data.get("media_type") == "video":
        return (
            f"NASA's APOD for {data['date']}, check the video from the link in the description. "
            f"{data['title']}: {data['explanation']}"
        ).replace("&", "and")
    return f"NASA's APOD for {data['date']}. {data['title']}: {data['explanation']}".replace(
        "&", "and"
    )


def fetch_all_voices(key: str, region: str) -> list[str]:
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        voices = json.load(resp)
    return sorted(
        v["ShortName"]
        for v in voices
        if v.get("Locale", "").startswith("en-US") and "Neural" in v.get("ShortName", "")
    )


def safe_filename(voice: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", voice)


def write_index(voices_done: list[tuple[str, str]]) -> None:
    rows = "\n".join(
        f'    <li><strong>{name}</strong> '
        f'<audio controls preload="none" src="{fname}"></audio></li>'
        for name, fname in voices_done
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Azure TTS voice demo — APOD narration</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
    li {{ margin: 1rem 0; }}
    audio {{ width: 100%; margin-top: 0.25rem; }}
  </style>
</head>
<body>
  <h1>Azure TTS voice demo</h1>
  <p>Full APOD narration text used in the video pipeline.</p>
  <ol>
{rows}
  </ol>
</body>
</html>
"""
    (OUT_DIR / "index.html").write_text(html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Azure TTS voice demos")
    parser.add_argument("date", nargs="?", default=DEFAULT_DATE, help="APOD date YYYY-MM-DD")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all en-US neural voices (~119). Default: recommended short list only.",
    )
    args = parser.parse_args()

    setup_logging()
    OUT_DIR.mkdir(exist_ok=True)
    speech_text = fetch_apod_speech_text(args.date)
    (OUT_DIR / "speech.txt").write_text(speech_text)
    print(f"Speech text: {len(speech_text)} chars ({args.date})")

    if args.all:
        key = os.environ["AZURE_SPEECH_KEY"]
        region = os.environ["AZURE_SPEECH_REGION"]
        voices = fetch_all_voices(key, region)
    else:
        voices = RECOMMENDED_VOICES

    print(f"Generating {len(voices)} voice(s)...")

    done: list[tuple[str, str]] = []
    for i, voice in enumerate(voices, 1):
        fname = f"{safe_filename(voice)}.mp3"
        out_path = OUT_DIR / fname
        style = None if "DragonHD" in voice else "documentary-narration"
        print(f"[{i}/{len(voices)}] {voice}")
        try:
            tmp = OUT_DIR / f"_tmp_{safe_filename(voice)}"
            audio_path, _, _ = azure_tts.get_audio(
                speech_text, str(tmp), voice=voice, style=style
            )
            Path(audio_path).replace(out_path)
            for extra in tmp.glob("*"):
                extra.unlink()
            tmp.rmdir()
            done.append((voice, fname))
        except Exception as exc:
            print(f"  FAILED: {exc}")

    write_index(done)
    print(f"\nDone. Open: {OUT_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
