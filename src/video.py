"""Assemble the final vertical short with ffmpeg.

Per beat: a varied Ken-Burns move (zoom in/out, pan L/R/U/D) on the image,
easing in over ~0.2s so every cut reads as an edit. Then:
  + burned-in karaoke captions (each word lights up as spoken)
  + amber keyword chyron per beat  + a thin progress bar
  + [optional timeline card]  + [optional host cards - off by default]
  + voiceover  + a soft generated ambient bed  + a whoosh on each cut
  -> out/short_YYYYMMDD.mp4

Duration is clamped to config.TARGET_SECONDS_MIN..MAX. Image lengths are the
real spoken time of their text so the picture tracks the voice. The polish
filtergraph has a plain-fallback at both the segment and final-assembly step
so a bad filter can never kill the daily run.
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


def _beat_durations(beats: list[dict], total: float, speech: float,
                    hook: str = "", cta: str = "") -> list[float]:
    """Each image runs for the time ITS text is actually spoken. The hook (no
    image of its own) folds into image 1, the cta and the trailing hold into the
    last image - so images 2..N-1 start exactly on their narration, no drift."""
    units = [hook] + [b.get("say", "") for b in beats] + [cta]
    w = [max(1, len(u.split())) for u in units]
    wsum = sum(w) or 1
    per = [speech * x / wsum for x in w]          # real spoken seconds per unit
    bd = per[1:-1] or [speech]
    bd[0] += per[0]                                # hook -> first image
    bd[-1] += per[-1] + max(0.0, total - speech)   # cta + tail hold -> last image
    return [round(max(1.2, d), 3) for d in bd]


_FADE_IN = 0.22       # each image eases in - makes every cut read as an edit


def _kenburns(idx: int, frames: int) -> str:
    """One of six motions (zoom in/out, pan L/R/U/D) rotated by segment index."""
    n = frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    mode = idx % 6
    if mode == 0:
        return f"z='min(1.0015+0.0011*on,1.18)':x='{cx}':y='{cy}'"
    if mode == 1:
        return f"z='if(lte(on,1),1.18,max(1.18-0.0011*on,1.0015))':x='{cx}':y='{cy}'"
    if mode == 2:   # pan right
        return f"z='1.14':x='(iw-iw/zoom)*on/{n}':y='(ih-ih/zoom)/2'"
    if mode == 3:   # pan left
        return f"z='1.14':x='(iw-iw/zoom)*(1-on/{n})':y='(ih-ih/zoom)/2'"
    if mode == 4:   # pan down
        return f"z='1.14':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*on/{n}'"
    return f"z='1.14':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*(1-on/{n})'"   # pan up


def _make_segment(idx: int, media: dict, dur: float) -> str:
    seg = config.WORK / f"seg{idx:03d}.mp4"
    frames = max(1, int(round(dur * FPS)))
    fin = f",fade=t=in:st=0:d={_FADE_IN}" if idx > 0 else ""
    common = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y", str(seg)]
    is_img = media["kind"] == "image"

    if is_img:
        big = (f"scale={int(W*1.35)}:{int(H*1.35)}:force_original_aspect_ratio=increase,"
               f"crop={int(W*1.35)}:{int(H*1.35)}")
        fancy = (f"{big},zoompan={_kenburns(idx, frames)}:d=1:s={W}x{H},setsar=1,fps={FPS},{_GRADE}{fin}")
        plain = (f"{big},zoompan=z='min(1.001+0.0012*on,1.18)':d=1:x='iw/2-(iw/zoom/2)':"
                 f"y='ih/2-(ih/zoom/2)':s={W}x{H},setsar=1,fps={FPS},{_GRADE}")
        base = ["ffmpeg", "-loop", "1", "-t", f"{dur}", "-i", media["path"]]
    else:
        v = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,{_GRADE}"
        fancy, plain = v + fin, v
        base = ["ffmpeg", "-stream_loop", "-1", "-t", f"{dur}", "-i", media["path"]]

    try:
        util.run([*base, "-vf", fancy, "-frames:v", str(frames), *common], quiet=True)
    except SystemExit:
        print(f"[video] segment {idx}: motion filter failed - plain fallback")
        util.run([*base, "-vf", plain, "-frames:v", str(frames), *common], quiet=True)
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


def _ambient_bed(total: float) -> str | None:
    """A soft, serious cinematic drone generated on the fly (no asset needed)."""
    out = config.WORK / "bed.m4a"
    d = f"{total + 2:.1f}"
    freqs = (98.0, 146.83, 196.0, 293.66)          # low G/D open drone
    ins = []
    for f in freqs:
        ins += ["-f", "lavfi", "-t", d, "-i", f"sine=frequency={f}"]
    filt = (
        f"{''.join(f'[{i}:a]' for i in range(len(freqs)))}amix=inputs={len(freqs)}:normalize=0,"
        "tremolo=f=0.2:d=0.35,lowpass=f=520,highpass=f=55,"
        "aecho=0.8:0.6:120|240:0.35|0.2,"
        f"afade=t=in:st=0:d=3,afade=t=out:st={max(0.1, total - 3):.1f}:d=3,volume=0.30[a]"
    )
    try:
        util.run(["ffmpeg", *ins, "-filter_complex", filt, "-map", "[a]",
                  "-c:a", "aac", "-b:a", "128k", "-y", str(out)], quiet=True)
        return str(out)
    except SystemExit as exc:
        print(f"[video] ambient bed failed ({exc}); no music")
        return None


def _music_bed(total: float) -> str | None:
    for pat in ("*.mp3", "*.m4a", "*.wav", "*.ogg"):
        hits = sorted(glob.glob(str(config.ASSETS / "music" / pat)))
        if hits:
            return hits[0]
    if config.MUSIC == "ambient":
        return _ambient_bed(total)
    return None


def _sfx_track(cut_times: list[float], total: float) -> str | None:
    """One short whoosh placed at every image cut, pre-mixed to its own track."""
    if not cut_times:
        return None
    whoosh = config.WORK / "whoosh.wav"
    sfx = config.WORK / "sfx.m4a"
    try:
        util.run([
            "ffmpeg", "-f", "lavfi", "-t", "0.5", "-i", "anoisesrc=color=pink:amplitude=0.6",
            "-af", ("volume='if(lt(t,0.04),t/0.04,max(0,1-(t-0.04)/0.42))':eval=frame,"
                    "lowpass=f=1900,highpass=f=230,volume=0.55"),
            "-y", str(whoosh),
        ], quiet=True)
        ins = []
        parts = []
        for i, ct in enumerate(cut_times):
            ins += ["-i", str(whoosh)]
            ms = max(0, int(ct * 1000))
            parts.append(f"[{i}:a]adelay={ms}|{ms}[s{i}]")
        parts.append("".join(f"[s{i}]" for i in range(len(cut_times)))
                     + f"amix=inputs={len(cut_times)}:normalize=0:dropout_transition=0,"
                       f"apad,atrim=0:{total:.3f}[a]")
        util.run(["ffmpeg", *ins, "-filter_complex", ";".join(parts), "-map", "[a]",
                  "-c:a", "aac", "-b:a", "128k", "-y", str(sfx)], quiet=True)
        return str(sfx)
    except SystemExit as exc:
        print(f"[video] sfx track failed ({exc}); no whoosh")
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
           timeline: list[dict] | None = None, hook: str = "", cta: str = "") -> str:
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
    beat_speech = max(1.0, speech - reserve)
    durs = _beat_durations(beats, beat_total, beat_speech, hook, cta)

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

    # image-cut timestamps (skip t=0 and the intro card if any)
    cut_times: list[float] = []
    acc = 0.0
    for k, (_m, d) in enumerate(plan):
        if 0 < k < len(plan):
            cut_times.append(round(acc, 3))
        acc += d
    cut_times = [t for t in cut_times if 0.3 < t < total - 0.3]

    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    final = config.OUT / f"short_{date}.mp4"

    ass_chain = f"ass='{_ass_arg(ass_path)}'"
    overlay = _overlay_ass(beats, durs, _INTRO_S if intro else 0.0)
    if overlay:
        ass_chain += f",ass='{_ass_arg(overlay)}'"
    pbar = (f",drawbox=x=0:y=ih-8:w='iw*t/{total:.3f}':h=8:color=0xE0A82E@0.9:thickness=fill"
            if config.PROGRESS_BAR else "")
    vfilter = f"[0:v]tpad=stop_mode=clone:stop_duration={_TAIL_PAD},{ass_chain}{pbar}[v]"

    # --- audio: voice (+ ambient bed) (+ whoosh sfx) --------------------
    music = _music_bed(total)
    sfx = _sfx_track(cut_times, total) if config.SFX else None
    inputs = ["-i", montage, "-i", audio_path]
    a_src = ["[1:a]aformat=sample_rates=48000:channel_layouts=stereo[voice]"]
    mix_labels = ["[voice]"]
    idx = 2
    if music:
        inputs += ["-i", music]
        a_src.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume={config.MUSIC_VOLUME}[music]")
        mix_labels.append("[music]"); idx += 1
    if sfx:
        inputs += ["-i", sfx]
        a_src.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.85[sfx]")
        mix_labels.append("[sfx]"); idx += 1
    if len(mix_labels) > 1:
        a_src.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
                     f"duration=longest:normalize=0:dropout_transition=0,apad[a]")
    else:
        a_src = ["[1:a]apad[a]"]

    filt = f"{vfilter};" + ";".join(a_src)
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-r", str(FPS), "-t", f"{total:.3f}",
           "-movflags", "+faststart", "-y", str(final)]
    try:
        util.run(["ffmpeg", *inputs, "-filter_complex", filt,
                  "-map", "[v]", "-map", "[a]", *enc])
    except SystemExit:
        print("[video] full filtergraph failed - assembling montage + voice only")
        safe = (f"[0:v]tpad=stop_mode=clone:stop_duration={_TAIL_PAD},"
                f"ass='{_ass_arg(ass_path)}'[v];[1:a]apad[a]")
        util.run(["ffmpeg", "-i", montage, "-i", audio_path, "-filter_complex", safe,
                  "-map", "[v]", "-map", "[a]", *enc])
        music = sfx = None

    dur = util.probe_duration(str(final))
    lo, hi = config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX
    flag = "" if lo - 1 <= dur <= hi + 1 else "  <-- OUT OF RANGE"
    fx = "+".join(x for x, ok in (("music", music), ("sfx", sfx),
                                  ("bar", config.PROGRESS_BAR)) if ok) or "plain"
    print(f"[video] rendered {final.name}  ({dur:.1f}s, target {lo}-{hi}s, "
          f"fx: {fx}, {final.stat().st_size // 1024} KB){flag}")
    return str(final)
