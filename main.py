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
# Original video audio is mixed under the narration at this volume.
VIDEO_BG_VOLUME = 0.30


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


def _ffprobe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio(path: str) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(proc.stdout.strip())


def _video_dimensions(path: str) -> tuple[int, int]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        w, h = proc.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except ValueError:
        return 0, 0


def _atempo_chain(factor: float) -> str:
    """atempo only accepts 0.5-2.0; chain filters to reach larger factors."""
    parts = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f *= 2.0
    parts.append(f"atempo={f:.4f}")
    return ",".join(parts)


def process_clip(video_in, narration_path, srt_path, video_path, narration_ms):
    """Compose a vertical short from an actual video: blurred (uncropped) clip
    centered on a blurred background, looped or sped up to the narration length,
    with the clip's own audio mixed under the narration and subtitles burned in.
    """
    narration_s = max(narration_ms / 1000.0, 0.5)
    dv = _ffprobe_duration(video_in)
    has_audio = _has_audio(video_in)
    speed_up = dv > narration_s + 0.2

    # For landscape clips, the video sits in a centered band with blurred bars
    # above/below; put the captions up in the top blurred area instead of over
    # the footage. Portrait/square clips fill the height, so keep them centered.
    vw, vh = _video_dimensions(video_in)
    landscape = vw > vh
    sub_style = "Alignment=6,MarginV=55" if landscape else "Alignment=10"

    input_opts = []
    setpts = ""
    if speed_up:
        setpts = f"setpts=PTS*{narration_s / dv:.6f},"
    else:
        input_opts = ["-stream_loop", "-1"]

    vfilter = (
        f"[0:v]{setpts}split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=30[bgb];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[ov];"
        f"[ov]subtitles={srt_path}:force_style='{sub_style}'[v]"
    )

    args = list(input_opts) + ["-i", video_in, "-i", narration_path]

    if has_audio:
        tempo = (_atempo_chain(dv / narration_s) + ",") if speed_up else ""
        afilter = (
            f"[0:a]{tempo}volume={VIDEO_BG_VOLUME}[va];"
            "[1:a]volume=1.0[na];"
            "[va][na]amix=inputs=2:duration=longest:normalize=0[a]"
        )
        filter_complex = vfilter + ";" + afilter
        audio_map = ["-map", "[a]"]
    else:
        filter_complex = vfilter
        audio_map = ["-map", "1:a"]

    _run_ffmpeg(
        args
        + ["-filter_complex", filter_complex, "-map", "[v]"]
        + audio_map
        + ["-t", f"{narration_s:.3f}", "-r", "30",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", video_path]
    )
    log.info("Wrote clip video %s (%.2f MB)", video_path, os.path.getsize(video_path) / 1_000_000)


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


def build_description(date, text, data, voice_name, attribution=True):
    """Compose the post text. When attribution is False, the voice/automation
    notes are omitted (used for the Instagram caption). The 'Image-' link is
    included only for image APODs (videos link via 'Video-')."""
    is_video = data["media_type"] == "video"
    hashtags = (
        "#nasa #apod"
        if is_video
        else "#nasa #apod #space #astronomy #astrophotography #explore #reels #fyp #foryoupage"
    )

    header = [f"{date} NASA's APOD update-"]
    if is_video:
        header.append(f"Video- {data['url']}")
    header.append(text)

    details = []
    if not is_video:
        details.append(f"Image- {data.get('hdurl', data['url'])}")
    if attribution:
        details.append(f"Voice using Azure TTS ({voice_name})")

    blocks = ["\n".join(header)]
    if details:
        blocks.append("\n".join(details))
    blocks.append(hashtags)
    if attribution:
        blocks.append("Uploaded Automatically by a program, an experiment by Z3R0C1PH3R")

    return "\n\n".join(blocks).replace(">", "").replace("<", "")


def run(date=None):
    media_path, data = img.nasa_apod(date)
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
    if data.get("use_clip"):
        process_clip(media_path, audio_path, srt_path, video_path, audio_len)
    else:
        process_video(media_path, audio_path, srt_path, video_path, audio_len)

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
            caption = build_description(date, text, data, voice_name, attribution=False)
            public_url = blob_upload.upload_video(video_path, f"{date}.mp4")
            insta.upload_reel(public_url, caption)
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
