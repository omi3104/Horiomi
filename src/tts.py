"""Voiceover via edge-tts (Microsoft neural voices, free, no API key).

The finished short must land inside config.TARGET_SECONDS_MIN..MAX. Speech is
the main lever, so this module:

  1. synthesises once at the base rate,
  2. measures the real duration with ffprobe,
  3. if it is outside the window, recomputes the edge-tts speaking rate and
     re-synthesises (a handful of times, rate clamped so it never sounds
     chipmunk / drunk),
  4. as a last resort trims the narration to the last full sentence that fits.

Returns (path, duration_seconds, spoken_text) - spoken_text may be shorter than
the input if step 4 fired, so captions stay in sync.
"""
from __future__ import annotations

import asyncio
import re

import edge_tts

from . import config, util

VOICE_PATH = config.WORK / "voice.mp3"

_RATE_MIN, _RATE_MAX = -25, 45   # percent; outside this edge-tts sounds bad
_MAX_ATTEMPTS = 4


async def _synth(text: str, voice: str, rate: str, dest: str) -> None:
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(dest)


def _speak(text: str, rate_pct: int) -> None:
    rate = f"{rate_pct:+d}%"
    try:
        asyncio.run(_synth(text, config.VOICE, rate, str(VOICE_PATH)))
    except Exception as exc:  # noqa: BLE001 - retry once with the default voice
        print(f"[tts] voice {config.VOICE!r} failed ({exc}); retrying with en-US-AndrewNeural")
        asyncio.run(_synth(text, "en-US-AndrewNeural", rate, str(VOICE_PATH)))
    if not VOICE_PATH.exists() or VOICE_PATH.stat().st_size < 4000:
        raise SystemExit("[tts] voiceover synthesis produced no audio")


def _trim_to_seconds(text: str, current_dur: float, target_dur: float) -> str:
    """Drop whole sentences from the end until the estimate fits target_dur."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    while len(parts) > 3:
        parts.pop()
        kept = " ".join(parts)
        est = current_dur * len(kept.split()) / max(1, len(text.split()))
        if est <= target_dur:
            return kept
    return " ".join(parts)


def synthesize(text: str) -> tuple[str, float, str]:
    config.WORK.mkdir(parents=True, exist_ok=True)
    lo, hi = config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX
    target = config.TARGET_SECONDS
    spoken = text
    rate_pct = config.TTS_RATE
    dur = 0.0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        _speak(spoken, rate_pct)
        dur = util.probe_duration(str(VOICE_PATH))
        print(f"[tts] attempt {attempt}: rate={rate_pct:+d}%  ->  {dur:.1f}s "
              f"(window {lo}-{hi}s)")
        if lo <= dur <= hi:
            break

        # Speech-rate model is close to linear: to go from `dur` to `target`
        # at current speed factor (1 + rate/100), scale the factor by dur/target.
        factor = (1 + rate_pct / 100) * (dur / target)
        new_rate = max(_RATE_MIN, min(_RATE_MAX, round((factor - 1) * 100)))

        if new_rate == rate_pct:
            # Rate is pinned at a clamp and still out of range.
            if dur > hi:
                spoken = _trim_to_seconds(spoken, dur, target)
                print(f"[tts] rate maxed; trimming narration to ~{len(spoken.split())} words")
                continue
            break  # too short even at the slowest rate - video.py will pad
        rate_pct = new_rate

    if not (lo <= dur <= hi):
        print(f"[tts] WARNING: final voiceover {dur:.1f}s is outside {lo}-{hi}s; "
              f"video.py will pad/trim the tail")
    print(f"[tts] wrote {VOICE_PATH} ({VOICE_PATH.stat().st_size // 1024} KB, {dur:.1f}s)")
    return str(VOICE_PATH), dur, spoken
