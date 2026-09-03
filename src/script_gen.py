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
_MIN_BEATS = 8
_MAX_BEATS = 14

_SCHEMA_HINT = (
    '{"title": str<=70, "hook": str, '
    f'"beats": [{{"say": str, "visual": str, "keyword": str}}] ({_MIN_BEATS}-{_MAX_BEATS} items), '
    '"timeline": [{"year": int, "label": str}] (3-6 items, or []), '
    '"cta": str, "description": str, "tags": [str], "hashtags": [str]}'
)

_PROMPT = textwrap.dedent(
    """\
    You write scripts for a faceless YouTube Shorts channel about HISTORY and
    historical geopolitics: ancient and medieval Greece, Rome, Persia, Egypt,
    Byzantium, the Islamic world, the Normans, the Mongols, the Ottomans, the
    British Empire, the World Wars and the Cold War treated as history. NOT
    current partisan politics.

    Topic: "{topic}"

    Rules:
    - Pick ONE surprising, lesser-known angle on the topic - not a textbook
      overview. Lead with the twist.
    - Historically accurate. Use real dates, names and places. If the popular
      version is a myth, correct it and make the correction the payoff. Never
      invent numbers.
    - Tone: dramatic and vivid, like a storyteller who was there. Short punchy
      sentences, mostly 2nd/3rd person. No markdown, no emojis, no "in this
      video", no stage directions, no "let that sink in".
    - HARD REQUIREMENT: narration (hook + every beat + cta) is {words_lo} to
      {words_hi} words TOTAL, aim for {words_target}. This fills about {seconds}
      seconds of speech - a shorter script makes the voice drag. Count as you go.
    - {beats_lo} to {beats_hi} beats. Each beat is ONE spoken sentence of about
      13 to 20 words (vivid, with a concrete detail) - so the picture changes
      often but the narration still reaches the word target. For each beat give:
        * "visual": a concrete image search phrase of real historical nouns
          ("Mongol cavalry siege of Baghdad", "Hagia Sophia interior dome",
          "Mughal miniature painting Akbar court", "map of the Abbasid
          Caliphate") - never abstractions like "power" or "glory". Use a
          "map of ..." phrase for 1-2 beats where geography matters.
        * "keyword": the single most important date, name or place in that
          beat, 1-3 words, shown as an on-screen caption ("1258", "Hulagu
          Khan", "Ain Jalut").
    - "timeline": 3-6 {{year, label}} points if the topic has a clear
      chronology (year is an integer, negative for BC), otherwise [].
    - Title <= 70 chars, a curiosity gap, front-load the strongest keyword, no
      ALL CAPS, no clickbait lie. Plain hyphens only, no fancy dashes.
    - SEO: work ONE of these real YouTube search phrases naturally into the
      title or the first line of the description if any fit: {seo}
    - description: first line is a keyword-rich one-sentence hook.

    Output ONLY minified JSON, no code fences, matching:
    {schema}
    """
)


def _prompt_for(topic: str, seo_terms: list[str] | None = None) -> str:
    seo = "; ".join(seo_terms or []) or "(none - skip this rule)"
    return _PROMPT.format(
        topic=topic,
        schema=_SCHEMA_HINT,
        seo=seo,
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


def _groq_pref(model: str) -> int:
    """Lower = try sooner. gpt-oss and llama are the reliable JSON producers;
    qwen3 on Groq has hit both json_validate_failed and the reasoning_effort
    quirk, so it goes last."""
    m = model.lower()
    if "llama-3.3-70b" in m or "gpt-oss-120b" in m:
        return 0
    if "gpt-oss" in m or ("llama" in m and "guard" not in m):
        return 1
    if "kimi" in m:
        return 2
    if "qwen" in m:
        return 4
    return 3


def _groq_models() -> list[str]:
    """Live model ids for this key, ordered by _groq_pref, then the hardcoded rest."""
    live: list[str] = []
    try:
        r = requests.get(f"{_GROQ_URL}/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}", **UA}, timeout=30)
        if r.ok:
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            chat = [i for i in ids if i and not any(b in i.lower() for b in _GROQ_SKIP)]
            live = sorted(chat, key=lambda m: (_groq_pref(m), m))
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


def _via_groq(topic: str, seo_terms=None) -> dict | None:
    if not config.GROQ_API_KEY:
        return None
    prompt = _prompt_for(topic, seo_terms)
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


def _via_pollinations(topic: str, seo_terms=None) -> dict | None:
    # The legacy text API is now paywalled (402) for everyone; kept as one
    # quick attempt in case that changes, but no longer worth a retry loop.
    try:
        r = requests.post(
            "https://text.pollinations.ai/", headers=UA, timeout=45,
            json={
                "messages": [{"role": "user", "content": _prompt_for(topic, seo_terms)}],
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


def _via_gemini(topic: str, seo_terms=None) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None
    prompt = _prompt_for(topic, seo_terms)
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


# Filler for the last-resort template (only runs when every model API is down).
_TEMPLATE_FILLER = [
    "The version in the textbooks skips the part that actually mattered.",
    "The people who were there recorded something the legend leaves out.",
    "What looked like a single decisive moment was really years in the making.",
    "The real cause was duller and stranger than the story we were told.",
    "It reshaped borders that still sit roughly where it left them.",
    "Chroniclers on both sides agreed on the outcome and almost nothing else.",
    "One overlooked detail changed the balance of power for generations.",
    "The winners wrote the account, and it shows.",
    "Archaeology keeps confirming the boring explanation over the dramatic one.",
    "Knowing why it happened is more unsettling than the myth ever was.",
]
_HISTORY_TAGS = ["history", "history facts", "historical facts", "ancient history",
                 "world history", "history shorts"]
_HISTORY_HASHTAGS = ["#history", "#historyfacts", "#shorts"]


def _fix_unicode(text: str) -> str:
    """Straighten fancy dashes / quotes the models sometimes emit."""
    for bad, good in (
        ("‑", "-"), ("–", "-"), ("—", "-"), ("−", "-"),
        ("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
        ("…", "..."), (" ", " "),
    ):
        text = text.replace(bad, good)
    return text


def _template(topic: str) -> dict:
    extract = _wikipedia_summary(topic)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", extract) if len(s.strip()) > 20]
    sentences = sentences[:_MAX_BEATS]

    seed = [
        f"There is a lesser-known story behind {topic}.",
        f"Here is what actually happened with {topic}.",
    ]
    body = sentences or seed
    i = 0
    while len(body) < _MAX_BEATS - 1:
        body.append(_TEMPLATE_FILLER[i % len(_TEMPLATE_FILLER)])
        i += 1
    body = body[:_MAX_BEATS]

    beats = [{"say": s, "visual": topic, "keyword": topic[:24]} for s in body]
    return {
        "title": f"The untold story of {topic}"[:70],
        "hook": f"Most people get {topic} completely wrong.",
        "beats": beats,
        "timeline": [],
        "cta": "Follow for a piece of history every day.",
        "description": f"A surprising look at {topic}.",
        "tags": list(_HISTORY_TAGS),
        "hashtags": list(_HISTORY_HASHTAGS),
    }


def _clean_timeline(raw) -> list[dict]:
    out: list[dict] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        try:
            year = int(str(p.get("year")).strip().lstrip("c").strip())
        except (TypeError, ValueError):
            continue
        label = _fix_unicode(str(p.get("label", "")).strip())
        if label and -4000 < year < 2100:
            out.append({"year": year, "label": label[:70]})
    # dedupe by year, keep order, cap 6
    seen: set[int] = set()
    uniq = [p for p in out if not (p["year"] in seen or seen.add(p["year"]))]
    return uniq[:6]


def _normalise(topic: str, data: dict, seo_terms: list[str] | None = None) -> dict:
    beats: list[dict] = []
    for b in data.get("beats", []):
        say = _fix_unicode(str(b.get("say", "")).strip())
        visual = _fix_unicode(str(b.get("visual", "")).strip()) or topic
        keyword = _fix_unicode(str(b.get("keyword", "")).strip())[:28]
        if say:
            beats.append({"say": say, "visual": visual, "keyword": keyword})
    if len(beats) < 3:
        raise ValueError("script has too few usable beats")

    hook = _fix_unicode(str(data.get("hook", "")).strip()) or beats[0]["say"]
    cta = _fix_unicode(str(data.get("cta", "")).strip()) or "Follow for a piece of history every day."
    narration = re.sub(r"\s+", " ", " ".join([hook] + [b["say"] for b in beats] + [cta])).strip()

    # tags = model tags + real YouTube search phrases + a history base set
    seen: set[str] = set()
    tags: list[str] = []
    for t in (list(data.get("tags", [])) + list(seo_terms or []) + _HISTORY_TAGS):
        t = _fix_unicode(str(t).strip().lstrip("#"))
        if t and t.lower() not in seen and len(t) <= 60:
            seen.add(t.lower())
            tags.append(t)
    tags = tags[:15]

    hashtags = [h if str(h).startswith("#") else f"#{h}" for h in data.get("hashtags", [])]
    for default in _HISTORY_HASHTAGS:
        if default not in [h.lower() for h in hashtags]:
            hashtags.append(default)
    hashtags = [_fix_unicode(h) for h in hashtags][:8]

    description = _fix_unicode(str(data.get("description", "")).strip())
    description = f"{description}\n\n{cta}\n{' '.join(hashtags)}".strip()

    title = _fix_unicode(str(data.get("title", "")).strip()) or topic
    return {
        "topic": topic,
        "title": title[:100],
        "hook": hook,
        "beats": beats,
        "timeline": _clean_timeline(data.get("timeline")),
        "cta": cta,
        "narration": narration,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "word_count": len(narration.split()),
        "target_seconds": config.TARGET_SECONDS,
    }


def build(topic: str, seo_terms: list[str] | None = None) -> dict:
    # Each provider self-skips if its key is unset, so this is just priority
    # order: Groq -> Gemini -> Pollinations -> Wikipedia template.
    raw: dict | None = None
    source = "template"
    for name, fn in (("groq", _via_groq), ("gemini", _via_gemini), ("pollinations", _via_pollinations)):
        raw = fn(topic, seo_terms)
        if raw:
            source = name
            break

    script: dict | None = None
    if raw:
        try:
            script = _normalise(topic, raw, seo_terms)
        except Exception as exc:  # noqa: BLE001
            print(f"[script] {source} output rejected ({exc}); using template")
            raw = None
    if not raw:
        script = _normalise(topic, _template(topic), seo_terms)
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
