"""Assemble the final vertical short with ffmpeg.

  [host intro card] + Ken-Burns image/video segments (one per beat)
    + [optional timeline card] + [host outro card]
  + burned-in .ass captions  + on-screen keyword overlays
  + voiceover  (+ optional music bed from assets/music/*.mp3)
  -> out/short_YYYYMMDD.mp4

Output duration is clamped to config.TARGET_SECONDS_MIN..MAX. The narration
plays across the whole thing (the hook lands over the intro card, the CTA over
the outro), so the cards do not lengthen the video beyond the window.
"""
from __future__ import annotations

import datetime as _dt
import glob
import os

from . import cards, config, presenter, util

FPS = config.FPS
W, H = config.WIDTH, config.HEIGHT
_GRADE = "eq=contrast=1.05:saturation=1.06:brightness=-0.01,vignette=PI/5"
_TAIL_PAD = 6
_INTRO_S, _OUTRO_S, _TL_S = 2.6, 2.2, 3.2


def _target_total(speech: float) -> float:
    lo, hi = config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX
    total = speech + 0.5
    if total < lo:
        print(f"[video] speech {speech:.1f}s under {lo}s - holding last frame to fill")
        return float(lo)
    if total > hi:
        print(f"[video] speech {speech:.1f}s over {hi}s - keeping speech, dropping tail pad")
        return round(speech + 0.3, 3)
    return round(total, 3)


def _beat_durations(beats: list[dict], total: float) -> list[float]:
    weights = [max(1, len(b["say"].split())) for b in beats]
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    out = [max(1.6, d) for d in raw]
    scale = total / sum(out)
    return [round(d * scale, 3) for d in out]


def _make_segment(idx: int, media: dict, dur: float) -> str:
    seg = config.WORK / f"seg{idx:03d}.mp4"
    frames = max(1, int(round(dur * FPS)))
    common = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y", str(seg)]

    if media["kind"] == "image":
        zexpr = ("min(1.001+0.0013*on,1.20)" if idx % 2 == 0
                 else "max(1.20-0.0013*on,1.001)")
        vf = (
            f"scale={int(W*1.2)}:{int(H*1.2)}:force_original_aspect_ratio=increase,"
            f"crop={int(W*1.2)}:{int(H*1.2)},"
            f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},"
            f"setsar=1,fps={FPS},{_GRADE}"
        )
        cmd = ["ffmpeg", "-loop", "1", "-t", f"{dur}", "-i", media["path"],
               "-vf", vf, "-frames:v", str(frames), *common]
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,{_GRADE}")
        cmd = ["ffmpeg", "-stream_loop", "-1", "-t", f"{dur}", "-i", media["path"],
               "-vf", vf, "-frames:v", str(frames), *common]
    util.run(cmd, quiet=True)
    return str(seg)


def _concat(segments: list[str]) -> str:
    listing = config.WORK / "concat.txt"
    listing.write_text("".join(f"file '{os.path.abspath(s)}'\n" for s in segments),
                       encoding="utf-8")
    montage = config.WORK / "montage.mp4"
    util.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(listing),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y", str(montage)],
             quiet=True)
    return str(montage)


def _music_bed() -> str | None:
    for pat in ("*.mp3", "*.m4a", "*.wav"):
        hits = sorted(glob.glob(str(config.ASSETS / "music" / pat)))
        if hits:
            return hits[0]
    return None


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600):d}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _overlay_ass(beats: list[dict], durs: list[float], offset: float) -> str | None:
    """Burn each beat's keyword as a boxed label in the upper third, timed to
    that beat's on-screen window (shifted by the intro card length)."""
    cues = []
    t = offset
    for b, d in zip(beats, durs):
        kw = (b.get("keyword") or "").strip().replace("{", "(").replace("}", ")")
        if kw and len(kw) >= 2:
            cues.append((t + 0.15, t + d - 0.1, kw.upper()))
        t += d
    if not cues:
        return None
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: KW,DejaVu Sans,64,&H00121212,&H000000FF,&H0033A8E0,&H64000000,-1,0,0,0,"
        "100,100,1,0,3,10,0,8,60,60,300,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for s, e, txt in cues:
        lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},KW,,0,0,0,,"
                     r"{\fad(120,80)}" + txt)
    path = config.WORK / "overlays.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _ass_arg(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/").replace(":", "\\:")


def render(media_items: list[dict], beats: list[dict], audio_path: str, ass_path: str,
           timeline: list[dict] | None = None) -> str:
    config.ensure_dirs()
    speech = util.probe_duration(audio_path)
    total = _target_total(speech)

    # --- host cards + optional timeline card -----------------------------
    intro = outro = tl_card = None
    reserve = 0.0
    portrait = presenter.get_portrait()
    if portrait is not None:
        intro = cards.intro_card(portrait, beats[0].get("keyword") or "")
        outro = cards.outro_card(portrait)
        if intro and outro:
            reserve += _INTRO_S + _OUTRO_S
    if timeline:
        tl_card = cards.timeline_card(timeline)
        if tl_card:
            reserve += _TL_S
    if total - reserve < max(9.0, 1.8 * len(beats)):   # not enough left for beats
        print("[video] not enough room for host/timeline cards - skipping them")
        intro = outro = tl_card = None
        reserve = 0.0

    beat_total = round(total - reserve, 3)
    durs = _beat_durations(beats, beat_total)

    plan: list[tuple[dict, float]] = []
    if intro:
        plan.append(({"kind": "image", "path": intro}, _INTRO_S))
    for j, m in enumerate(media_items):
        plan.append((m, durs[min(j, len(durs) - 1)]))
    if tl_card:
        plan.append(({"kind": "image", "path": tl_card}, _TL_S))
    if outro:
        plan.append(({"kind": "image", "path": outro}, _OUTRO_S))

    segs = [_make_segment(k, m, d) for k, (m, d) in enumerate(plan)]
    montage = _concat(segs)

    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    final = config.OUT / f"short_{date}.mp4"

    ass_chain = f"ass='{_ass_arg(ass_path)}'"
    overlay = _overlay_ass(beats, durs, _INTRO_S if intro else 0.0)
    if overlay:
        ass_chain += f",ass='{_ass_arg(overlay)}'"
    vfilter = f"[0:v]tpad=stop_mode=clone:stop_duration={_TAIL_PAD},{ass_chain}[v]"

    music = _music_bed()
    if music:
        filt = (
            f"{vfilter};"
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
            f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.10[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
            f"[mix]apad[a]"
        )
        cmd = ["ffmpeg", "-i", montage, "-i", audio_path, "-i", music,
               "-filter_complex", filt, "-map", "[v]", "-map", "[a]"]
    else:
        filt = f"{vfilter};[1:a]apad[a]"
        cmd = ["ffmpeg", "-i", montage, "-i", audio_path,
               "-filter_complex", filt, "-map", "[v]", "-map", "[a]"]

    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-r", str(FPS), "-t", f"{total:.3f}",
            "-movflags", "+faststart", "-y", str(final)]
    util.run(cmd)
    dur = util.probe_duration(str(final))
    lo, hi = config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX
    flag = "" if lo - 1 <= dur <= hi + 1 else "  <-- OUT OF RANGE"
    extras = "+".join(x for x, ok in (("intro", intro), ("timeline", tl_card), ("outro", outro)) if ok) or "none"
    print(f"[video] rendered {final.name}  ({dur:.1f}s, target {lo}-{hi}s, "
          f"cards: {extras}, {final.stat().st_size // 1024} KB){flag}")
    return str(final)
