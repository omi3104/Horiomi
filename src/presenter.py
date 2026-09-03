"""The recurring channel host portrait.

This is a brand identity shown on the intro / outro cards - it does NOT
lip-sync or talk (that needs a paid avatar API or a GPU). To keep the same
face every video it uses a fixed prompt + fixed seed, or a pinned image at
assets/presenter.png if you drop one in.
"""
from __future__ import annotations

import urllib.parse

import requests

from . import config

UA = {"User-Agent": "yt-shorts-agent/1.1 (history shorts)"}
_CACHE = config.WORK / "presenter.png"
_PINNED = config.ASSETS / "presenter.png"


def get_portrait() -> str | None:
    if not config.PRESENTER:
        return None
    if _PINNED.exists() and _PINNED.stat().st_size > 10_000:
        print(f"[presenter] using pinned {_PINNED}")
        return str(_PINNED)
    if _CACHE.exists() and _CACHE.stat().st_size > 10_000:
        return str(_CACHE)

    config.WORK.mkdir(parents=True, exist_ok=True)
    q = urllib.parse.quote(config.PRESENTER_PROMPT[:300])
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width=768&height=768&nologo=true&seed={config.PRESENTER_SEED}&model=flux")
    try:
        r = requests.get(url, headers=UA, timeout=90)
        r.raise_for_status()
        if len(r.content) < 10_000:
            raise ValueError("portrait too small")
        _CACHE.write_bytes(r.content)
        print(f"[presenter] generated host portrait ({len(r.content)//1024} KB)")
        return str(_CACHE)
    except Exception as exc:  # noqa: BLE001
        print(f"[presenter] portrait unavailable ({exc}); skipping host cards")
        return None
