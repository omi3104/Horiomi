"""Map v1.1 - an animated sequence of political-map states, rendered with
Pillow plus a couple of plain ffmpeg calls (image-sequence -> mp4).

Borders come from the historical-basemaps project
(github.com/aourednik/historical-basemaps, CC-BY-SA 4.0): a set of world
GeoJSON snapshots from 123000 BC to 2010. Files are fetched at run time and
cached under work/maps_cache/. No matplotlib / geopandas / cartopy, so this
adds nothing to requirements.txt.

Each beat's map state is composed once: several named polities get their own
flat colour (not just "the highlighted one vs. a grey blob"), the beat's
subject is filled in the channel amber, and a year badge is added. Beats
after the first are not a hard cut to the next still: the previous beat's
state is re-rendered into the SAME frame (so the two align pixel-for-pixel)
and a short "ink spreads outward from the empire" reveal clip carries the
map from the old state into the new one - video.py then holds on the final
frame for the rest of the beat's speaking time (the "hold_last" media kind).

A beat dict may carry, on top of the usual say / keyword:
  year      int    - snapshot to draw (negative = BC); nearest available wins
  highlight [str]  - polity / region names to fill in the accent colour
  focus     str    - optional region name ("Europe", "South Asia", ...) or
                     "" to auto-fit the highlighted polygons

render_beats(beats, topic) -> list[dict] | None
  beat 0 -> {"kind": "image", "path": ...}
  beat 1+ -> {"kind": "video", "path": ..., "hold_last": True}
  (video.render() already understands both.) Returns None if the dataset
  can't be reached at all (pipeline then falls back to slideshow).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.request
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, util

_DATA_BASE = config.get(
    "MAP_DATA_BASE",
    "https://raw.githubusercontent.com/aourednik/historical-basemaps/master/geojson/",
)
_CACHE = config.WORK / "maps_cache"
_REVEAL_DIR = config.WORK / "map_reveal"
_SS = max(1, config.get_int("MAP_SUPERSAMPLE", 2))      # render Nx then downscale
_W, _H = config.WIDTH, config.HEIGHT
_CW, _CH = _W * _SS, _H * _SS
_REVEAL_FRAMES = config.get_int("MAP_REVEAL_FRAMES", 14)
_REVEAL_FPS = 15

# dark "map-history channel" palette, keyed to the channel's amber (0xE0A82E)
_OCEAN = (11, 17, 24)
_LAND = (39, 46, 55)
_LAND_ALT = (33, 39, 47)
_BORDER = (72, 82, 94)
_ACCENT = (224, 168, 46)
_ACCENT_EDGE = (255, 224, 138)
_NEIGHBOUR = (150, 120, 66)     # other polities that share the beat's SUBJECTO
_TEXT = (238, 240, 244)

# other named, sizeable polities get one of these instead of flat grey, so
# the map reads as "who's who" and not just one blob on a featureless
# continent. Picked by a stable hash of the name, so the same empire keeps
# the same colour across every beat of the video.
_POLITY_PALETTE = [
    (88, 132, 186), (98, 160, 132), (178, 110, 140), (141, 124, 188),
    (96, 170, 170), (183, 143, 90),
]
_MAX_COLOURED = 6
_MIN_COLOUR_AREA = 0.006     # fraction of canvas area a polity needs for its own colour
_MIN_LABEL_BOX = (46, 20)    # px (final resolution) a polity needs to fit a name label

_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

# available snapshots (from the repo's geojson/ listing), newest match wins
_YEARS = [
    -123000, -10000, -8000, -5000, -4000, -3000, -2000, -1500, -1000, -700,
    -500, -400, -323, -300, -200, -100, -1,
    100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1279,
    1300, 1400, 1492, 1500, 1530, 1600, 1650, 1700, 1715, 1783, 1800, 1815,
    1880, 1900, 1914, 1920, 1930, 1938, 1945, 1960, 1994, 2000, 2010,
]

# named regions the script can zoom to (lon_min, lat_min, lon_max, lat_max)
_REGIONS = {
    "world": (-168, -52, 178, 74),
    "eurasia": (-12, 5, 145, 62),
    "europe": (-12, 34, 45, 60),
    "western europe": (-11, 40, 20, 56),
    "central europe": (3, 41, 32, 56),
    "eastern europe": (16, 42, 52, 61),
    "northern europe": (-6, 48, 32, 66),
    "southern europe": (-10, 34, 30, 48),
    "mediterranean": (-7, 29, 38, 47),
    "balkans": (13, 34, 32, 47),
    "iberia": (-10, 35, 5, 44),
    "france": (-6, 41, 10, 52),
    "britain": (-11, 49, 3, 61),
    "italy": (6, 36, 19, 47),
    "germany": (2, 45, 20, 56),
    "scandinavia": (3, 54, 33, 71),
    "russia": (18, 41, 150, 72),
    "greece": (18, 34, 30, 42),
    "aegean": (19, 34, 30, 41),
    "black sea": (27, 40, 42, 47),
    "mesopotamia": (38, 29, 49, 38),
    "anatolia": (25, 35, 45, 43),
    "asia minor": (25, 35, 45, 43),
    "near east": (25, 27, 50, 42),
    "middle east": (24, 12, 64, 42),
    "levant": (32, 29, 42, 38),
    "arabia": (33, 12, 60, 33),
    "persia": (43, 24, 64, 40),
    "iran": (43, 24, 64, 40),
    "caucasus": (37, 37, 51, 45),
    "central asia": (50, 33, 82, 51),
    "north africa": (-13, 17, 37, 38),
    "east africa": (28, -12, 52, 18),
    "west africa": (-18, 3, 16, 20),
    "africa": (-19, -36, 52, 38),
    "egypt": (24, 21, 37, 32),
    "sahara": (-13, 15, 34, 32),
    "south asia": (60, 5, 92, 37),
    "india": (67, 6, 90, 35),
    "indian subcontinent": (60, 5, 92, 37),
    "southeast asia": (92, -11, 142, 24),
    "east asia": (95, 18, 146, 46),
    "china": (73, 17, 126, 46),
    "japan": (128, 30, 146, 46),
    "north america": (-168, 7, -52, 72),
    "south america": (-82, -56, -34, 13),
    "americas": (-168, -56, -34, 74),
    "atlantic": (-82, -8, 25, 62),
}

_ALIASES = {
    "germany": ("german reich", "germany", "deutsches reich", "nazi germany",
                "german empire", "west germany", "east germany"),
    "german reich": ("german reich", "germany", "greater german reich"),
    "byzantine empire": ("byzantine", "eastern roman", "byzantium", "east roman"),
    "byzantium": ("byzantine", "eastern roman", "byzantium"),
    "roman empire": ("roman empire", "rome", "western roman", "roman republic"),
    "rome": ("roman empire", "rome", "roman republic"),
    "ottoman empire": ("ottoman", "turkey", "sublime porte"),
    "ottomans": ("ottoman", "turkey"),
    "mughal empire": ("mughal", "mogul", "hindustan", "timurid india"),
    "mughals": ("mughal", "mogul"),
    "british empire": ("united kingdom", "great britain", "british", "england"),
    "britain": ("united kingdom", "great britain", "british", "england"),
    "england": ("united kingdom", "england", "great britain"),
    "united kingdom": ("united kingdom", "great britain", "british", "england"),
    "soviet union": ("soviet", "u.s.s.r", "ussr", "russia"),
    "ussr": ("soviet", "u.s.s.r", "ussr", "russia"),
    "russia": ("russia", "russian empire", "soviet", "muscovy", "ussr"),
    "persia": ("persia", "iran", "safavid", "achaemenid", "sassan", "qajar",
               "parthian"),
    "iran": ("persia", "iran", "safavid", "qajar"),
    "abbasid caliphate": ("abbasid", "caliphate"),
    "umayyad caliphate": ("umayyad", "caliphate"),
    "caliphate": ("caliphate", "abbasid", "umayyad", "rashidun"),
    "france": ("france", "french", "gaul", "frankish", "west francia"),
    "spain": ("spain", "castile", "aragon", "hispania", "al-andalus"),
    "austria": ("austria", "habsburg", "austria-hungary", "austrian empire"),
    "austria-hungary": ("austria-hungary", "habsburg", "austria"),
    "poland": ("poland", "polish", "polish-lithuanian", "rzeczpospolita"),
    "delhi sultanate": ("delhi sultanate", "delhi", "sultanate of delhi"),
    "sikh empire": ("sikh", "sikhs", "punjab"),
    "sikhs": ("sikh", "sikhs", "punjab"),
    "durrani empire": ("durrani", "afghan", "afghanistan"),
    "afghan durrani empire": ("durrani", "afghan", "afghanistan"),
    "afghanistan": ("durrani", "afghan", "afghanistan"),
    "maratha empire": ("maratha", "marathas"),
    "maratha confederacy": ("maratha", "marathas"),
    "marathas": ("maratha", "marathas"),
    "maurya empire": ("maurya", "mauryan"),
    "gupta empire": ("gupta",),
    "mongol empire": ("mongol", "yuan", "golden horde", "ilkhanate", "chagatai"),
    "mongols": ("mongol", "yuan", "golden horde", "ilkhanate"),
    "china": ("china", "qing", "ming", "han", "tang", "song", "yuan", "qin"),
    "japan": ("japan", "japanese", "nippon"),
    "greece": ("greece", "hellenic", "macedon", "athens", "sparta"),
    "macedonia": ("macedon", "macedonia", "alexander"),
    "egypt": ("egypt", "egyptian", "ptolem", "mamluk"),
}

_JSON_CACHE: dict[int, dict] = {}

# names the script writer may use for a beat's "focus"
FOCUS_REGIONS = tuple(_REGIONS)
# snapshot years the dataset actually carries (script anchors beats near these)
SNAPSHOT_YEARS = tuple(_YEARS)


# --------------------------------------------------------------------------- #
#  data
# --------------------------------------------------------------------------- #
def _year_file(year: int) -> str:
    return f"world_bc{abs(year)}.geojson" if year < 0 else f"world_{year}.geojson"


def _nearest_year(year: int) -> int:
    return min(_YEARS, key=lambda y: abs(y - year))


def _load(year: int) -> dict:
    """Fetch (and disk-cache) the nearest snapshot; raise on a hard failure."""
    y = _nearest_year(year)
    if y in _JSON_CACHE:
        return _JSON_CACHE[y]
    _CACHE.mkdir(parents=True, exist_ok=True)
    local = _CACHE / _year_file(y)
    if not local.exists():
        url = _DATA_BASE + _year_file(y)
        req = urllib.request.Request(url, headers={"User-Agent": "horiomi-maps/1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            local.write_bytes(resp.read())
    gj = json.loads(local.read_text(encoding="utf-8", errors="replace"))
    _JSON_CACHE[y] = gj
    return gj


# --------------------------------------------------------------------------- #
#  name matching
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _contains_word(haystack: str, needle: str) -> bool:
    """Substring containment, but only at word boundaries - so a short alias
    token like 'han' does not spuriously match inside 'afghan'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _wanted_terms(name: str) -> list[str]:
    n = _norm(name)
    terms = {n}
    for key, al in _ALIASES.items():
        if n == key or n in al or any(_contains_word(n, a) for a in al):
            terms.update(_ALIASES[key])
            terms.add(key)
    return [t for t in terms if len(t) >= 3]


def _feat_names(feat: dict) -> list[str]:
    p = feat.get("properties", {}) or {}
    return [_norm(str(p.get(k, ""))) for k in ("NAME", "SUBJECTO", "PARTOF", "ABBREVN")
            if p.get(k)]


def _matches(feat: dict, wanted: list[str]) -> bool:
    fn = _feat_names(feat)
    if not fn:
        return False
    for w in wanted:
        for name in fn:
            if name == w or (len(w) >= 4 and _contains_word(name, w)):
                return True
    return False


# --------------------------------------------------------------------------- #
#  geometry / projection
# --------------------------------------------------------------------------- #
def _rings(geom: dict):
    """Yield exterior rings (lists of [lon, lat]) from Polygon / MultiPolygon."""
    t = (geom or {}).get("type")
    c = (geom or {}).get("coordinates")
    if not c:
        return
    if t == "Polygon":
        if c:
            yield c[0]
    elif t == "MultiPolygon":
        for poly in c:
            if poly:
                yield poly[0]


def _ring_bbox(ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _feat_bbox(feat: dict):
    box = None
    for ring in _rings(feat.get("geometry", {})):
        b = _ring_bbox(ring)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                     max(box[2], b[2]), max(box[3], b[3]))
    return box


def _pad(box, frac=0.12, minspan=4.0):
    w, s, e, n = box
    dx = max((e - w) * frac, minspan)
    dy = max((n - s) * frac, minspan)
    return (max(-179, w - dx), max(-85, s - dy),
            min(179, e + dx), min(85, n + dy))


def _fit_box(box):
    """Pad, then grow the short axis toward the 9:16 canvas aspect (capped) so
    the region sits large in frame without distorting the map."""
    w, s, e, n = _pad(box)
    lat0 = math.radians((s + n) / 2)
    k = max(0.2, math.cos(lat0))
    px, py = (e - w) * k, (n - s)              # projected spans (deg-equivalent)
    want = (_CH / _CW)                         # 1.778, portrait
    have = py / px
    if have < want:                            # too wide -> add some latitude
        grow = min(want / have, 1.3)
        dlat = (py * grow - py) / 2
        s, n = max(-85, s - dlat), min(85, n + dlat)
    else:                                      # too tall -> add some longitude
        grow = min(have / want, 1.3)
        dlon = ((px * grow - px) / 2) / k
        w, e = max(-179, w - dlon), min(179, e + dlon)
    return (w, s, e, n)


class _Proj:
    """Standard-parallel equirectangular fitted (contain) to a lon/lat box.
    Out-of-box geography is masked to ocean afterwards, so a wide region just
    gets clean sea bands top/bottom rather than spurious neighbours."""

    def __init__(self, box):
        w, s, e, n = box
        self.lat0 = math.radians((s + n) / 2)
        self.k = max(0.2, math.cos(self.lat0))
        x0 = math.radians(w) * self.k
        x1 = math.radians(e) * self.k
        y0 = math.radians(s)
        y1 = math.radians(n)
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        span_x, span_y = x1 - x0, y1 - y0
        scale = min(_CW / span_x, _CH / span_y)
        self.scale = scale
        self.off_x = (_CW - span_x * scale) / 2
        self.off_y = (_CH - span_y * scale) / 2
        # projected-box rectangle in pixels, for the ocean mask
        self.box_px = (self.off_x, self.off_y,
                       self.off_x + span_x * scale, self.off_y + span_y * scale)

    def __call__(self, lon, lat):
        x = math.radians(lon) * self.k
        y = math.radians(lat)
        return (self.off_x + (x - self.x0) * self.scale,
                self.off_y + (self.y1 - y) * self.scale)

    def ring_px(self, ring):
        out = []
        prev = None
        for lon, lat in ring:
            if prev is not None and abs(lon - prev) > 180:   # antimeridian split
                return None
            prev = lon
            out.append(self(lon, lat))
        return out if len(out) >= 3 else None


# --------------------------------------------------------------------------- #
#  drawing
# --------------------------------------------------------------------------- #
def _font(size: int):
    for p in _FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _year_label(year: int) -> str:
    return f"{abs(year)} BC" if year < 0 else f"AD {year}" if year < 1000 else str(year)


def _draw_badge(img: Image.Image, text: str) -> None:
    """Year badge, drawn at FINAL resolution (unlike the supersampled body
    of _compose), so callers apply it after resizing/compositing."""
    d = ImageDraw.Draw(img)
    f = _font(30)
    pad = 13
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    tw, th = r - l, b - t
    x0, y0 = 24, 30
    d.rounded_rectangle(
        [x0, y0, x0 + tw + pad * 2, y0 + th + pad * 2],
        radius=9, fill=(6, 10, 15), outline=_ACCENT, width=2,
    )
    d.text((x0 + pad - l, y0 + pad - t), text, font=f, fill=_ACCENT)


def _graticule(d: ImageDraw.ImageDraw, proj: "_Proj", box) -> None:
    w, s, e, n = box
    col = (26, 33, 42)
    for lon in range(int(math.floor(w / 10) * 10), int(e) + 10, 10):
        d.line([proj(lon, s), proj(lon, n)], fill=col, width=max(1, _SS))
    for lat in range(int(math.floor(s / 10) * 10), int(n) + 10, 10):
        d.line([proj(w, lat), proj(e, lat)], fill=col, width=max(1, _SS))


def _polity_colour(name: str) -> tuple[int, int, int]:
    idx = zlib.crc32(name.encode("utf-8")) % len(_POLITY_PALETTE)
    return _POLITY_PALETTE[idx]


def _label(d: ImageDraw.ImageDraw, text: str, cx: float, cy: float, size: int, fill) -> None:
    f = _font(size)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    w, h = r - l, b - t
    x, y = cx - w / 2, cy - h / 2
    for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        d.text((x + ox - l, y + oy - t), text, font=f, fill=(0, 0, 0))
    d.text((x - l, y - t), text, font=f, fill=fill)


def _compose(gj: dict, highlight: list[str], box) -> tuple[Image.Image, tuple[float, float] | None]:
    """Render one map state at final resolution (no year badge - callers add
    that afterwards). Returns (image, highlighted-region centroid in px)."""
    box = _fit_box(box)
    proj = _Proj(box)
    img = Image.new("RGB", (_CW, _CH), _OCEAN)
    d = ImageDraw.Draw(img)
    _graticule(d, proj, box)

    feats = gj.get("features", [])
    wanted = []
    for h in highlight:
        wanted.extend(_wanted_terms(h))
    wanted = list(dict.fromkeys(wanted))

    # project every feature once; reuse for land / borders / colour / labels
    cache = []
    for feat in feats:
        rings_px = []
        for ring in _rings(feat.get("geometry", {})):
            px = proj.ring_px(ring)
            if px:
                rings_px.append(px)
        if not rings_px:
            continue
        xs = [p[0] for ring in rings_px for p in ring]
        ys = [p[1] for ring in rings_px for p in ring]
        bbox_px = (min(xs), min(ys), max(xs), max(ys))
        area_px = (bbox_px[2] - bbox_px[0]) * (bbox_px[3] - bbox_px[1])
        cache.append({"feat": feat, "rings": rings_px, "bbox": bbox_px, "area": area_px})

    is_hi, hi_subjects = [], set()
    for c in cache:
        if wanted and _matches(c["feat"], wanted):
            is_hi.append(c)
            p = c["feat"].get("properties", {}) or {}
            if p.get("NAME"):
                hi_subjects.add(_norm(str(p["NAME"])))
    hi_ids = {id(c) for c in is_hi}

    # the biggest other NAMEd polities in view get their own colour
    canvas_area = _CW * _CH
    coloured = [c for c in cache
                if id(c) not in hi_ids
                and (c["feat"].get("properties", {}) or {}).get("NAME")
                and c["area"] >= canvas_area * _MIN_COLOUR_AREA]
    coloured.sort(key=lambda c: -c["area"])
    coloured = coloured[:_MAX_COLOURED]
    coloured_ids = {id(c) for c in coloured}
    colour_of = {id(c): _polity_colour(str((c["feat"].get("properties") or {}).get("NAME")))
                 for c in coloured}

    # 1) land
    for i, c in enumerate(cache):
        fill = colour_of.get(id(c)) or (_LAND if i % 2 else _LAND_ALT)
        for ring in c["rings"]:
            d.polygon(ring, fill=fill)
    # 2) borders
    for c in cache:
        for ring in c["rings"]:
            d.line(ring + [ring[0]], fill=_BORDER, width=max(1, _SS))
    # 3) polities that answer to the beat's subject's overlord (context)
    for c in cache:
        p = c["feat"].get("properties", {}) or {}
        subj = _norm(str(p.get("SUBJECTO", "")))
        if subj and subj in hi_subjects and id(c) not in hi_ids:
            for ring in c["rings"]:
                d.polygon(ring, fill=_NEIGHBOUR)
    # 4) the beat's subject, in the channel amber
    hi_cx = hi_cy = hi_n = 0.0
    for c in is_hi:
        for ring in c["rings"]:
            d.polygon(ring, fill=_ACCENT)
        for ring in c["rings"]:
            d.line(ring + [ring[0]], fill=_ACCENT_EDGE, width=max(2, 3 * _SS))
        bx0, by0, bx1, by1 = c["bbox"]
        hi_cx += (bx0 + bx1) / 2
        hi_cy += (by0 + by1) / 2
        hi_n += 1
    centroid_px = (hi_cx / hi_n, hi_cy / hi_n) if hi_n else None

    # labels: the subject, then the biggest coloured neighbours, if they fit
    def _fits(c):
        bx0, by0, bx1, by1 = c["bbox"]
        return (bx1 - bx0) > _MIN_LABEL_BOX[0] * _SS and (by1 - by0) > _MIN_LABEL_BOX[1] * _SS

    if is_hi:
        big = max(is_hi, key=lambda c: c["area"])
        if _fits(big):
            bx0, by0, bx1, by1 = big["bbox"]
            name = str((big["feat"].get("properties") or {}).get("NAME") or (highlight[0] if highlight else ""))
            _label(d, name.upper(), (bx0 + bx1) / 2, (by0 + by1) / 2, int(40 * _SS), _TEXT)
    for c in coloured[:4]:
        if not _fits(c):
            continue
        bx0, by0, bx1, by1 = c["bbox"]
        name = str((c["feat"].get("properties") or {}).get("NAME") or "")
        _label(d, name.upper(), (bx0 + bx1) / 2, (by0 + by1) / 2, int(32 * _SS), _TEXT)

    # mask any geography that fell outside the focus box into clean ocean
    bx0, by0, bx1, by1 = proj.box_px
    if by0 > 1:
        d.rectangle([0, 0, _CW, by0], fill=_OCEAN)
    if by1 < _CH - 1:
        d.rectangle([0, by1, _CW, _CH], fill=_OCEAN)
    if bx0 > 1:
        d.rectangle([0, 0, bx0, _CH], fill=_OCEAN)
    if bx1 < _CW - 1:
        d.rectangle([bx1, 0, _CW, _CH], fill=_OCEAN)

    # vignette
    vig = Image.new("L", (_CW, _CH), 0)
    ImageDraw.Draw(vig).ellipse(
        [-_CW * 0.30, -_CH * 0.18, _CW * 1.30, _CH * 1.18], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(_CW // 12))
    dark = Image.new("RGB", (_CW, _CH), (0, 0, 0))
    img = Image.composite(img, Image.blend(img, dark, 0.55), vig)

    img = img.resize((_W, _H), Image.LANCZOS)
    centroid = (centroid_px[0] / _SS, centroid_px[1] / _SS) if centroid_px else None
    return img, centroid


def _reveal_mask(centroid: tuple[float, float] | None, t: float) -> Image.Image:
    """A soft-edged circle, eased outward from `centroid` (or the canvas
    centre), covering the whole frame by t=1 - the 'ink spreads' reveal."""
    cx, cy = centroid or (_W / 2, _H / 2)
    max_r = math.hypot(max(cx, _W - cx), max(cy, _H - cy)) * 1.05
    r = max_r * (1 - (1 - t) ** 2)      # ease-out
    m = Image.new("L", (_W, _H), 0)
    ImageDraw.Draw(m).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return m.filter(ImageFilter.GaussianBlur(max(2, _W // 60)))


def _make_reveal_clip(prev_img: Image.Image, curr_img: Image.Image,
                       centroid: tuple[float, float] | None, badge_text: str,
                       dest: Path) -> str | None:
    """Write an mp4 that dissolves prev_img into curr_img via a growing
    circular reveal centred on the new territory, ending exactly on
    curr_img (+ badge) so video.py can freeze on that last frame."""
    _REVEAL_DIR.mkdir(parents=True, exist_ok=True)
    for f in _REVEAL_DIR.glob("frame_*.png"):
        f.unlink()
    n = _REVEAL_FRAMES
    for k in range(n):
        t = (k + 1) / n
        mask = _reveal_mask(centroid, t)
        frame = Image.composite(curr_img, prev_img, mask)
        _draw_badge(frame, badge_text)
        frame.save(_REVEAL_DIR / f"frame_{k:03d}.png", "PNG")
    try:
        util.run([
            "ffmpeg", "-y", "-framerate", str(_REVEAL_FPS), "-start_number", "0",
            "-i", str(_REVEAL_DIR / "frame_%03d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(config.FPS),
            str(dest),
        ], quiet=True)
    except SystemExit:
        return None
    return str(dest)


# --------------------------------------------------------------------------- #
#  public
# --------------------------------------------------------------------------- #
def _topic_region(topic: str):
    t = _norm(topic)
    hits = [(name, box) for name, box in _REGIONS.items() if name in t]
    if hits:
        return max(hits, key=lambda nb: len(nb[0]))[1]
    return None


def _zoom_box(box, frac):
    """Shrink a lon/lat box toward its centre (frac 0..0.9)."""
    w, s, e, n = box
    cx, cy = (w + e) / 2, (s + n) / 2
    f = 1 - max(0.0, min(0.9, frac))
    return (cx - (e - w) / 2 * f, cy - (n - s) / 2 * f,
            cx + (e - w) / 2 * f, cy + (n - s) / 2 * f)


def _beat_box(beat: dict, gj: dict, fallback):
    focus = _norm(str(beat.get("focus", "")))
    if focus:
        if focus in _REGIONS:
            return _REGIONS[focus]
        hits = [(name, box) for name, box in _REGIONS.items()
                if name in focus or focus in name]
        if hits:                                   # prefer the most specific key
            return max(hits, key=lambda nb: len(nb[0]))[1]
    # union of the highlighted polygons
    wanted = []
    for h in beat.get("highlight", []) or []:
        wanted.extend(_wanted_terms(h))
    box = None
    for feat in gj.get("features", []):
        if wanted and _matches(feat, wanted):
            b = _feat_bbox(feat)
            if b:
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                             max(box[2], b[2]), max(box[3], b[3]))
    return box or fallback


def render_beats(beats: list[dict], topic: str = "") -> list[dict] | None:
    config.ensure_dirs()
    _CACHE.mkdir(parents=True, exist_ok=True)
    default_box = _topic_region(topic) or _REGIONS["europe"]

    # probe the dataset once; a hard failure here -> let the caller fall back
    try:
        first_year = next((int(b["year"]) for b in beats if str(b.get("year", "")).lstrip("-").isdigit()), 1900)
        _load(first_year)
    except Exception as exc:  # noqa: BLE001
        print(f"[maps] dataset unreachable ({exc}); pipeline will use slideshow")
        return None

    out: list[dict] = []
    last_box = default_box
    last_sig = None
    repeat = 0
    prev = None   # (gj, highlight) of the previous beat, for the reveal clip
    for i, beat in enumerate(beats):
        try:
            year = int(beat["year"])
        except (KeyError, TypeError, ValueError):
            year = int(_norm(topic).split(" ")[-1]) if _norm(topic).split(" ")[-1].isdigit() else 1900
        try:
            gj = _load(year)
            box = _beat_box(beat, gj, last_box)
            last_box = box
            # consecutive beats on the same snapshot + framing would be an
            # identical still - push in a little each time so there is motion
            sig = (_nearest_year(year), tuple(round(v, 1) for v in box))
            repeat = repeat + 1 if sig == last_sig else 0
            last_sig = sig
            draw_box = _zoom_box(box, 0.12 * repeat) if repeat else box
            highlight = beat.get("highlight", []) or []
            badge = _year_label(_nearest_year(year))
            curr_img, centroid = _compose(gj, highlight, draw_box)

            if prev is not None:
                prev_gj, prev_hl = prev
                prev_img, _ = _compose(prev_gj, prev_hl, draw_box)
                clip_path = _make_reveal_clip(
                    prev_img, curr_img, centroid, badge, config.WORK / f"map_{i:02d}.mp4")
            else:
                clip_path = None

            if clip_path:
                out.append({"kind": "video", "path": clip_path, "hold_last": True})
            else:
                _draw_badge(curr_img, badge)
                dest = config.WORK / f"map_{i:02d}.png"
                curr_img.save(dest, "PNG")
                out.append({"kind": "image", "path": str(dest)})

            prev = (gj, highlight)
            hl = ", ".join(highlight) or "-"
            print(f"[maps] beat {i}: {badge}  highlight={hl}  "
                  f"{'(reveal)' if clip_path else '(still)'}")
        except Exception as exc:  # noqa: BLE001
            print(f"[maps] beat {i} failed ({exc}); reusing previous frame")
            if out:
                out.append(dict(out[-1]))
                prev = None   # don't chain a reveal onto a reused/stale frame
            else:
                return None
    return out
