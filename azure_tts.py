import html
import logging
import os
import re
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from utils import retry

log = logging.getLogger(__name__)

MIN_CHARS = 15
MAX_CHARS = 20

# Punctuation that should hug the previous word (no leading space) in subtitles.
_NO_SPACE_BEFORE = set(".,:;!?)]}%")

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


def json_to_srt(words: list[dict]) -> tuple[str, int]:
    output = []
    i = 0
    seq_no = 1

    while i < len(words):
        start = words[i]["time"]
        start_i = i
        chars = len(words[i]["value"])

        while chars < MIN_CHARS and i + 1 < len(words):
            i += 1
            chars += len(words[i]["value"])
            if chars > MAX_CHARS:
                chars -= len(words[i]["value"])
                i -= 1
                break

        end = words[i + 1]["time"] - 1 if i + 1 < len(words) else words[start_i]["time"] + 1000
        line = _join_words([word["value"] for word in words[start_i : i + 1]])
        output.append(
            f"{seq_no}\n"
            f"{start // 3600000:02d}:{(start // 60000) % 60:02d}:{(start // 1000) % 60:02d},{start % 1000:03d} --> "
            f"{end // 3600000:02d}:{(end // 60000) % 60:02d}:{(end // 1000) % 60:02d},{end % 1000:03d}\n"
            f"{line}\n"
        )
        i += 1
        seq_no += 1

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
        srt_content = (
            f"1\n"
            f"00:00:00,000 --> {end_time // 3600000:02d}:{(end_time // 60000) % 60:02d}:"
            f"{(end_time // 1000) % 60:02d},{end_time % 1000:03d}\n"
            f"{text}\n"
        )

    with open(srt_path, "w") as srt_file:
        srt_file.write(srt_content)

    log.info("Audio %s (%d ms), subtitles %s", audio_path, end_time, srt_path)
    return audio_path, srt_path, end_time
