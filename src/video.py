"""Assemble the final vertical short with ffmpeg.

montage (Ken-Burns image / cropped video segments)
  + burned-in .ass captions
  + voiceover  (+ optional music bed from assets/music/*.mp3)
  -> out/short_YYYYMMDD.mp4

The output duration is clamped to config.TARGET_SECONDS_MIN..MAX:
  * speech shorter than MIN  -> the last frame is held (tpad) and the tail
    padded with silence so the file still reaches MIN,
  * speech longer than MAX   -> only the trailing pad is dropped; narration is
    never cut here (tts.py already trims if it must).
"""
from __future__ import annotations

import datetime as _dt
import glob
import os

from . import config, util

FPS = config.FPS
W, H = config.WIDTH, config.HEIGHT
_GRADE = "eq=contrast=1.05:saturation=1.06:brightness=-0.01,vignette=PI/5"
_TAIL_PAD = 6  # seconds of held last-frame available for the MIN-length pad


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
    out = [max(1.8, d) for d in raw]
    scale = total / sum(out)
    return [round(d * scale, 3) for d in out]


def _make_segment(idx: int, media: dict, dur: float) -> str:
    seg = config.WORK / f"seg{idx:02d}.mp4"
    frames = max(1, int(round(dur * FPS)))
    common = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y", str(seg)]

    if media["kind"] == "image":
        if idx % 2 == 0:
            zexpr = "min(1.001+0.0013*on,1.20)"
        else:
            zexpr = "max(1.20-0.0013*on,1.001)"
        vf = (
            f"scale={int(W*1.2)}:{int(H*1.2)}:force_original_aspect_ratio=increase,"
            f"crop={int(W*1.2)}:{int(H*1.2)},"
            f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},"
            f"setsar=1,fps={FPS},{_GRADE}"
        )
        cmd = ["ffmpeg", "-loop", "1", "-t", f"{dur}", "-i", media["path"],
               "-vf", vf, "-frames:v", str(frames), *common]
    else:
        vf = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"fps={FPS},setsar=1,{_GRADE}"
        )
        cmd = ["ffmpeg", "-stream_loop", "-1", "-t", f"{dur}", "-i", media["path"],
               "-vf", vf, "-frames:v", str(frames), *common]
    util.run(cmd, quiet=True)
    return str(seg)


def _concat(segments: list[str]) -> str:
    listing = config.WORK / "concat.txt"
    listing.write_text(
        "".join(f"file '{os.path.abspath(s)}'\n" for s in segments), encoding="utf-8"
    )
    montage = config.WORK / "montage.mp4"
    util.run(
        ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y", str(montage)],
        quiet=True,
    )
    return str(montage)


def _music_bed() -> str | None:
    for pat in ("*.mp3", "*.m4a", "*.wav"):
        hits = sorted(glob.glob(str(config.ASSETS / "music" / pat)))
        if hits:
            return hits[0]
    return None


def render(media_items: list[dict], beats: list[dict], audio_path: str, ass_path: str) -> str:
    config.ensure_dirs()
    speech = util.probe_duration(audio_path)
    total = _target_total(speech)
    durs = _beat_durations(beats, total)

    segments = [_make_segment(i, m, durs[min(i, len(durs) - 1)]) for i, m in enumerate(media_items)]
    montage = _concat(segments)

    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    final = config.OUT / f"short_{date}.mp4"
    ass_abs = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")

    # Hold the last frame + pad audio with silence, then cut to exactly `total`.
    vfilter = f"[0:v]tpad=stop_mode=clone:stop_duration={_TAIL_PAD},ass='{ass_abs}'[v]"

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
    print(f"[video] rendered {final.name}  ({dur:.1f}s, target {lo}-{hi}s, "
          f"{final.stat().st_size // 1024} KB){flag}")
    return str(final)
