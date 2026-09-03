"""Full-frame still cards rendered with Pillow: the host intro / outro and an
optional timeline graphic. All 1080x1920, dark channel theme. Every function
returns a PNG path or None - callers must treat None as "skip this card".
"""
from __future__ import annotations

from . import config

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False

W, H = config.WIDTH, config.HEIGHT
BG = (14, 17, 22)
PANEL = (28, 35, 45)
TEXT = (230, 237, 243)
DIM = (139, 148, 158)
ACCENT = (224, 168, 46)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int):
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _center_text(draw, cx, y, text, font, fill, max_w=None):
    if max_w:
        lines = _wrap(draw, text, font, max_w)
    else:
        lines = [text]
    asc, desc = font.getmetrics()
    lh = asc + desc + 8
    for i, ln in enumerate(lines):
        w = draw.textlength(ln, font=font)
        draw.text((cx - w / 2, y + i * lh), ln, font=font, fill=fill)
    return y + len(lines) * lh


def _circle_portrait(path: str, diam: int):
    im = Image.open(path).convert("RGB")
    im = ImageOps.fit(im, (diam, diam), Image.LANCZOS)
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diam, diam), fill=255)
    im.putalpha(mask)
    return im


def _base():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def _host_card(portrait: str | None, kicker: str, big: str, sub: str, slug: str) -> str | None:
    if not _PIL:
        return None
    config.WORK.mkdir(parents=True, exist_ok=True)
    img, d = _base()
    cy = H // 2

    if portrait:
        try:
            diam = 460
            p = _circle_portrait(portrait, diam)
            d.ellipse((W // 2 - diam // 2 - 8, cy - 640 - 8,
                       W // 2 + diam // 2 + 8, cy - 640 + diam + 8), outline=ACCENT, width=6)
            img.paste(p, (W // 2 - diam // 2, cy - 640), p)
        except Exception as exc:  # noqa: BLE001
            print(f"[cards] portrait paste failed: {exc}")

    y = cy - 110
    if kicker:
        _center_text(d, W // 2, y, kicker.upper(), _font(38), ACCENT)
        y += 70
    y = _center_text(d, W // 2, y, big, _font(96), TEXT, max_w=W - 150)
    if sub:
        _center_text(d, W // 2, y + 24, sub, _font(44), DIM, max_w=W - 200)

    out = config.WORK / f"card_{slug}.png"
    img.save(out)
    return str(out)


def intro_card(portrait: str | None, topic: str) -> str | None:
    return _host_card(portrait, config.CHANNEL_NAME, "A moment in history",
                      topic[:80], "intro")


def outro_card(portrait: str | None) -> str | None:
    return _host_card(portrait, config.CHANNEL_NAME, "Follow for a piece of",
                      "history every day", "outro")


def timeline_card(points: list[dict]) -> str | None:
    if not _PIL or not points:
        return None
    pts = []
    for p in points:
        try:
            yr = int(str(p.get("year")).strip().lstrip("c").strip())
        except (TypeError, ValueError):
            continue
        lbl = str(p.get("label", "")).strip()
        if lbl:
            pts.append((yr, lbl))
    pts = sorted(pts)[:6]
    if len(pts) < 3:
        return None

    config.WORK.mkdir(parents=True, exist_ok=True)
    img, d = _base()
    _center_text(d, W // 2, 150, "TIMELINE", _font(52), ACCENT)

    top, bot = 380, H - 360
    x = 150
    d.line((x, top, x, bot), fill=DIM, width=4)
    n = len(pts)
    for i, (yr, lbl) in enumerate(pts):
        yy = top + (bot - top) * i // max(1, n - 1)
        d.ellipse((x - 14, yy - 14, x + 14, yy + 14), fill=ACCENT)
        ytxt = f"{abs(yr)} BC" if yr < 0 else str(yr)
        d.text((x + 46, yy - 60), ytxt, font=_font(46), fill=ACCENT)
        for j, ln in enumerate(_wrap(d, lbl, _font(40), W - x - 120)):
            d.text((x + 46, yy - 8 + j * 52), ln, font=_font(40), fill=TEXT)

    out = config.WORK / "timeline.png"
    img.save(out)
    return str(out)
