"""Shared helpers: subprocess wrapper + ffprobe duration."""
from __future__ import annotations

import json
import shlex
import subprocess


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print("[cmd] " + " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"[cmd] failed ({proc.returncode}): {' '.join(cmd[:3])} ...\n"
            f"----- stderr -----\n{proc.stderr[-4000:]}"
        )


def probe_duration(path: str) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"[ffprobe] could not read {path}")
    info = json.loads(proc.stdout)
    if info.get("format", {}).get("duration"):
        return float(info["format"]["duration"])
    for stream in info.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise SystemExit(f"[ffprobe] no duration in {path}")
