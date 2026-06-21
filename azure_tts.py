import html
import logging
import os
import re
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from utils import retry

log = logging.getLogger(__name__)

# Max characters per on-screen caption (kept to one comfortable line).
MAX_CHARS = 22
# Try to keep each caption visible at least this long, merging tiny fragments.
MIN_SEGMENT_MS = 900

# Punctuation that should hug the previous word (no leading space) in subtitles.
_NO_SPACE_BEFORE = set(".,:;!?)]}%")

# A caption is cut after a token ending in one of these.
_BREAK_AFTER = set(".,!?:;")

# Paid-tier voices (set AZURE_SPEECH_VOICE to swap):
#   en-US-Andrew:DragonHDLatestNeural  — Dragon HD male narrator (default)
#   en-US-Ava:DragonHDLatestNeural     — Dragon HD female
#   en-US-AndrewMultilingualNeural     — warm male narrator
#   en-US-AvaMultilingualNeural        — expressive female
#   en-US-EmmaMultilingualNeural       — calm female
#   en-US-BrianMultilingualNeural      — casual male


def _join_words(values: list[str]) -> str:
    line = ""
    for val in values:
        if not val:
            continue
        if not line:
            line = val
        elif val[:1] in _NO_SPACE_BEFORE:
            line += val
        else:
            line += " " + val
    return line


def _ticks_to_ms(ticks: int) -> int:
    return ticks // 10000


def _fmt_ts(ms: int) -> str:
    return (
        f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:"
        f"{(ms // 1000) % 60:02d},{ms % 1000:03d}"
    )


def _ends_clause(token: str) -> bool:
    return bool(token) and token[-1] in _BREAK_AFTER


def json_to_srt(words: list[dict]) -> tuple[str, int]:
    """Build clause-aware captions.

    Cuts a caption after clause punctuation (, . ! ? : ;) or when the next word
    would exceed MAX_CHARS, then merges captions that would flash too briefly
    (shorter than MIN_SEGMENT_MS) as long as the merge still fits MAX_CHARS.
    """
    n = len(words)
    if n == 0:
        return "", 0

    # 1) Initial clause/length segments as [start_idx, end_idx] inclusive.
    segments: list[list[int]] = []
    i = 0
    while i < n:
        j = i
        while j < n - 1:
            if _ends_clause(words[j]["value"]):
                break
            candidate = _join_words([w["value"] for w in words[i : j + 2]])
            if len(candidate) > MAX_CHARS:
                break
            j += 1
        segments.append([i, j])
        i = j + 1

    def seg_start(seg: list[int]) -> int:
        return words[seg[0]]["time"]

    def seg_end(seg: list[int]) -> int:
        nxt = seg[1] + 1
        return words[nxt]["time"] - 1 if nxt < n else words[seg[0]]["time"] + 1500

    # 2) Forward-merge captions that display too briefly, respecting MAX_CHARS.
    merged: list[list[int]] = []
    k = 0
    while k < len(segments):
        cur = list(segments[k])
        while k + 1 < len(segments):
            nxt = segments[k + 1]
            combined = _join_words([w["value"] for w in words[cur[0] : nxt[1] + 1]])
            if seg_end(cur) - seg_start(cur) < MIN_SEGMENT_MS and len(combined) <= MAX_CHARS:
                cur[1] = nxt[1]
                k += 1
            else:
                break
        merged.append(cur)
        k += 1

    # 3) Emit SRT.
    output = []
    end = 0
    for seq_no, seg in enumerate(merged, 1):
        start = seg_start(seg)
        end = seg_end(seg)
        line = _join_words([w["value"] for w in words[seg[0] : seg[1] + 1]])
        output.append(f"{seq_no}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{line}\n")

    return "\n".join(output), end


def _escape_with_spelled_apod(text: str) -> str:
    parts = re.split(r"(APOD)", text, flags=re.IGNORECASE)
    out = []
    for part in parts:
        if part.upper() == "APOD":
            out.append('<say-as interpret-as="characters">APOD</say-as>')
        else:
            out.append(html.escape(part))
    return "".join(out)


def _build_ssml(text: str, voice: str, style: str, rate: str) -> str:
    inner = _escape_with_spelled_apod(text)
    if rate:
        inner = f'<prosody rate="{rate}">{inner}</prosody>'
    if "DragonHD" not in voice and style:
        inner = f'<mstts:express-as style="{style}">{inner}</mstts:express-as>'
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        f'<voice name="{voice}">{inner}</voice></speak>'
    )


@retry(exceptions=(RuntimeError,), tries=3, delay=5.0)
def _synthesize(ssml: str, key: str, region: str) -> tuple[bytes, list[dict]]:
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true"
    )

    word_boundaries: list[dict] = []

    def word_boundary_cb(evt: speechsdk.SpeechSynthesisWordBoundaryEventArgs) -> None:
        word = evt.text.strip()
        if word:
            word_boundaries.append(
                {"time": _ticks_to_ms(evt.audio_offset), "value": word}
            )

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=None
    )
    synthesizer.synthesis_word_boundary.connect(word_boundary_cb)

    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = result.cancellation_details
        raise RuntimeError(
            f"Azure TTS failed: {result.reason} "
            f"({details.error_details if details else 'no details'})"
        )
    return result.audio_data, word_boundaries


def get_audio(
    text: str,
    folder: str,
    voice: str | None = None,
    style: str | None = None,
    rate: str | None = None,
    speech_key: str | None = None,
    speech_region: str | None = None,
) -> tuple[str, str, int]:
    key = speech_key or os.environ["AZURE_SPEECH_KEY"]
    region = speech_region or os.environ["AZURE_SPEECH_REGION"]
    voice = voice or os.getenv("AZURE_SPEECH_VOICE", "en-US-Andrew:DragonHDLatestNeural")
    style = style or os.getenv("AZURE_SPEECH_STYLE", "documentary-narration")
    rate = rate if rate is not None else os.getenv("AZURE_SPEECH_RATE", "+12%")

    Path(folder).mkdir(parents=True, exist_ok=True)
    log.info("Synthesizing %d chars with voice=%s rate=%s", len(text), voice, rate)

    ssml = _build_ssml(text, voice, style, rate)
    audio_data, word_boundaries = _synthesize(ssml, key, region)

    audio_path = f"{folder}/audio.mp3"
    with open(audio_path, "wb") as audio_file:
        audio_file.write(audio_data)

    srt_path = f"{folder}/audio.srt"
    if word_boundaries:
        srt_content, end_time = json_to_srt(word_boundaries)
    else:
        log.warning("No word boundaries returned; writing single-block subtitle")
        end_time = 30000
        srt_content = f"1\n00:00:00,000 --> {_fmt_ts(end_time)}\n{text}\n"

    with open(srt_path, "w") as srt_file:
        srt_file.write(srt_content)

    log.info("Audio %s (%d ms), subtitles %s", audio_path, end_time, srt_path)
    return audio_path, srt_path, end_time
