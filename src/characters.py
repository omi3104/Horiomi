"""Two animated cartoon hosts (skeptic + expert) for FORMAT=dialogue.

No GPU, no paid avatar API: one fixed AI-generated (or pinned) portrait per
character, composited into a debate-panel scene that is genuinely animated
every frame - a pulsing "speaking" glow ring, a live audio-style waveform
under whichever host is talking, a subtle breathing scale, and blink-style
dimming on the other host - all driven by the turn timeline, all pure Pillow
math (no facial-landmark guessing, so it can't come out misaligned).

NOTE: this is a speaking-indicator animation, not lip-synced mouth movement -
placing a mouth shape on an AI-generated face reliably needs to know exactly
where the mouth is, which isn't something this pipeline can verify blind.
Once you've seen a real render we can add mouth-flap if the portrait's mouth
position is marked (see README).

Renders straight to an mp4 (no captions/audio yet - video.py composites
those) by piping raw RGB frames into ffmpeg, so nothing touches disk per
frame.
"""
from __future__ import annotations

import math
import subprocess
import urllib.parse

import requests

from . import config, util

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False

W, H = config.WIDTH, config.HEIGHT
UA = {"User-Agent": "yt-shorts-agent/1.1 (history shorts)"}
FPS_ANIM = 15   # rendered at a lower fps; ffmpeg upsamples to config.FPS

BG = (12, 15, 19)
PANEL = (24, 30, 39)
PANEL_ACTIVE = (35, 44, 57)
TEXT = (230, 237, 243)
DIM = (110, 118, 128)
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


def _portrait(role: str) -> str | None:
    """role: 'skeptic' | 'expert'. Pinned art in assets/characters/ wins."""
    pinned = config.ASSETS / "characters" / f"{role}.png"
    if pinned.exists() and pinned.stat().st_size > 8000:
        print(f"[characters] using pinned {pinned}")
        return str(pinned)
    cache = config.WORK / f"char_{role}.png"
    if cache.exists() and cache.stat().st_size > 8000:
        return str(cache)

    config.WORK.mkdir(parents=True, exist_ok=True)
    prompt = config.SKEPTIC_PROMPT if role == "skeptic" else config.EXPERT_PROMPT
    seed = config.SKEPTIC_SEED if role == "skeptic" else config.EXPERT_SEED
    q = urllib.parse.quote(prompt[:280])
    url = (f"https://image.pollinations.ai/prompt/{q}?width=768&height=768"
           f"&nologo=true&seed={seed}&model=flux")
    try:
        r = requests.get(url, headers=UA, timeout=90)
        r.raise_for_status()
        if len(r.content) < 8000:
            raise ValueError("portrait too small")
        cache.write_bytes(r.content)
        print(f"[characters] generated {role} portrait ({len(r.content)//1024} KB)")
        return str(cache)
    except Exception as exc:  # noqa: BLE001
        print(f"[characters] {role} portrait unavailable ({exc})")
        return None


def _circle(path: str, diam: int):
    im = Image.open(path).convert("RGB")
    im = ImageOps.fit(im, (diam, diam), Image.LANCZOS)
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diam, diam), fill=255)
    im.putalpha(mask)
    return im


def _center_text(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


class _Host:
    def __init__(self, role: str, name: str, cx: int):
        self.role = role
        self.name = name
        self.cx = cx           # panel horizontal center
        img = _portrait(role)
        self.avatar = _circle(img, 420) if img else None


def _get_active(turns: list[dict], t: float) -> str | None:
    for turn in turns:
        if turn["start"] - 0.05 <= t < turn["end"] + 0.05:
            return turn["speaker"]
    return None


def _draw_frame(hosts: list[_Host], active: str | None, t: float) -> "Image.Image":
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # sit in the upper-middle of the frame - the lower third is reserved for
    # captions / keyword chyron / progress bar (see video.py)
    panel_w, panel_h = 460, 640
    top = int(H * 0.40 - panel_h / 2)

    for host in hosts:
        on = host.role == active
        x0, x1 = host.cx - panel_w // 2, host.cx + panel_w // 2
        y0, y1 = top, top + panel_h
        pulse = 1.0 + (0.02 * math.sin(t * 5.2) if on else 0.0)
        pw, ph = int(panel_w * pulse), int(panel_h * pulse)
        px0 = host.cx - pw // 2
        py0 = (top + panel_h // 2) - ph // 2

        panel_col = PANEL_ACTIVE if on else PANEL
        d.rounded_rectangle((px0, py0, px0 + pw, py0 + ph), radius=34,
                            fill=panel_col, outline=ACCENT if on else (40, 46, 55),
                            width=6 if on else 2)

        diam = 300
        acx, acy = host.cx, py0 + 170
        if on:
            glow = int(14 + 6 * math.sin(t * 6.4))
            d.ellipse((acx - diam // 2 - glow, acy - diam // 2 - glow,
                       acx + diam // 2 + glow, acy + diam // 2 + glow),
                      outline=ACCENT, width=5)
        if host.avatar is not None:
            av = host.avatar
            if not on:
                # dim the RGB only - alpha_composite-ing a rectangular dark
                # layer would punch a square through the circular mask
                rgb = ImageEnhance.Brightness(av.convert("RGB")).enhance(0.55)
                av = Image.merge("RGBA", (*rgb.split(), av.split()[3]))
            img.paste(av, (acx - diam // 2, acy - diam // 2), av)
        else:
            d.ellipse((acx - diam // 2, acy - diam // 2, acx + diam // 2, acy + diam // 2),
                      fill=(60, 66, 76))

        label = host.name.upper() if host.name.lower() != host.role else \
            ("SKEPTIC" if host.role == "skeptic" else "EXPERT")
        name_y = acy + diam // 2 + 46
        _center_text(d, host.cx, name_y, label, _font(46), TEXT if on else DIM)

        # tiny "speaking" waveform under the name
        if on:
            bars, bw, gap = 7, 10, 8
            base_y = name_y + 100
            total_w = bars * bw + (bars - 1) * gap
            bx0 = host.cx - total_w // 2
            for i in range(bars):
                hgt = int(10 + 34 * abs(math.sin(t * 10 + i * 1.35)))
                bx = bx0 + i * (bw + gap)
                d.rounded_rectangle((bx, base_y - hgt, bx + bw, base_y + hgt),
                                    radius=bw // 2, fill=ACCENT)

    return img


def render(turns: list[dict], total: float) -> str | None:
    """Renders the silent debate-panel animation to work/characters.mp4, or
    returns None if anything is unavailable (caller falls back to slideshow)."""
    if not _PIL:
        print("[characters] Pillow unavailable")
        return None
    hosts = [
        _Host("skeptic", config.SKEPTIC_NAME, W // 4),
        _Host("expert", config.EXPERT_NAME, 3 * W // 4),
    ]
    if all(h.avatar is None for h in hosts):
        print("[characters] no portraits available for either host")
        return None

    out = config.WORK / "characters.mp4"
    n_frames = max(1, int(round(total * FPS_ANIM)))
    cmd = [
        "ffmpeg", "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{W}x{H}", "-framerate", str(FPS_ANIM), "-i", "-",
        "-r", str(config.FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "20", "-y", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for i in range(n_frames):
            t = i / FPS_ANIM
            active = _get_active(turns, t)
            frame = _draw_frame(hosts, active, t)
            proc.stdin.write(frame.tobytes())
    except (BrokenPipeError, OSError) as exc:
        print(f"[characters] frame pipe failed: {exc}")
        proc.kill()
        return None
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
    _, err = proc.communicate(timeout=120)
    if proc.returncode != 0:
        print(f"[characters] ffmpeg encode failed: {err.decode(errors='replace')[-800:]}")
        return None
    print(f"[characters] rendered {out.name} ({n_frames} frames @ {FPS_ANIM}fps -> {config.FPS}fps)")
    return str(out)
