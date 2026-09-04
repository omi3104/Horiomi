"""Fetch one on-topic visual per script beat, tuned for a HISTORY channel.

Per beat, in order:
  1. a REAL picture from the topic's OWN Wikipedia page whose filename matches
     this beat (the actual portrait / battle painting / map). Unique per topic,
     so the opening images stop repeating across videos.
  2. "anchor" beats (portrait / map / painting / person-name keyword) -> other
     real sources (Wikimedia art, Met Museum).
  3. an AI image built from the beat's own sentence, with a per-TOPIC seed and
     a subject-forward prompt (not a generic "dramatic history painting").
  4. generic STOCK (Pexels / Pixabay), capped at ~25% of beats.

Real / stock candidates pass a keyword-overlap relevance gate so a "siege of
Baghdad" beat cannot land on a stock motorway clip. Result lists are shuffled
per beat and `_used_urls` blocks repeats.
"""
from __future__ import annotations

import base64
import mimetypes
import random
import re
import time
import urllib.parse
import zlib

import requests

from . import config

# Wikimedia throttles generic agents hard - their policy wants a real contact.
UA = {"User-Agent": "HoriomiHistoryShorts/1.1 (+https://github.com/omi3104/Horiomi)"}
MEDIA_DIR = config.WORK / "media"
_used_urls: set[str] = set()

_MIN_BYTES = 8_000
_MAX_VIDEO_BYTES = 90_000_000
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".ogv")
_PER_PAGE = 25

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "why", "how", "what", "that", "this", "these", "those",
    "it", "its", "as", "at", "by", "with", "from", "into", "than", "then",
    "too", "very", "so", "you", "your", "they", "their", "them", "not", "no",
    "can", "will", "just", "about", "over", "under", "more", "most", "some",
    "one", "two", "there", "here", "when", "which", "while", "because", "who",
    "had", "has", "have", "been", "would", "could", "after", "before", "during",
    "history", "historical", "ancient", "famous", "great", "first", "last",
}

# Loose era hint -> a stock/AI style phrase, so AI images look period-correct.
_ERA_HINTS = [
    (r"rome|roman|caesar|byzanti|constantin", "ancient Rome, marble and bronze, Roman legionaries"),
    (r"greece|greek|athen|sparta|alexander|hellen", "ancient Greece, classical marble, hoplites"),
    (r"persia|persian|achaemenid|sassan|cyrus|darius|xerxes", "ancient Persia, Persepolis reliefs"),
    (r"egypt|pharaoh|nile|pyramid|cleopatra", "ancient Egypt, sandstone temples, hieroglyphs"),
    (r"mughal|akbar|aurangzeb|shah jahan|babur|delhi sultanate|tipu|maratha|sikh empire|india|hindustan",
     "Mughal India, red sandstone forts, miniature-painting style"),
    (r"ottoman|suleiman|janissar|istanbul|topkapi", "Ottoman Empire, tilework, janissaries"),
    (r"abbasid|umayyad|caliph|baghdad|cordoba|al-andalus|islamic golden age|saladin|crusade",
     "medieval Islamic world, arabesque architecture, manuscripts"),
    (r"mongol|genghis|hulagu|timur|tamerlane", "Mongol steppe, horse archers, yurts"),
    (r"viking|norse|norman|saxon|1066|hastings", "early medieval Europe, longships, mail armour"),
    (r"napoleon|waterloo|french revolution|18th|19th centur", "Napoleonic era, muskets and cannon smoke"),
    (r"world war|western front|trench|1914|1918|1939|1945", "early 20th century, sepia documentary photo"),
    (r"cold war|1950s|1960s|berlin wall|cuban missile", "mid 20th century, grainy archival photo"),
]


def _rng(stem: str) -> random.Random:
    return random.Random(stem)


def _kw(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOPWORDS}


def _relevant(query: str, *texts: str) -> bool:
    """True if the candidate's text shares a meaningful word with the query."""
    q = _kw(query)
    if not q:
        return True
    return bool(q & _kw(" ".join(t or "" for t in texts)))


def _era_style(topic: str) -> str:
    t = (topic or "").lower()
    for pat, hint in _ERA_HINTS:
        if re.search(pat, t):
            return hint
    return "historical scene, period-accurate"


def _clean_query(text: str) -> str:
    words = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    kept = [w for w in words if w not in _STOPWORDS]
    return " ".join(kept[:6]) or (text or "").strip()


def _beat_queries(beat: dict, topic: str) -> list[str]:
    visual = (beat.get("visual") or "").strip()
    say = beat.get("say") or ""
    kw = beat.get("keyword") or ""
    cands = [
        visual,
        f"{kw} {topic}".strip() if kw else "",
        _clean_query(f"{visual} {topic}"),
        _clean_query(say),
        _clean_query(topic),
        " ".join(_clean_query(say).split()[:3]),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        c = re.sub(r"\s+", " ", c or "").strip()
        if len(c) >= 3 and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out or [topic or "history"]


def _download(url: str, dest_stem: str, kind_hint: str = "") -> str | None:
    if not url:
        return None
    url = url.split("?utm", 1)[0]          # drop Wikimedia's tracking params
    if url in _used_urls:
        return None
    cap = _MAX_VIDEO_BYTES if kind_hint == "video" else 25_000_000
    for attempt in range(3):
        path = None
        try:
            with requests.get(url, headers=UA, stream=True, timeout=90) as r:
                if r.status_code == 429:
                    time.sleep(2 + attempt * 3)
                    continue
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
                            raise ValueError(f"exceeds {cap // 1_000_000} MB cap")
            if size < _MIN_BYTES:
                path.unlink(missing_ok=True)
                return None
            _used_urls.add(url)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[media]   download failed ({exc})")
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return None
    print("[media]   download gave up after repeated 429s")
    return None


# =====================================================================
#  KEYLESS history-image sources (the primary path)
# =====================================================================
def _commons_search(gsrsearch: str, stem: str, want: str = "bitmap") -> str | None:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{gsrsearch} filetype:{want}", "gsrlimit": _PER_PAGE,
                "gsrnamespace": 6, "prop": "imageinfo",
                "iiprop": "url|size|extmetadata", "iiurlwidth": config.WIDTH,
            },
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        pages = list(r.json().get("query", {}).get("pages", {}).values())
        _rng(stem).shuffle(pages)
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            title = p.get("title", "")
            meta = info.get("extmetadata", {}) or {}
            desc = (meta.get("ImageDescription", {}) or {}).get("value", "")
            if not _relevant(gsrsearch, title, desc):
                continue
            url = info.get("thumburl") or info.get("url", "")
            got = _download(url, stem, "video" if want == "video" else "image")
            if got:
                return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   commons({want}) error: {exc}")
    return None


def _wikimedia_art(q: str, stem: str) -> str | None:
    return _commons_search(
        f'{q} (painting OR portrait OR engraving OR fresco OR illustration OR '
        f'manuscript OR relief OR sculpture)', stem)


def _wikimedia_map(q: str, stem: str) -> str | None:
    return _commons_search(f"{q} (map OR territory OR empire extent)", stem)


def _wikimedia_photo(q: str, stem: str) -> str | None:
    return _commons_search(q, stem)


def _wikimedia_video(q: str, stem: str) -> str | None:
    return _commons_search(q, stem, want="video")


def _topic_wikipedia_images(topic: str) -> list[tuple[str, str]]:
    """The real pictures ON the topic's own Wikipedia page(s) - portraits of the
    key figures, the famous painting of the event, maps. Returns (url, name) so
    each beat can pick the one whose filename best matches it. Unique per topic,
    so the opening images stop repeating across videos."""
    out: list[tuple[str, str]] = []
    try:
        s = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": topic,
                    "srlimit": 3, "format": "json"},
            headers=UA, timeout=20,
        ).json()
        titles = [h["title"] for h in s.get("query", {}).get("search", [])][:2]
        if not titles:
            return out
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "titles": "|".join(titles), "format": "json",
                    "generator": "images", "gimlimit": 60,
                    "prop": "imageinfo", "iiprop": "url|size", "iiurlwidth": config.WIDTH},
            headers=UA, timeout=25,
        ).json()
        junk = (".svg", "commons-logo", "wiki", "icon", "edit-", "ambox",
                "question_book", "folder", "loudspeaker", "red_pog", "increase",
                "decrease", "symbol", "flag_of", "coat_of_arms", "map_marker",
                "gnome-", "crystal_", "nuvola", "portal-", "star_full",
                "location_dot", "blank_", "disambig", "wiktionary", "ogg", "spoken")
        seen: set[str] = set()
        for p in r.get("query", {}).get("pages", {}).values():
            name = p.get("title", "").replace("File:", "")
            low = name.lower()
            if low in seen or any(x in low for x in junk):
                continue
            if not _relevant(topic, name.replace("_", " ")):
                continue
            info = (p.get("imageinfo") or [{}])[0]
            u = info.get("thumburl") or info.get("url", "")
            if u and (info.get("width") or 0) >= 350:
                seen.add(low)
                out.append((u.split("?utm", 1)[0], name.replace("_", " ")))
    except Exception as exc:  # noqa: BLE001
        print(f"[media] wikipedia topic images failed: {exc}")
    print(f"[media] {len(out)} real Wikipedia images for {topic!r}")
    return out


def _pool_pick(pool: list[tuple[str, str]], used: set[str], beat: dict, stem: str) -> str | None:
    """Best unused image from the topic pool for this beat, matched on filename."""
    want = _kw(f"{beat.get('visual','')} {beat.get('keyword','')} {beat.get('say','')}")
    scored = []
    for url, name in pool:
        if url in used:
            continue
        overlap = len(want & _kw(name))
        scored.append((overlap, url))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_overlap, best_url = scored[0]
    # take a matched image (>=2 shared words), or the lead image for beat 0
    if best_overlap >= 2 or (stem.endswith("00") and best_overlap >= 1):
        got = _download(best_url, stem, "image")
        if got:
            used.add(best_url)
            print(f"[media]   -> wikipedia_topic_image (match {best_overlap})")
            return got
    return None


def _met_museum(q: str, stem: str) -> str | None:
    try:
        s = requests.get(
            "https://collectionapi.metmuseum.org/public/collection/v1/search",
            params={"q": q, "hasImages": "true"}, headers=UA, timeout=25,
        ).json()
        ids = s.get("objectIDs") or []
        _rng(stem).shuffle(ids)
        for oid in ids[:8]:
            o = requests.get(
                f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}",
                headers=UA, timeout=20,
            ).json()
            if not o.get("isPublicDomain"):
                continue
            img = o.get("primaryImage") or o.get("primaryImageSmall")
            if img and _relevant(q, o.get("title"), o.get("culture"),
                                 o.get("period"), o.get("objectName")):
                got = _download(img, stem, "image")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   met error: {exc}")
    return None


def _openverse(q: str, stem: str) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": q, "license_type": "commercial", "size": "large",
                    "mature": "false", "page_size": _PER_PAGE},
            headers=UA, timeout=30,
        )
        if not r.ok:
            return None
        items = r.json().get("results", [])
        _rng(stem).shuffle(items)
        for item in items:
            if not _relevant(q, item.get("title"), " ".join(
                    t.get("name", "") for t in item.get("tags", []) or [])):
                continue
            got = _download(item.get("url", ""), stem, "image")
            if got:
                return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   openverse error: {exc}")
    return None


# =====================================================================
#  AI image (keyless Pollinations; Gemini if a key exists) - always on-topic
# =====================================================================
_GEMINI_IMAGE_MODELS = (
    "gemini-2.5-flash-image-preview", "gemini-2.5-flash-image",
    "gemini-2.0-flash-preview-image-generation",
)


def _seed_for(topic: str, i: int) -> int:
    """Per-topic, per-beat seed - so beat 0 is not seed 1000 in EVERY video."""
    return (zlib.crc32(f"{topic}|{i}".encode()) % 2_000_000) + 1


def _ai_prompt(beat: dict, topic: str) -> str:
    # subject-forward and short, so the model renders THIS scene, not a generic
    # "dramatic history painting". Style is a light suffix only.
    vis = (beat.get("visual") or "").strip()
    say = (beat.get("say") or "").strip()
    kw = (beat.get("keyword") or "").strip()
    subject = vis or say or topic
    bits = [subject]
    if kw and kw.lower() not in subject.lower() and not kw.replace(",", "").isdigit():
        bits.append(kw)
    if say and say.lower() != subject.lower():
        bits.append(say)
    core = ". ".join(bits)
    return (f"{core}. {topic}, {_era_style(topic)}. "
            f"realistic historical painting, period-accurate, detailed faces, "
            f"cinematic light, vertical, no text no watermark")


def _gemini_image(prompt: str, stem: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": f"Vertical 9:16, no text: {prompt}"}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    for model in _GEMINI_IMAGE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=payload, timeout=90)
            if not r.ok:
                print(f"[media]   gemini image {model} HTTP {r.status_code}")
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
    base = (f"https://image.pollinations.ai/prompt/{q}"
            f"?width={config.WIDTH}&height={config.HEIGHT}&nologo=true"
            f"&enhance=true&seed={seed}")
    for model in ("flux", "turbo", ""):
        url = base + (f"&model={model}" if model else "")
        for attempt in range(2):
            got = _download(url, stem, "image")
            if got:
                return got
            time.sleep(3 + attempt * 4)
    return None


def _ai_image(beat: dict, topic: str, stem: str, seed: int) -> str | None:
    prompt = _ai_prompt(beat, topic)
    got = _gemini_image(prompt, stem)
    if got:
        print("[media]   -> AI image (gemini)")
        return got
    got = _pollinations(prompt, stem, seed)
    if got:
        print(f"[media]   -> AI image (pollinations, seed {seed})")
    return got


# =====================================================================
#  Optional stock (only when a key is set AND PREFER_VIDEO)
# =====================================================================
def _pexels_photo(q: str, stem: str) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY, **UA},
            params={"query": q, "orientation": "portrait", "per_page": _PER_PAGE},
            timeout=30,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        _rng(stem).shuffle(photos)
        for photo in photos:
            if not _relevant(q, photo.get("alt")):
                continue
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
                    "orientation": "vertical", "per_page": _PER_PAGE},
            headers=UA, timeout=30,
        )
        if r.ok:
            hits = r.json().get("hits", [])
            _rng(stem).shuffle(hits)
            for hit in hits:
                if not _relevant(q, hit.get("tags")):
                    continue
                got = _download(hit.get("largeImageURL", ""), stem, "image")
                if got:
                    return got
    except Exception as exc:  # noqa: BLE001
        print(f"[media]   pixabay photo error: {exc}")
    return None


def _pexels_video(q: str, stem: str) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": config.PEXELS_API_KEY, **UA},
            params={"query": q, "orientation": "portrait", "per_page": _PER_PAGE, "size": "medium"},
            timeout=30,
        )
        r.raise_for_status()
        vids = r.json().get("videos", [])
        _rng(stem).shuffle(vids)
        for vid in vids:
            files = sorted(vid.get("video_files", []),
                           key=lambda f: abs((f.get("height") or 0) - 1920))
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


# ---------------------------------------------------------------------
_REAL_PROVIDERS = (_wikimedia_art, _met_museum, _wikimedia_map, _wikimedia_photo)
_STOCK_PROVIDERS = (_pexels_photo, _pixabay_photo, _openverse)
_VIDEO_PROVIDERS = (_pexels_video, _pixabay_video, _wikimedia_video)

# beats that really want a specific real artifact rather than a painted scene
_ANCHOR_HINT = re.compile(
    r"\bportrait\b|\bmap\b|\bpainting\b|\bcoin(age)?\b|manuscript|fresco|"
    r"engraving|\bcharter\b|\btreaty\b|photograph|\bmemorial\b|\bmonument\b|"
    r"\bruins\b|\bstatue\b|\brelief\b|\btomb\b|mosaic|\bbust\b|tapestry", re.I)


def _needs_real(beat: dict) -> bool:
    vis = (beat.get("visual") or "")
    if _ANCHOR_HINT.search(vis):
        return True
    kw = (beat.get("keyword") or "").strip()
    caps = [w for w in kw.split() if w[:1].isupper()]
    return len(caps) >= 2


def fetch_for_beats(beats: list[dict], topic: str = "") -> list[dict]:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    topic_pool = _topic_wikipedia_images(topic) if topic else []
    pool_used: set[str] = set()
    stock_cap = max(1, round(len(beats) * 0.25))
    stock_used = 0
    stock = list(_STOCK_PROVIDERS) + (list(_VIDEO_PROVIDERS) if config.PREFER_VIDEO else [])

    def _try_real(beat, queries, stem):
        for q in queries:
            for provider in _REAL_PROVIDERS:
                got = provider(q, stem)
                if got:
                    print(f"[media]   -> {provider.__name__} ({q!r})")
                    return got
        return None

    def _try_stock(queries, stem):
        nonlocal stock_used
        if stock_used >= stock_cap:
            return None
        for q in queries:
            for provider in stock:
                got = provider(q, stem)
                if got:
                    stock_used += 1
                    print(f"[media]   -> {provider.__name__} (stock {stock_used}/{stock_cap}, {q!r})")
                    return got
        return None

    results: list[dict] = []
    distinct: list[dict] = []
    src_mix: dict[str, int] = {"ai": 0, "real": 0, "stock": 0}
    for i, beat in enumerate(beats):
        stem = f"beat{i:02d}"
        queries = _beat_queries(beat, topic)
        anchor = _needs_real(beat)
        print(f"[media] beat {i} ({'anchor' if anchor else 'scene'}): {queries[:2]}")

        path = kindtag = None
        # 1. a REAL Wikipedia picture of THIS topic that matches the beat -
        #    unique per video, so opening images stop repeating
        got = _pool_pick(topic_pool, pool_used, beat, stem)
        if got:
            path, kindtag = got, "real"

        if not path:
            seq = (["real", "ai", "stock"] if anchor else ["ai", "real", "stock"])
            for step in seq:
                if step == "ai":
                    path = _ai_image(beat, topic, stem, _seed_for(topic, i))
                    kindtag = "ai" if path else kindtag
                elif step == "real":
                    path = _try_real(beat, queries, stem)
                    kindtag = "real" if path else kindtag
                else:
                    path = _try_stock(queries, stem)
                    kindtag = "stock" if path else kindtag
                if path:
                    break

        if path:
            src_mix[kindtag] += 1
            kind = "video" if path.lower().endswith(_VIDEO_EXTS) else "image"
            item = {"beat": i, "path": path, "kind": kind, "visual": queries[0]}
            results.append(item)
            distinct.append(item)
        elif distinct:
            s = distinct[i % len(distinct)]
            print(f"[media]   beat {i}: nothing new - reusing beat {s['beat']}'s")
            results.append({**s, "beat": i})
        else:
            raise SystemExit(f"[media] no media for beat {i} ({queries!r})")

    uniq = len({r["path"] for r in results})
    tot = len(results)
    print(f"[media] {tot} beats: {src_mix['ai']} AI / {src_mix['real']} real / "
          f"{src_mix['stock']} stock  ({uniq} distinct)")
    if uniq < max(2, tot * 2 // 3):
        print("[media] WARNING: low visual variety for this topic")
    return results
