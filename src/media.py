"""Fetch one background clip/image per script beat.

Per beat the sources are tried in order and the first usable file wins. When
config.PREFER_VIDEO is on (default) every VIDEO source is tried before any
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
                params={"query": q, "orientation": orientation, "per_page": 10, "size": "medium"},
                timeout=30,
            )
            r.raise_for_status()
            for vid in r.json().get("videos", []):
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
            params={"key": config.PIXABAY_API_KEY, "q": q, "per_page": 8},
            headers=UA, timeout=30,
        )
        if r.ok:
            for hit in r.json().get("hits", []):
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
            params={"apiKey": config.COVERR_API_KEY, "query": q, "page_size": 8, "urls": "true"},
            headers=UA, timeout=30,
        )
        if not r.ok:
            print(f"[media]   coverr HTTP {r.status_code}")
            return None
        data = r.json()
        hits = data.get("hits") or data.get("videos") or []
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
                "gsrsearch": f"{q} filetype:video", "gsrlimit": 8, "gsrnamespace": 6,
                "prop": "imageinfo", "iiprop": "url|size|mime",
            },
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        pages = r.json().get("query", {}).get("pages", {})
        cands = sorted(pages.values(), key=lambda p: -(p.get("imageinfo", [{}])[0].get("width", 0) or 0))
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
    q = urllib.parse.quote(prompt[:300])
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width={config.WIDTH}&height={config.HEIGHT}&nologo=true&model=flux&enhance=true&seed={seed}")
    for attempt in range(3):
        got = _download(url, stem, "image")
        if got:
            return got
        time.sleep(4 + attempt * 5)
    return None


_VIDEO_PROVIDERS = (_pexels_video, _pixabay_video, _coverr_video, _wikimedia_video)
_IMAGE_PROVIDERS = (_pexels_photo, _pixabay_photo, _openverse, _wikimedia_photo, _gemini_image)


def fetch_for_beats(beats: list[dict]) -> list[dict]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    providers = (list(_VIDEO_PROVIDERS) if config.PREFER_VIDEO else []) + list(_IMAGE_PROVIDERS)

    results: list[dict] = []
    for i, beat in enumerate(beats):
        q = beat["visual"]
        stem = f"beat{i:02d}"
        print(f"[media] beat {i}: {q!r}")
        path = None
        for provider in providers:
            path = provider(q, stem)
            if path:
                break
        if not path:
            path = _pollinations(q, stem, seed=1000 + i)

        if not path:
            if results:
                print("[media]   nothing found - reusing previous asset")
                results.append({**results[-1], "beat": i})
                continue
            raise SystemExit(f"[media] no media for beat {i} ({q!r})")

        kind = "video" if path.lower().endswith(_VIDEO_EXTS) else "image"
        results.append({"beat": i, "path": path, "kind": kind, "visual": q})

    nv = sum(r["kind"] == "video" for r in results)
    print(f"[media] {nv} video / {len(results) - nv} image assets")
    if config.PREFER_VIDEO and nv == 0 and not (
        config.PEXELS_API_KEY or config.PIXABAY_API_KEY or config.COVERR_API_KEY
    ):
        print("[media] tip: add PEXELS_API_KEY / PIXABAY_API_KEY / COVERR_API_KEY "
              "for real stock video (see README).")
    return results
