"""Fetch one background clip/image per script beat.

Per beat, first source that returns a usable file wins:
  1. Pexels video (portrait)          - only if PEXELS_API_KEY set
  2. Pexels photo (portrait)          - only if PEXELS_API_KEY set
  3. Pixabay video / photo            - only if PIXABAY_API_KEY set
  4. Openverse (CC real photos)       - KEYLESS
  5. Wikimedia Commons (real photos)  - KEYLESS
  6. Gemini AI image                  - only if GEMINI_API_KEY set
  7. Pollinations AI image            - KEYLESS, always-on fallback

Everything lands in work/media/ . Nothing here knows the topic text.
"""
from __future__ import annotations

import base64
import mimetypes
import time
import urllib.parse

import requests

from . import config

UA = {"User-Agent": "yt-shorts-agent/1.0 (github actions; educational shorts)"}
MEDIA_DIR = config.WORK / "media"
_used_urls: set[str] = set()


def _download(url: str, dest_stem: str, kind_hint: str = "") -> str | None:
    if not url or url in _used_urls:
        return None
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
        if size < 8_000:
            path.unlink(missing_ok=True)
            return None
        _used_urls.add(url)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   download failed ({exc})")
        return None


# --- keyed sources (optional) -----------------------------------------
def _pexels_video(q: str, stem: str) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": config.PEXELS_API_KEY, **UA},
            params={"query": q, "orientation": "portrait", "per_page": 8, "size": "medium"},
            timeout=30,
        )
        r.raise_for_status()
        for vid in r.json().get("videos", []):
            files = [f for f in vid.get("video_files", []) if (f.get("height") or 0) >= (f.get("width") or 0)]
            files.sort(key=lambda f: abs((f.get("height") or 0) - 1920))
            for f in files:
                got = _download(f["link"], stem, "video")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pexels video error: {exc}")
    return None


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


def _pixabay(q: str, stem: str) -> str | None:
    if not config.PIXABAY_API_KEY:
        return None
    try:
        rv = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": config.PIXABAY_API_KEY, "q": q, "per_page": 5},
            headers=UA, timeout=30,
        )
        if rv.ok:
            for hit in rv.json().get("hits", []):
                v = hit.get("videos", {})
                for key in ("large", "medium", "small"):
                    got = _download(v.get(key, {}).get("url", ""), stem, "video")
                    if got:
                        return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pixabay video error: {exc}")
    try:
        rp = requests.get(
            "https://pixabay.com/api/",
            params={"key": config.PIXABAY_API_KEY, "q": q, "image_type": "photo",
                    "orientation": "vertical", "per_page": 5},
            headers=UA, timeout=30,
        )
        if rp.ok:
            for hit in rp.json().get("hits", []):
                got = _download(hit.get("largeImageURL", ""), stem, "image")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pixabay photo error: {exc}")
    return None


# --- keyless real-photo sources -------------------------------------
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


def _wikimedia(q: str, stem: str) -> str | None:
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


# --- AI images ----------------------------------------------------
def _gemini_image(prompt: str, stem: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.0-flash-preview-image-generation:generateContent")
    try:
        r = requests.post(
            url, params={"key": config.GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": f"Vertical 9:16 photorealistic, no text, cinematic: {prompt}"}]}],
                "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
            },
            timeout=90,
        )
        if not r.ok:
            print(f"[media]   gemini image HTTP {r.status_code}")
            return None
        for part in r.json()["candidates"][0]["content"]["parts"]:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                path = MEDIA_DIR / f"{stem}.png"
                path.write_bytes(base64.b64decode(inline["data"]))
                return str(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   gemini image error: {exc}")
    return None


def _pollinations(prompt: str, stem: str, seed: int) -> str | None:
    q = urllib.parse.quote(prompt[:300])
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width={config.WIDTH}&height={config.HEIGHT}&nologo=true&model=flux&enhance=true&seed={seed}")
    for attempt in range(3):
        got = _download(url, stem, "image")
        if got:
            return got
        time.sleep(4 + attempt * 5)
    return None


def fetch_for_beats(beats: list[dict]) -> list[dict]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i, beat in enumerate(beats):
        q = beat["visual"]
        stem = f"beat{i:02d}"
        print(f"[media] beat {i}: {q!r}")
        path = (
            _pexels_video(q, stem)
            or _pexels_photo(q, stem)
            or _pixabay(q, stem)
            or _openverse(q, stem)
            or _wikimedia(q, stem)
            or _gemini_image(q, stem)
            or _pollinations(q, stem, seed=1000 + i)
        )
        if not path:
            if results:
                print("[media]   nothing found - reusing previous asset")
                results.append({**results[-1], "beat": i})
                continue
            raise SystemExit(f"[media] no media for beat {i} ({q!r})")
        kind = "video" if path.lower().endswith((".mp4", ".mov", ".webm", ".m4v")) else "image"
        results.append({"beat": i, "path": path, "kind": kind, "visual": q})
    nv = sum(r["kind"] == "video" for r in results)
    print(f"[media] {nv} video / {len(results) - nv} image assets")
    return results
