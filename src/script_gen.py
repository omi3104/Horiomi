"""Turn a topic string into a structured short-video script.

Writer priority (first that works wins):
  1. Pollinations text API   - keyless, free
  2. Gemini API              - only if GEMINI_API_KEY is set (optional upgrade)
  3. Wikipedia-backed template - keyless last resort so the daily job never dies

Returns:
  { title, hook, cta, description, tags[], hashtags[],
    beats: [ {say, visual}, ... ], narration, word_count }
"""
from __future__ import annotations

import json
import re
import textwrap
import urllib.parse

import requests

from . import config

UA = {"User-Agent": "yt-shorts-agent/1.0 (+github actions)"}

_SCHEMA_HINT = (
    '{"title": str<=90, "hook": str, '
    '"beats": [{"say": str, "visual": str}] (5-7 items), '
    '"cta": str, "description": str, "tags": [str], "hashtags": [str]}'
)

_PROMPT = textwrap.dedent(
    """\
    You write scripts for a faceless YouTube Shorts channel about surprising
    but TRUE facts (science, history, nature, space, psychology).

    Topic: "{topic}"

    Rules:
    - Factually accurate. If the topic is a common myth, correct it and make
      the correction the payoff. Never invent statistics.
    - Spoken style: short punchy 2nd-person sentences. No markdown, no emojis,
      no "in this video", no stage directions.
    - Narration (hook + all beats) = 95 to 135 words total.
    - 5 to 7 beats. Each beat = ONE sentence + a concrete visual search phrase
      using real nouns a stock library would have ("humpback whale underwater",
      not "wonder" or "mystery").
    - Title <= 90 chars, curiosity-driven, no ALL CAPS, no false promise.

    Output ONLY minified JSON, no code fences, matching:
    {schema}
    """
)


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1:
        text = text[s : e + 1]
    return json.loads(text)


def _via_pollinations(topic: str) -> dict | None:
    prompt = _PROMPT.format(topic=topic, schema=_SCHEMA_HINT)
    for model in ("openai", "mistral", "openai-large"):
        try:
            r = requests.post(
                "https://text.pollinations.ai/",
                headers=UA,
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "model": model,
                    "jsonMode": True,
                    "private": True,
                    "referrer": "yt-shorts-agent",
                },
                timeout=90,
            )
            if not r.ok:
                print(f"[script] pollinations({model}) HTTP {r.status_code}")
                continue
            return _extract_json(r.text)
        except Exception as exc:  # noqa: BLE001
            print(f"[script] pollinations({model}) failed: {exc}")
    return None


def _via_gemini(topic: str) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None
    prompt = _PROMPT.format(topic=topic, schema=_SCHEMA_HINT)
    for model in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash"):
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": config.GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
                },
                timeout=60,
            )
            if not r.ok:
                print(f"[script] gemini({model}) HTTP {r.status_code}")
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            return _extract_json("".join(p.get("text", "") for p in parts))
        except Exception as exc:  # noqa: BLE001
            print(f"[script] gemini({model}) failed: {exc}")
    return None


def _wikipedia_summary(topic: str) -> str:
    """Grab a plain-language extract to seed the template fallback."""
    try:
        s = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": topic, "format": "json", "srlimit": 1},
            headers=UA, timeout=20,
        ).json()
        title = s["query"]["search"][0]["title"]
        page = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}",
            headers=UA, timeout=20,
        ).json()
        return page.get("extract", "") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[script] wikipedia fallback failed: {exc}")
        return ""


def _template(topic: str) -> dict:
    extract = _wikipedia_summary(topic)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", extract) if len(s.strip()) > 25][:5]
    if len(sentences) < 3:
        sentences = [
            f"Here is something surprising about {topic}.",
            "Most people get this completely wrong.",
            "The real explanation is stranger than the myth.",
            "Once you know it, you cannot unsee it.",
        ]
    beats = [{"say": s, "visual": topic} for s in sentences]
    hook = f"Did you know this about {topic}?"
    return {
        "title": f"The truth about {topic}"[:90],
        "hook": hook,
        "beats": beats,
        "cta": "Follow for a new fact every day.",
        "description": f"A quick fact about {topic}.",
        "tags": ["facts", "did you know", "shorts", "educational", "science"],
        "hashtags": ["#shorts", "#facts", "#didyouknow"],
    }


def _normalise(topic: str, data: dict) -> dict:
    beats: list[dict] = []
    for b in data.get("beats", []):
        say = str(b.get("say", "")).strip()
        visual = str(b.get("visual", "")).strip() or topic
        if say:
            beats.append({"say": say, "visual": visual})
    if len(beats) < 3:
        raise ValueError("script has too few usable beats")

    hook = str(data.get("hook", "")).strip() or beats[0]["say"]
    cta = str(data.get("cta", "")).strip() or "Follow for a new fact every day."
    narration = re.sub(r"\s+", " ", " ".join([hook] + [b["say"] for b in beats] + [cta])).strip()

    tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()][:15] or [
        "facts", "did you know", "shorts", "educational",
    ]
    hashtags = [h if str(h).startswith("#") else f"#{h}" for h in data.get("hashtags", [])]
    for default in ("#shorts", "#facts", "#didyouknow"):
        if default not in [h.lower() for h in hashtags]:
            hashtags.append(default)
    hashtags = hashtags[:8]

    description = str(data.get("description", "")).strip()
    description = f"{description}\n\n{cta}\n{' '.join(hashtags)}".strip()

    return {
        "topic": topic,
        "title": (str(data.get("title", "")).strip() or topic)[:100],
        "hook": hook,
        "beats": beats,
        "cta": cta,
        "narration": narration,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "word_count": len(narration.split()),
    }


def build(topic: str) -> dict:
    raw = _via_pollinations(topic) or _via_gemini(topic)
    source = "pollinations/gemini"
    if raw:
        try:
            script = _normalise(topic, raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[script] model output rejected ({exc}); using template")
            raw = None
    if not raw:
        script = _normalise(topic, _template(topic))
        source = "template"
    print(
        f"[script] via {source}: {script['title']!r} "
        f"({script['word_count']} words, {len(script['beats'])} beats)"
    )
    return script
