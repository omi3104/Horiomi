"""Word-timed karaoke captions.

Primary: faster-whisper transcribes the generated voiceover for real word
timings. Fallback: distribute the known narration text evenly across the
measured audio duration. Output is an .ass subtitle file.
"""
from __future__ import annotations

import re

from . import config, util

ASS_PATH = config.WORK / "captions.ass"

MAX_WORDS_PER_CUE = 3
MAX_CUE_SECONDS = 1.7

# Alignment 2 = bottom-centre; MarginV lifts the text up out of the very bottom
# so it clears the YouTube Shorts UI (progress bar, like/share rail). Tune with
# the CAPTION_MARGIN_V env var (pixels, PlayResY is 1920).
_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,96,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0.6,0,1,6,3,2,80,80,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").strip()


def _cues_from_words(words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    bucket: list[tuple[float, float, str]] = []
    for w in words:
        bucket.append(w)
        span = bucket[-1][1] - bucket[0][0]
        if len(bucket) >= MAX_WORDS_PER_CUE or span >= MAX_CUE_SECONDS or w[2].strip().endswith((".", "!", "?")):
            start = bucket[0][0]
            end = max(bucket[-1][1], start + 0.4)
            text = " ".join(x[2].strip() for x in bucket).upper()
            cues.append((start, end, text))
            bucket = []
    if bucket:
        start = bucket[0][0]
        end = max(bucket[-1][1], start + 0.4)
        cues.append((start, end, " ".join(x[2].strip() for x in bucket).upper()))
    return cues


def _whisper_words(audio_path: str) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel  # imported lazily - heavy

    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        audio_path, language="en", word_timestamps=True, vad_filter=True
    )
    words: list[tuple[float, float, str]] = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                words.append((float(w.start), float(w.end), w.word))
    return words


def _even_words(text: str, duration: float) -> list[tuple[float, float, str]]:
    tokens = [t for t in re.findall(r"\S+", text) if t]
    if not tokens:
        return []
    step = duration / len(tokens)
    return [(i * step, (i + 1) * step, tok) for i, tok in enumerate(tokens)]


def build(audio_path: str, narration: str) -> str:
    duration = util.probe_duration(audio_path)
    try:
        words = _whisper_words(audio_path)
        if len(words) < 3:
            raise RuntimeError("whisper returned too few words")
        print(f"[captions] whisper aligned {len(words)} words")
    except Exception as exc:  # noqa: BLE001
        print(f"[captions] whisper unavailable ({exc}); using even split")
        words = _even_words(narration, duration)

    cues = _cues_from_words(words)
    margin_v = config.get_int("CAPTION_MARGIN_V", 380)
    lines = [_ASS_HEADER.format(w=config.WIDTH, h=config.HEIGHT, mv=margin_v)]
    for start, end, text in cues:
        fx = r"{\fad(70,60)\t(0,120,\fscx112\fscy112)\t(120,220,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{fx}{_esc(text)}")
    ASS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[captions] wrote {len(cues)} cues -> {ASS_PATH}")
    return str(ASS_PATH)
