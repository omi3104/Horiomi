"""Word-timed karaoke captions (each word lights up as it is spoken).

Primary: faster-whisper transcribes the generated voiceover for real word
timings. Fallback: distribute the known narration text evenly across the
measured audio duration. Output is an .ass subtitle file.
"""
from __future__ import annotations

import re

from . import config, util

ASS_PATH = config.WORK / "captions.ass"

MAX_WORDS_PER_CUE = 4
MAX_CUE_SECONDS = 2.2

# Primary = spoken (amber highlight), Secondary = not-yet-spoken (white).
# \kf sweeps the fill from Secondary -> Primary across each word.
_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,98,&H002EA8E0,&H00FFFFFF,&H00101010,&H96000000,-1,0,0,0,100,100,0.8,0,1,7,4,2,90,90,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# entrance: quick fade + a small overshoot pop
_FX = r"{\fad(50,40)\t(0,130,\fscx113\fscy113)\t(130,250,\fscx100\fscy100)}"


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").strip()


def _finish(bucket: list[tuple[float, float, str]]) -> tuple[float, float, list[tuple[str, int]]]:
    start = bucket[0][0]
    end = max(bucket[-1][1], start + 0.5)
    kw: list[tuple[str, int]] = []
    for i, (ws, _we, tok) in enumerate(bucket):
        nxt = bucket[i + 1][0] if i + 1 < len(bucket) else end
        cs = max(1, round((nxt - ws) * 100))          # karaoke duration, centiseconds
        kw.append((_esc(tok).upper(), cs))
    return start, end, kw


def _cues_from_words(words: list[tuple[float, float, str]]):
    cues = []
    bucket: list[tuple[float, float, str]] = []
    for w in words:
        bucket.append(w)
        span = bucket[-1][1] - bucket[0][0]
        if (len(bucket) >= MAX_WORDS_PER_CUE or span >= MAX_CUE_SECONDS
                or w[2].strip().endswith((".", "!", "?", ";", ":"))):
            cues.append(_finish(bucket))
            bucket = []
    if bucket:
        cues.append(_finish(bucket))
    return cues


def _whisper_words(audio_path: str) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel  # imported lazily - heavy

    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        audio_path, language=config.LANGUAGE, word_timestamps=True, vad_filter=True
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
    for start, end, kw in cues:
        body = "".join(rf"{{\kf{cs}}}{tok} " for tok, cs in kw).rstrip()
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{_FX}{body}")
    ASS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[captions] wrote {len(cues)} karaoke cues -> {ASS_PATH}")
    return str(ASS_PATH)
