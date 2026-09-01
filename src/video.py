"""Assemble the final vertical short with ffmpeg.

montage (Ken-Burns image / cropped video segments)
  + burned-in .ass captions
  + voiceover  (+ optional music bed from assets/music/*.mp3)
  -> out/short_YYYYMMDD.mp4
"""
from __future__ import annotations

import datetime as _dt
import glob
import os

from . import config, util

FPS = config.FPS
W, H = config.WIDTH, config.HEIGHT
_GRADE = "eq=contrast=1.05:saturation=1.06:brightness=-0.01,vignette=PI/5"


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
    total = speech + 0.6
    durs = _beat_durations(beats, total)

    segments = [_make_segment(i, m, durs[min(i, len(durs) - 1)]) for i, m in enumerate(media_items)]
    montage = _concat(segments)

    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    final = config.OUT / f"short_{date}.mp4"
    ass_abs = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")

    music = _music_bed()
    if music:
        filt = (
            f"[0:v]ass='{ass_abs}'[v];"
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
            f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.10[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        cmd = ["ffmpeg", "-i", montage, "-i", audio_path, "-i", music,
               "-filter_complex", filt, "-map", "[v]", "-map", "[a]"]
    else:
        filt = f"[0:v]ass='{ass_abs}'[v]"
        cmd = ["ffmpeg", "-i", montage, "-i", audio_path,
               "-filter_complex", filt, "-map", "[v]", "-map", "1:a"]

    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-r", str(FPS), "-shortest", "-movflags", "+faststart",
            "-y", str(final)]
    util.run(cmd)
    dur = util.probe_duration(str(final))
    print(f"[video] rendered {final.name}  ({dur:.1f}s, {final.stat().st_size // 1024} KB)")
    return str(final)
