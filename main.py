#!/usr/bin/python3
"""NASA APOD -> Azure TTS -> video -> YouTube/Instagram daily pipeline."""

import logging
import os
import shutil
import subprocess
import sys
import tempfile

import azure_tts
import blob_upload
import img
import insta
import upload
from utils import setup_logging

log = logging.getLogger("apod")

INTRO_SECONDS = 5


def _run_ffmpeg(args: list[str]) -> None:
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.error("ffmpeg failed (%d): %s", proc.returncode, proc.stderr.strip()[-2000:])
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


def process_video(image_path, audio_path, srt_path, video_path, duration_ms):
    """Build a 1080x1920 short: blurred-bg intro + slow pan, with audio + subtitles."""
    total_seconds = duration_ms // 1000 + 1
    pan_seconds = max(total_seconds - INTRO_SECONDS, 1)

    workdir = tempfile.mkdtemp(prefix="apod_")
    intro = os.path.join(workdir, "intro.mp4")
    pan = os.path.join(workdir, "pan.mp4")
    combined = os.path.join(workdir, "combined.mp4")
    concat_list = os.path.join(workdir, "concat.txt")

    try:
        _run_ffmpeg([
            "-framerate", "1", "-loop", "1", "-i", image_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-filter_complex",
            "scale=w=1080:h=trunc(ih/(iw/1080)/2)*2,split [og][cp]; "
            "[cp] crop=trunc(ih*9/16/2)*2:ih:iw/2-ow/2:0, scale=1080:1920, gblur=sigma=50 [blurred]; "
            "[blurred][og]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2",
            "-r", "30", "-t", str(INTRO_SECONDS), intro,
        ])

        _run_ffmpeg([
            "-framerate", "60", "-loop", "1", "-i", image_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf",
            f"scale=h=1920:w=trunc(iw/(ih/1920)/2)*2,"
            f"crop=h=1920:w=1080:x=(iw-1080)*t/{pan_seconds},trim=duration={pan_seconds}",
            "-r", "60", pan,
        ])

        with open(concat_list, "w") as f:
            f.write(f"file '{intro}'\nfile '{pan}'\n")
        _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", combined])

        _run_ffmpeg([
            "-i", combined, "-i", audio_path,
            "-vf", f"subtitles={srt_path}:force_style='Alignment=10'",
            "-c:a", "copy", video_path,
        ])
        log.info("Wrote video %s (%.2f MB)", video_path, os.path.getsize(video_path) / 1_000_000)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_description(date, text, data, voice_name):
    tail = "Uploaded Automatically by a program, an experiment by Z3R0C1PH3R"
    if data["media_type"] != "video":
        image_ref = data.get("hdurl", data["url"])
        body = (
            f"{date} NASA's APOD update-\n{text}\n\nImage- {image_ref}\n"
            f"Voice using Azure TTS ({voice_name})\n\n"
            "#nasa #apod #space #astronomy #astrophotography #explore #reels #fyp #foryoupage\n\n"
            f"{tail}"
        )
    else:
        body = (
            f"{date} NASA's APOD update-\nVideo- {data['url']}\n{text}\n\n"
            f"Image- {data['thumbnail_url']}\nVoice using Azure TTS ({voice_name})\n\n"
            f"#nasa #apod\n\n{tail}"
        )
    return body.replace(">", "").replace("<", "")


def run(date=None):
    image_path, data = img.nasa_apod(date)
    date = data["date"]
    text = f"{data['title']}:\n{data['explanation']}"

    if data["media_type"] != "video":
        speech_text = f"NASA's APOD for {date}. {data['title']}: {data['explanation']}"
    else:
        speech_text = (
            f"NASA's APOD for {date}, check the video from the link in the description. "
            f"{data['title']}: {data['explanation']}"
        )
    speech_text = speech_text.replace("&", "and")

    audio_path, srt_path, audio_len = azure_tts.get_audio(speech_text, date)

    os.makedirs("videos", exist_ok=True)
    video_path = f"videos/{date}.mp4"
    process_video(image_path, audio_path, srt_path, video_path, audio_len)

    voice_name = os.getenv("AZURE_SPEECH_VOICE", "en-US-Andrew:DragonHDLatestNeural")
    title = f"{data['title']} #shorts"
    description = build_description(date, text, data, voice_name)

    failures = []

    try:
        resp = upload.upload_video(title, description, video_path)
        log.info("YouTube: https://youtu.be/%s", resp.get("id"))
    except Exception:
        log.exception("YouTube upload failed")
        failures.append("youtube")

    if os.getenv("INSTAGRAM_ACCESS_TOKEN"):
        try:
            public_url = blob_upload.upload_video(video_path, f"{date}.mp4")
            insta.upload_reel(public_url, description)
        except Exception:
            log.exception("Instagram upload failed")
            failures.append("instagram")
    else:
        log.info("INSTAGRAM_ACCESS_TOKEN not set; skipping Instagram")

    return failures


def main():
    setup_logging()
    date = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        failures = run(date)
    except Exception:
        log.exception("Pipeline failed before publishing")
        return 1

    if failures:
        log.error("Completed with failures: %s", ", ".join(failures))
        return 2
    log.info("Pipeline completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
