"""Fetch one background clip/image per script beat.

For each beat several search phrases are derived (the model's visual phrase,
plus keyword sets pulled from the spoken line) and tried against every source
until a NEW file downloads. Provider result lists are shuffled with a
per-beat seed and `_used_urls` blocks repeats, so distinct beats land on
distinct clips even when their phrasing is similar. If a beat still finds
nothing it reuses an earlier asset on a rotation (never the same clip 9x).

When config.PREFER_VIDEO is on (default) every VIDEO source is tried before any
still, so the short is real motion footage wherever possible:

  VIDEO   1. Pexels video      - needs PEXELS_API_KEY   (free)
          2. Pixabay video     - needs PIXABAY_API_KEY  (free)
          3. Coverr video      - needs COVERR_API_KEY   (free)
          4. Wikimedia Commons video (webm/ogv)         - KEYLESS
  STILLS  5. Pexels photo      - needs PEXELS_API_KEY
          6. Pixabay photo     - needs PIXABAY_API_KEY
          7. Openverse         - KEYLESS (CC real photos)
          8. Wikimedia Commons photo                    - KEYLESS
          9. Gemini AI image   - needs GEMINI_API_KEY
         10. Pollinations AI image                      - KEYLESS, always-on

See README "Stock media API keys" for how to get the three free keys.
Everything lands in work/media/ . Nothing here knows the topic text.
"""
from __future__ import annotations

import base64
import mimetypes
import random
import re
import time
import urllib.parse

import requests

from . import config

UA = {"User-Agent": "yt-shorts-agent/1.0 (github actions; educational shorts)"}
MEDIA_DIR = config.WORK / "media"
_used_urls: set[str] = set()

_MIN_BYTES = 8_000
_MAX_VIDEO_BYTES = 90_000_000   # skip huge Commons masters
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".ogv")
_PER_PAGE = 25   # ask for plenty so distinct beats land on distinct clips

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "why", "how", "what", "that", "this", "these", "those",
    "it", "its", "as", "at", "by", "with", "from", "into", "than", "then",
    "too", "very", "so", "you", "your", "they", "their", "them", "not", "no",
    "can", "will", "just", "about", "over", "under", "more", "most", "some",
    "one", "two", "there", "here", "when", "which", "while", "because",
}


def _rng(stem: str) -> random.Random:
    return random.Random(stem)


def _clean_query(text: str) -> str:
    """Turn a sentence / phrase into a stock-library-friendly noun phrase."""
    words = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    kept = [w for w in words if w not in _STOPWORDS]
    return " ".join(kept[:6]) or (text or "").strip()


def _beat_queries(beat: dict, i: int) -> list[str]:
    """Ordered, de-duplicated search phrases to try for one beat."""
    visual = (beat.get("visual") or "").strip()
    say = beat.get("say") or ""
    cands = [
        visual,
        _clean_query(visual),
        _clean_query(say),
        " ".join(_clean_query(say).split()[:3]),
        " ".join(_clean_query(visual).split()[:2]),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        c = re.sub(r"\s+", " ", c or "").strip()
        if len(c) >= 3 and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out or ["cinematic abstract background"]


def _download(url: str, dest_stem: str, kind_hint: str = "") -> str | None:
    if not url or url in _used_urls:
        return None
    cap = _MAX_VIDEO_BYTES if kind_hint == "video" else 25_000_000
    path: "None | object" = None
    try:
        with requests.get(url, headers=UA, stream=True, timeout=90) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
            ext = mimetypes.guess_extension(ctype) or ""
            if ext in ("", ".bin", ".jpe"):
                ext = ".mp4" if (kind_hint == "video" or "video" in ctype) else ".jpg"
            path = MEDIA_DIR / f"{dest_stem}{ext}"
            size = 0
            with open(path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
                    size += len(chunk)
                    if size > cap:
                        raise ValueError(f"file exceeds {cap // 1_000_000} MB cap")
        if size < _MIN_BYTES:
            path.unlink(missing_ok=True)
            return None
        _used_urls.add(url)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   download failed ({exc})")
        if path is not None:
            try:
                path.unlink(missing_ok=True)  # type: ignore[union-attr]
            except OSError:
                pass
        return None


# --- stock VIDEO sources --------------------------------------------------
def _pexels_video(q: str, stem: str) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    for orientation in ("portrait", "landscape"):
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": config.PEXELS_API_KEY, **UA},
                params={"query": q, "orientation": orientation, "per_page": _PER_PAGE, "size": "medium"},
                timeout=30,
            )
            r.raise_for_status()
            vids = r.json().get("videos", [])
            _rng(stem).shuffle(vids)
            for vid in vids:
                files = sorted(
                    vid.get("video_files", []),
                    key=lambda f: abs((f.get("height") or 0) - 1920),
                )
                for f in files:
                    got = _download(f.get("link", ""), stem, "video")
                    if got:
                        return got
        except Exception as exc:  # noqa: BLE001
            print(f"[media]   pexels video error: {exc}")
    return None


def _pixabay_video(q: str, stem: str) -> str | None:
    if not config.PIXABAY_API_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": config.PIXABAY_API_KEY, "q": q, "per_page": _PER_PAGE},
            headers=UA, timeout=30,
        )
        if r.ok:
            hits = r.json().get("hits", [])
            _rng(stem).shuffle(hits)
            for hit in hits:
                v = hit.get("videos", {})
                for key in ("large", "medium", "small"):
                    got = _download(v.get(key, {}).get("url", ""), stem, "video")
                    if got:
                        return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pixabay video error: {exc}")
    return None


def _coverr_video(q: str, stem: str) -> str | None:
    if not config.COVERR_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"apiKey": config.COVERR_API_KEY, "query": q, "page_size": _PER_PAGE, "urls": "true"},
            headers=UA, timeout=30,
        )
        if not r.ok:
            print(f"[media]   coverr HTTP {r.status_code}")
            return None
        data = r.json()
        hits = data.get("hits") or data.get("videos") or []
        _rng(stem).shuffle(hits)
        for h in hits:
            urls = h.get("urls") or {}
            for key in ("mp4_download", "mp4", "mp4_preview"):
                got = _download(urls.get(key, ""), stem, "video")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   coverr error: {exc}")
    return None


def _wikimedia_video(q: str, stem: str) -> str | None:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{q} filetype:video", "gsrlimit": 20, "gsrnamespace": 6,
                "prop": "imageinfo", "iiprop": "url|size|mime",
            },
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        cands = list(r.json().get("query", {}).get("pages", {}).values())
        _rng(stem).shuffle(cands)
        for p in cands:
            info = (p.get("imageinfo") or [{}])[0]
            url = info.get("url", "")
            if url.lower().endswith(_VIDEO_EXTS):
                got = _download(url, stem, "video")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   wikimedia video error: {exc}")
    return None


# --- stock PHOTO sources ------------------------------------------------
def _pexels_photo(q: str, stem: str) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY, **UA},
            params={"query": q, "orientation": "portrait", "per_page": 8},
            timeout=30,
        )
        r.raise_for_status()
        for photo in r.json().get("photos", []):
            src = photo.get("src", {})
            for key in ("portrait", "large2x", "original", "large"):
                got = _download(src.get(key, ""), stem, "image")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pexels photo error: {exc}")
    return None


def _pixabay_photo(q: str, stem: str) -> str | None:
    if not config.PIXABAY_API_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": config.PIXABAY_API_KEY, "q": q, "image_type": "photo",
                    "orientation": "vertical", "per_page": 8},
            headers=UA, timeout=30,
        )
        if r.ok:
            for hit in r.json().get("hits", []):
                got = _download(hit.get("largeImageURL", ""), stem, "image")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pixabay photo error: {exc}")
    return None


def _openverse(q: str, stem: str) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": q, "license_type": "commercial", "size": "large",
                    "mature": "false", "page_size": 8},
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        for item in r.json().get("results", []):
            got = _download(item.get("url", ""), stem, "image")
            if got:
                return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   openverse error: {exc}")
    return None


def _wikimedia_photo(q: str, stem: str) -> str | None:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": 8, "gsrnamespace": 6,
                "prop": "imageinfo", "iiprop": "url|size", "iiurlwidth": config.WIDTH,
            },
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        pages = r.json().get("query", {}).get("pages", {})
        cands = sorted(pages.values(), key=lambda p: -(p.get("imageinfo", [{}])[0].get("width", 0) or 0))
        for p in cands:
            info = (p.get("imageinfo") or [{}])[0]
            got = _download(info.get("thumburl") or info.get("url", ""), stem, "image")
            if got:
                return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   wikimedia error: {exc}")
    return None


# --- AI images ----------------------------------------------------------
_GEMINI_IMAGE_MODELS = (
    "gemini-2.5-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-preview-image-generation",
)


def _gemini_image(prompt: str, stem: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": f"Vertical 9:16 photorealistic, no text, cinematic: {prompt}"}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    for model in _GEMINI_IMAGE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=payload, timeout=90)
            if not r.ok:
                print(f"[media]   gemini image {model} HTTP {r.status_code} {r.text[:120]!r}")
                continue
            for part in (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    path = MEDIA_DIR / f"{stem}.png"
                    path.write_bytes(base64.b64decode(inline["data"]))
                    return str(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[media]   gemini image {model} error: {exc}")
    return None


def _pollinations(prompt: str, stem: str, seed: int) -> str | None:
    text = _clean_query(prompt) or (prompt or "").strip() or "cinematic abstract background"
    q = urllib.parse.quote(text[:220])
    base = (f"https://image.pollinations.ai/prompt/{q}"
            f"?width={config.WIDTH}&height={config.HEIGHT}&nologo=true&seed={seed}")
    for model in ("flux", "turbo", ""):
        url = base + (f"&model={model}" if model else "")
        for attempt in range(2):
            got = _download(url, stem, "image")
            if got:
                return got
            time.sleep(3 + attempt * 4)
    return None


_VIDEO_PROVIDERS = (_pexels_video, _pixabay_video, _coverr_video, _wikimedia_video)
_IMAGE_PROVIDERS = (_pexels_photo, _pixabay_photo, _openverse, _wikimedia_photo, _gemini_image)


def fetch_for_beats(beats: list[dict]) -> list[dict]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    providers = (list(_VIDEO_PROVIDERS) if config.PREFER_VIDEO else []) + list(_IMAGE_PROVIDERS)

    results: list[dict] = []
    distinct: list[dict] = []   # successful, unique assets - for graceful rotation
    for i, beat in enumerate(beats):
        stem = f"beat{i:02d}"
        queries = _beat_queries(beat, i)
        print(f"[media] beat {i}: {queries}")

        path = None
        for q in queries:
            for provider in providers:
                path = provider(q, stem)
                if path:
                    break
            if path:
                break
        if not path:
            # AI image from the beat's own sentence - always unique per beat
            path = _pollinations(beat.get("say") or queries[0], stem, seed=1000 + i * 7)

        if path:
            kind = "video" if path.lower().endswith(_VIDEO_EXTS) else "image"
            item = {"beat": i, "path": path, "kind": kind, "visual": queries[0]}
            results.append(item)
            distinct.append(item)
        elif distinct:
            src = distinct[i % len(distinct)]           # rotate, don't freeze on one clip
            print(f"[media]   beat {i}: no new asset - reusing beat {src['beat']}'s")
            results.append({**src, "beat": i})
        else:
            raise SystemExit(f"[media] no media for beat {i} ({queries!r})")

    nv = sum(r["kind"] == "video" for r in results)
    uniq = len({r["path"] for r in results})
    print(f"[media] {nv} video / {len(results) - nv} image  "
          f"({uniq} distinct across {len(results)} beats)")
    if uniq < max(2, len(results) // 2):
        print("[media] WARNING: low visual variety - the script's per-beat visual "
              "phrases were too similar, or stock sources had few matches for them.")
    if config.PREFER_VIDEO and nv == 0 and not (
        config.PEXELS_API_KEY or config.PIXABAY_API_KEY or config.COVERR_API_KEY
    ):
        print("[media] tip: add PEXELS_API_KEY / PIXABAY_API_KEY / COVERR_API_KEY "
              "for real stock video (see README).")
    return results
