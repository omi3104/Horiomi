"""Turn a topic string into a structured short-video script.

Writer priority (first that works wins):
  1. Groq API                - only if GROQ_API_KEY is set (fast, reliable free tier)
  2. Gemini API              - only if GEMINI_API_KEY is set
  3. Pollinations text API   - keyless, free (often paywalled/renamed now)
  4. Wikipedia-backed template - keyless last resort so the daily job never dies

The narration is sized to roughly config.TARGET_SECONDS of speech so the
finished short lands inside the 50-80s window (tts.py + video.py enforce the
hard bounds).

Returns:
  { topic, title, hook, cta, description, tags[], hashtags[],
    beats: [ {say, visual}, ... ], narration, word_count, target_seconds }
"""
from __future__ import annotations

import json
import re
import textwrap
import time
import urllib.parse

import requests

from . import config

UA = {"User-Agent": "yt-shorts-agent/1.0 (+github actions)"}

# Words needed to fill the target speech time, plus a comfortable spread.
_TARGET_WORDS = round(config.TARGET_SECONDS * config.SPEAKING_WPS)
_WORDS_LO = round(config.TARGET_SECONDS_MIN * config.SPEAKING_WPS) + 10
_WORDS_HI = round(config.TARGET_SECONDS_MAX * config.SPEAKING_WPS) - 10
_MIN_BEATS = 6
_MAX_BEATS = 10

_SCHEMA_HINT = (
    '{"title": str<=90, "hook": str, '
    f'"beats": [{{"say": str, "visual": str}}] ({_MIN_BEATS}-{_MAX_BEATS} items), '
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
    - Narration = hook + every beat + cta, {words_lo} to {words_hi} words TOTAL
      (aim for {words_target}). This must fill about {seconds} seconds of speech,
      so do NOT stop early - keep adding real detail, context and examples.
    - {beats_lo} to {beats_hi} beats. Each beat = one or two sentences plus a
      concrete visual search phrase using real nouns a stock library would have
      ("humpback whale underwater", "aurora over snow", not "wonder" or
      "mystery").
    - Title <= 90 chars, curiosity-driven, no ALL CAPS, no false promise.

    Output ONLY minified JSON, no code fences, matching:
    {schema}
    """
)


def _prompt_for(topic: str) -> str:
    return _PROMPT.format(
        topic=topic,
        schema=_SCHEMA_HINT,
        words_lo=_WORDS_LO,
        words_hi=_WORDS_HI,
        words_target=_TARGET_WORDS,
        seconds=config.TARGET_SECONDS,
        beats_lo=_MIN_BEATS,
        beats_hi=_MAX_BEATS,
    )


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1:
        text = text[s : e + 1]
    return json.loads(text)


_GROQ_URL = "https://api.groq.com/openai/v1"
# Free tier is 8000 tokens/minute TOTAL (prompt + completion), so max_tokens
# must stay well under that or the request 413s outright.
_GROQ_MAX_TOKENS = 4500
# Fallback ids behind live discovery. gpt-oss first - it's what free Groq keys
# actually carry right now.
_GROQ_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
)
_GROQ_SKIP = ("whisper", "tts", "guard", "embed", "distil", "allam", "vision",
              "compound", "prompt-guard", "safety", "orpheus", "canopylabs",
              "playai", "sonar", "-stt", "-asr")
_GROQ_REASONING = ("gpt-oss", "deepseek-r1", "qwen3", "-r1", "reasoning", "thinking", "o1", "o3", "o4")


def _groq_is_reasoning(model: str) -> bool:
    m = model.lower()
    return any(h in m for h in _GROQ_REASONING)


def _groq_models() -> list[str]:
    """Live model ids for this key, non-reasoning first, then the hardcoded rest."""
    live: list[str] = []
    try:
        r = requests.get(f"{_GROQ_URL}/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}", **UA}, timeout=30)
        if r.ok:
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            chat = [i for i in ids if i and not any(b in i.lower() for b in _GROQ_SKIP)]
            live = [i for i in chat if not _groq_is_reasoning(i)] + [i for i in chat if _groq_is_reasoning(i)]
            print(f"[script] groq available: {live[:8]}")
        else:
            print(f"[script] groq list-models HTTP {r.status_code} {r.text[:160]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"[script] groq list-models failed: {exc}")
    seen: set[str] = set()
    order: list[str] = []
    for m in live + list(_GROQ_MODELS):
        if m and m not in seen:
            seen.add(m)
            order.append(m)
    return order


def _groq_body(model: str, prompt: str, effort: str | None) -> dict:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "You output only minified JSON matching the schema in the user "
                "message. No prose, no markdown, no code fences.")},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": _GROQ_MAX_TOKENS,
    }
    if effort is not None:
        body["reasoning_effort"] = effort
        body["reasoning_format"] = "hidden"
    return body


def _via_groq(topic: str) -> dict | None:
    if not config.GROQ_API_KEY:
        return None
    prompt = _prompt_for(topic)
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", **UA}
    for model in _groq_models()[:5]:
        # reasoning models: some want "low", some only accept "none"/"default";
        # fall through the options on the specific 400 that complains.
        efforts: list[str | None] = ["low", "none", None] if _groq_is_reasoning(model) else [None]
        for effort in efforts:
            try:
                r = requests.post(f"{_GROQ_URL}/chat/completions", headers=headers,
                                  json=_groq_body(model, prompt, effort), timeout=90)
            except Exception as exc:  # noqa: BLE001
                print(f"[script] groq({model}) failed: {exc}")
                break
            if r.status_code == 400 and "reasoning_effort" in r.text:
                continue  # try the next effort value for this model
            if r.status_code == 429:
                print(f"[script] groq({model}) 429 rate-limited; waiting 20s")
                time.sleep(20)
            if not r.ok:
                print(f"[script] groq({model}) HTTP {r.status_code} {r.text[:180]!r}")
                break  # 404 / 413 / decommissioned -> next model
            msg = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
            if not msg.strip():
                print(f"[script] groq({model}) empty content")
                break
            out = _extract_json(msg)
            n = len(out.get("beats") or []) if isinstance(out, dict) else 0
            if n < 3:
                print(f"[script] groq({model}) only {n} beats (truncated?); next model")
                break
            print(f"[script] groq ok via {model} (effort={effort}, {n} beats)")
            return out
        time.sleep(1)
    return None


def _via_pollinations(topic: str) -> dict | None:
    # The legacy text API is now paywalled (402) for everyone; kept as one
    # quick attempt in case that changes, but no longer worth a retry loop.
    try:
        r = requests.post(
            "https://text.pollinations.ai/", headers=UA, timeout=45,
            json={
                "messages": [{"role": "user", "content": _prompt_for(topic)}],
                "jsonMode": True, "private": True, "referrer": "yt-shorts-agent",
            },
        )
        if not r.ok:
            print(f"[script] pollinations HTTP {r.status_code} {r.text[:160]!r}")
            return None
        return _extract_json(r.text)
    except Exception as exc:  # noqa: BLE001
        print(f"[script] pollinations failed: {exc}")
        return None


_GEMINI_HOST = "https://generativelanguage.googleapis.com"
# Concrete current ids first (the "-latest" aliases have become unreliable and
# the models endpoint hands back ids that then 404 for new keys). Update the
# leading id when Google's error text names a newer one.
_GEMINI_MODELS = (
    "gemini-3.6-flash", "gemini-flash-latest", "gemini-3-flash",
    "gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.6-pro",
)


def _gemini_discover_model(api_version: str) -> str | None:
    """Ask the API which models are actually available to this key."""
    try:
        r = requests.get(f"{_GEMINI_HOST}/{api_version}/models",
                         params={"key": config.GEMINI_API_KEY}, headers=UA, timeout=30)
        if not r.ok:
            print(f"[script] gemini list-models HTTP {r.status_code} {r.text[:160]!r}")
            return None
        models = r.json().get("models", [])
        usable = [
            m["name"].split("/")[-1] for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        # Prefer a flash model, else the first that works.
        pick = next((n for n in usable if "flash" in n and "vision" not in n), None) or (usable[0] if usable else None)
        if pick:
            print(f"[script] gemini discovered model: {pick}")
        return pick
    except Exception as exc:  # noqa: BLE001
        print(f"[script] gemini list-models failed: {exc}")
        return None


def _gemini_call(api_version: str, model: str, prompt: str) -> dict | None:
    r = requests.post(
        f"{_GEMINI_HOST}/{api_version}/models/{model}:generateContent",
        params={"key": config.GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
        },
        timeout=60,
    )
    if not r.ok:
        print(f"[script] gemini({api_version}/{model}) HTTP {r.status_code} {r.text[:200]!r}")
        return None
    cands = r.json().get("candidates") or []
    if not cands:
        print(f"[script] gemini({model}) no candidates {r.text[:200]!r}")
        return None
    parts = cands[0].get("content", {}).get("parts", [])
    return _extract_json("".join(p.get("text", "") for p in parts))


def _via_gemini(topic: str) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None
    prompt = _prompt_for(topic)
    for api_version in ("v1beta", "v1"):
        # hardcoded current ids first; discovery only appended (it returns
        # retired ids that 404 for new keys).
        tried: list[str] = list(_GEMINI_MODELS)
        discovered = _gemini_discover_model(api_version)
        if discovered and discovered not in tried:
            tried.append(discovered)
        for model in tried:
            try:
                out = _gemini_call(api_version, model, prompt)
                if out:
                    print(f"[script] gemini ok via {api_version}/{model}")
                    return out
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


# Filler used only by the last-resort template, kept plain and non-spammy. It
# only runs when both model APIs are down, and exists so the day still ships.
_TEMPLATE_FILLER = [
    "The story behind {topic} is stranger than the version most people repeat.",
    "The common explanation sounds right, but the measurements tell a different tale.",
    "Researchers pinned it down by testing the obvious answer and watching it fail.",
    "It comes down to simple physics, chemistry and a lot of time.",
    "The effect is small on any single day, yet it adds up in a way you can measure.",
    "Once someone points it out, you start noticing it almost everywhere.",
    "This one fact quietly connects to a much bigger picture in science and history.",
    "It is the kind of detail that changes how you look at something ordinary.",
    "The records are consistent, checked and re-checked over many years.",
    "Understanding why it happens is more satisfying than the myth ever was.",
]


def _template(topic: str) -> dict:
    extract = _wikipedia_summary(topic)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", extract) if len(s.strip()) > 20]
    sentences = sentences[:_MAX_BEATS]

    seed = [
        f"Here is something surprising about {topic}.",
        f"Here is what actually makes {topic} worth knowing.",
    ]
    body = sentences or seed
    i = 0
    # Fill towards the top of the beat range so the narration is long enough
    # for the 50-80s window even with no model help.
    while len(body) < _MAX_BEATS - 1:
        body.append(_TEMPLATE_FILLER[i % len(_TEMPLATE_FILLER)].format(topic=topic))
        i += 1
    body = body[:_MAX_BEATS]

    beats = [{"say": s, "visual": topic} for s in body]
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
        "target_seconds": config.TARGET_SECONDS,
    }


def build(topic: str) -> dict:
    # Each provider self-skips if its key is unset, so this is just priority
    # order: Groq -> Gemini -> Pollinations -> Wikipedia template.
    raw: dict | None = None
    source = "template"
    for name, fn in (("groq", _via_groq), ("gemini", _via_gemini), ("pollinations", _via_pollinations)):
        raw = fn(topic)
        if raw:
            source = name
            break

    script: dict | None = None
    if raw:
        try:
            script = _normalise(topic, raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[script] {source} output rejected ({exc}); using template")
            raw = None
    if not raw:
        script = _normalise(topic, _template(topic))
        source = "template"

    assert script is not None
    if script["word_count"] < _WORDS_LO * 0.7:
        print(f"[script] WARNING: only {script['word_count']} words "
              f"(< {_WORDS_LO}); tts.py slows down, video.py pads to "
              f"{config.TARGET_SECONDS_MIN}s")
    print(
        f"[script] via {source}: {script['title']!r} "
        f"({script['word_count']} words ~ target {_TARGET_WORDS}, "
        f"{len(script['beats'])} beats)"
    )
    return script
