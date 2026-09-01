"""Voiceover via edge-tts (Microsoft neural voices, free, no API key)."""
from __future__ import annotations

import asyncio

import edge_tts

from . import config

VOICE_PATH = config.WORK / "voice.mp3"


async def _synth(text: str, voice: str, rate: str, dest: str) -> None:
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(dest)


def synthesize(text: str, rate: str = "+8%") -> str:
    config.WORK.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_synth(text, config.VOICE, rate, str(VOICE_PATH)))
    except Exception as exc:  # noqa: BLE001 - retry once with the default voice
        print(f"[tts] voice {config.VOICE!r} failed ({exc}); retrying with en-US-AndrewNeural")
        asyncio.run(_synth(text, "en-US-AndrewNeural", rate, str(VOICE_PATH)))
    if not VOICE_PATH.exists() or VOICE_PATH.stat().st_size < 4000:
        raise SystemExit("[tts] voiceover synthesis produced no audio")
    print(f"[tts] wrote {VOICE_PATH} ({VOICE_PATH.stat().st_size // 1024} KB)")
    return str(VOICE_PATH)
