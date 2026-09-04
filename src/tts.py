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
import os
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


# =====================================================================
#  DIALOGUE mode: one clip per turn (skeptic / expert voice), stitched with a
#  short gap. Same adaptive-rate idea as synthesize(), applied to the total.
# =====================================================================
_GAP_S = 0.22


def _speak_turn(text: str, voice: str, rate_pct: int, dest) -> None:
    rate = f"{rate_pct:+d}%"
    try:
        asyncio.run(_synth(text, voice, rate, str(dest)))
    except Exception as exc:  # noqa: BLE001
        print(f"[tts]   turn voice {voice!r} failed ({exc}); retrying with {config.VOICE!r}")
        asyncio.run(_synth(text, config.VOICE, rate, str(dest)))
    if not dest.exists() or dest.stat().st_size < 800:
        raise SystemExit(f"[tts] turn synthesis produced no audio ({dest})")


def _synth_turns(turns: list[dict], rate_pct: int) -> list[str]:
    paths = []
    for i, t in enumerate(turns):
        voice = config.SKEPTIC_VOICE if t["speaker"] == "skeptic" else config.EXPERT_VOICE
        dest = config.WORK / f"turn_{i:02d}.mp3"
        _speak_turn(t["say"], voice, rate_pct, dest)
        paths.append(str(dest))
    return paths


def _gap_file() -> str:
    path = config.WORK / "gap.mp3"
    util.run(["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
              "-t", f"{_GAP_S}", "-q:a", "9", "-y", str(path)], quiet=True)
    return str(path)


def synthesize_dialogue(turns: list[dict]) -> tuple[str, float, list[dict]]:
    """Returns (audio_path, total_seconds, turns_with_start_end_timing)."""
    config.WORK.mkdir(parents=True, exist_ok=True)
    lo, hi = config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX
    target = config.TARGET_SECONDS
    turns = list(turns)
    rate_pct = config.TTS_RATE
    paths: list[str] = []
    durs: list[float] = []

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        paths = _synth_turns(turns, rate_pct)
        durs = [util.probe_duration(p) for p in paths]
        speech = sum(durs) + _GAP_S * max(0, len(durs) - 1)
        print(f"[tts] dialogue attempt {attempt}: rate={rate_pct:+d}%  ->  {speech:.1f}s "
              f"(window {lo}-{hi}s, {len(turns)} turns)")
        if lo <= speech <= hi:
            break
        factor = (1 + rate_pct / 100) * (speech / target)
        new_rate = max(_RATE_MIN, min(_RATE_MAX, round((factor - 1) * 100)))
        if new_rate == rate_pct:
            if speech > hi and len(turns) > 6:
                turns = turns[:-2]   # drop the last skeptic+expert pair, keep alternation
                print(f"[tts] rate maxed; dropping to {len(turns)} turns")
                continue
            break
        rate_pct = new_rate

    gap = _gap_file()
    listing = config.WORK / "turns_concat.txt"
    lines = []
    for i, p in enumerate(paths):
        lines.append(f"file '{os.path.abspath(p)}'")
        if i < len(paths) - 1:
            lines.append(f"file '{os.path.abspath(gap)}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

    audio_path = config.WORK / "dialogue.mp3"
    util.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(listing),
              "-c:a", "libmp3lame", "-b:a", "128k", "-y", str(audio_path)], quiet=True)

    t = 0.0
    turns_out = []
    for i, (turn, d) in enumerate(zip(turns, durs)):
        turns_out.append({**turn, "start": round(t, 3), "end": round(t + d, 3)})
        t += d + (_GAP_S if i < len(turns) - 1 else 0.0)

    dur = util.probe_duration(str(audio_path))
    if not (lo <= dur <= hi):
        print(f"[tts] WARNING: dialogue audio {dur:.1f}s is outside {lo}-{hi}s; "
              f"video.py will pad/trim the tail")
    print(f"[tts] wrote {audio_path} ({dur:.1f}s, {len(turns_out)} turns)")
    return str(audio_path), dur, turns_out
