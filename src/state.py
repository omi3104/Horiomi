"""Tiny persisted state, committed back by the GitHub Action after each run.

  data/used_topics.json  - rolling topic history so the channel never repeats
  data/last_run.json     - a snapshot of the most recent build for the PWA
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import config

HISTORY_PATH = config.DATA / "used_topics.json"
LAST_RUN_PATH = config.DATA / "last_run.json"
MAX_HISTORY = 400


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def load() -> list[dict]:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def used_norm_topics() -> set[str]:
    return {_norm(item.get("topic", "")) for item in load() if item.get("topic")}


def is_fresh(topic: str) -> bool:
    n = _norm(topic)
    if len(n) < 8:
        return False
    return n not in used_norm_topics()


def record(topic: str, title: str, video_id: str | None) -> None:
    config.ensure_dirs()
    history = load()
    history.append(
        {
            "topic": topic,
            "title": title,
            "video_id": video_id,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
    )
    history = history[-MAX_HISTORY:]
    HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def write_last_run(picked: dict, script: dict, result: dict) -> None:
    """Snapshot the latest build so the iPhone PWA can show it for review."""
    config.ensure_dirs()
    now = datetime.now(timezone.utc)
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "topic": script.get("topic") or picked.get("topic"),
        "topic_source": picked.get("source"),
        "topic_url": picked.get("url"),
        "title": script.get("title"),
        "hook": script.get("hook"),
        "description": script.get("description"),
        "tags": script.get("tags", []),
        "hashtags": script.get("hashtags", []),
        "beats": script.get("beats", []),
        "word_count": script.get("word_count"),
        "duration_seconds": script.get("duration_seconds"),
        "target_window": [config.TARGET_SECONDS_MIN, config.TARGET_SECONDS_MAX],
        "privacy": config.PRIVACY,
        "dry_run": config.DRY_RUN,
        "video_id": result.get("video_id"),
        "youtube_url": result.get("url"),
        "studio_url": result.get("studio_url"),
        "uploaded_at": result.get("uploaded_at"),
    }
    LAST_RUN_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
