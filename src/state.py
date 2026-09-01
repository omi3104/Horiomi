"""Tiny persisted history so the channel never repeats a topic.

Stored as data/used_topics.json and committed back by the GitHub Action after
every successful run.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import config

HISTORY_PATH = config.DATA / "used_topics.json"
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


def recent_norm_topics(days_back: int | None = None) -> set[str]:
    return {_norm(item.get("topic", "")) for item in load() if item.get("topic")}


def is_fresh(topic: str) -> bool:
    n = _norm(topic)
    if len(n) < 8:
        return False
    return n not in recent_norm_topics()


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
