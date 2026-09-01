"""Pick one fresh, view-worthy EVERGREEN topic for today.

Signal sources (all free, no key):
  * Reddit  r/todayilearned, r/science, r/Damnthatsinteresting  (top of day)
  * Google Trends daily RSS   (used only to *boost* facts that overlap a
    currently-trending term, never as the sole topic)
  * A curated evergreen fallback bank so the pipeline can never stall.

Nothing here is case/channel specific beyond the niche = "evergreen facts".
"""
from __future__ import annotations

import html
import random
import re
import time

import feedparser
import requests

from . import config, state

UA = {"User-Agent": "yt-shorts-agent/1.0 (research bot; contact via repo)"}

# Topics we skip - hard to monetise / not "fun fact" material.
_BLOCKLIST = re.compile(
    r"\b(rape|murder|suicide|nazi|hitler|holocaust|genocide|slur|porn|pedophil|"
    r"shooting|massacre|terroris|abortion|trump|biden|election|israel|palestin|"
    r"gaza|ukraine|covid death|died by|killed himself|killed herself)\b",
    re.I,
)

_SUBREDDITS = ["todayilearned", "science", "Damnthatsinteresting", "interestingasfuck"]

_FALLBACK_BANK = [
    "why the Eiffel Tower grows taller in summer",
    "how octopuses have three hearts and blue blood",
    "the only letter not in any US state name",
    "why honey never spoils",
    "how Venus is the hottest planet despite Mercury being closer to the Sun",
    "why the Great Wall of China is not visible from space",
    "how bananas are technically radioactive",
    "why your stomach lining replaces itself every few days",
    "how a day on Venus is longer than its year",
    "why cats can't taste sweetness",
    "how the Sahara desert was green just 6000 years ago",
    "why glass is technically a slow-moving liquid myth explained",
    "how sharks existed before trees",
    "why the shortest war in history lasted 38 minutes",
    "how humans share 60 percent of their DNA with bananas",
    "why hot water can freeze faster than cold water",
    "how there are more stars than grains of sand on Earth",
    "why the human body glows in the dark too faint to see",
    "how Cleopatra lived closer to the Moon landing than to the pyramids",
    "why lightning is five times hotter than the surface of the Sun",
]


def _clean_til(title: str) -> str:
    t = html.unescape(title).strip()
    t = re.sub(r"^TIL[:,]?\s*(that\s+)?", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t


def _reddit_candidates() -> list[dict]:
    out: list[dict] = []
    for sub in _SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=30"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            children = r.json().get("data", {}).get("children", [])
        except Exception as exc:  # noqa: BLE001 - network best effort
            print(f"[trends] reddit r/{sub} failed: {exc}")
            time.sleep(1)
            continue
        for c in children:
            d = c.get("data", {})
            title = d.get("title", "")
            if not title or d.get("over_18"):
                continue
            topic = _clean_til(title) if sub == "todayilearned" else html.unescape(title).strip()
            if not (18 <= len(topic) <= 160):
                continue
            if _BLOCKLIST.search(topic):
                continue
            out.append(
                {
                    "topic": topic,
                    "score": int(d.get("ups", 0)),
                    "source": f"reddit/r/{sub}",
                    "url": "https://reddit.com" + d.get("permalink", ""),
                }
            )
    return out


def _trending_terms() -> list[str]:
    feeds = [
        f"https://trends.google.com/trending/rss?geo={config.GEO}",
        f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={config.GEO}",
    ]
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed, request_headers=UA)
            terms = [e.get("title", "").strip() for e in parsed.entries if e.get("title")]
            if terms:
                return terms
        except Exception as exc:  # noqa: BLE001
            print(f"[trends] google trends feed failed: {exc}")
    return []


def pick_topic() -> dict:
    """Return {topic, source, url, score, boosted}."""
    if config.TOPIC_OVERRIDE:
        return {"topic": config.TOPIC_OVERRIDE, "source": "override", "url": "", "score": 0, "boosted": False}

    candidates = _reddit_candidates()
    fresh = [c for c in candidates if state.is_fresh(c["topic"])]
    print(f"[trends] {len(candidates)} reddit candidates, {len(fresh)} fresh after de-dup")

    if not fresh:
        bank = [t for t in _FALLBACK_BANK if state.is_fresh(t)] or _FALLBACK_BANK
        choice = random.choice(bank)
        return {"topic": choice, "source": "fallback-bank", "url": "", "score": 0, "boosted": False}

    trend_words = {w.lower() for term in _trending_terms() for w in re.findall(r"[a-zA-Z]{4,}", term)}
    for c in fresh:
        overlap = trend_words & set(re.findall(r"[a-zA-Z]{4,}", c["topic"].lower()))
        c["boosted"] = bool(overlap)
        c["rank"] = c["score"] * (1.6 if overlap else 1.0)

    fresh.sort(key=lambda c: c["rank"], reverse=True)
    best = fresh[0]
    print(f"[trends] chosen: {best['topic']!r}  ({best['source']}, score={best['score']}, boosted={best['boosted']})")
    return best
