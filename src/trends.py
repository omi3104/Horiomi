"""Pick one fresh, high-demand HISTORY / GEOPOLITICS topic for today.

Niche: ancient & medieval history and historical geopolitics - Greeks, Romans,
Persians, Normans, Byzantines, the Islamic world, the British empire, the Mongols,
the World Wars and the Cold War *as history*. Surprising, lesser-known angles on
famous subjects. NOT current partisan politics.

Signal sources (all free, no key):
  * Wikipedia most-viewed articles (yesterday)  -> real audience demand
  * Wikipedia "On this day" events              -> a dated, timely hook
  * YouTube search-suggest for era seed terms   -> proven long-tail queries
  * A large curated bank of dramatic hooks      -> never stalls

"Let search demand weight the era spread": whichever civilisation has the most
pageviews / richest YouTube suggestions that day naturally wins more slots.
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import random
import re
import urllib.parse

import requests

from . import config, state

UA = {"User-Agent": "yt-shorts-agent/1.1 (history shorts; contact via repo)"}

# --- what counts as our niche ------------------------------------------------
_ALLOW = re.compile(
    r"\b("
    r"empires?|emperors?|dynast(y|ies)|caliphates?|caliphs?|sultanates?|pharaohs?|"
    r"roman|rome|byzantin\w*|constantinople|hellenistic|spartan|athenian|"
    r"persia\w*|achaemenid|sassanid|parthian|"
    r"ottomans?|mughals?|mongols?|khanate|viking|norse|normans?|saxons?|franks?|"
    r"crusades?|crusaders?|templars?|medieval|mediaeval|antiquity|dark ages|middle ages|"
    r"mesopotamia|babylon\w*|assyria\w*|sumer\w*|akkad\w*|hittite|phoenician|carthag\w*|"
    r"ancient egypt|ancient greece|ancient china|ancient india|"
    r"battle of|siege of|sack of|fall of|rise of|war of|wars of|treaty of|"
    r"reconquista|the reconquest|partition of|conquest of|"
    r"kings? of|queens? of|emperors? of|tsar of|monarchy of|"
    r"legions?|phalanx|hoplites?|centurions?|gladiators?|knights?|samurai|janissar\w*|"
    r"plantagenet|tudor|stuart|habsburg|bourbon|romanov|carolingian|merovingian|"
    r"ming dynasty|qing dynasty|han dynasty|tang dynasty|song dynasty|yuan dynasty|shogun\w*|"
    r"charlemagne|saladin|genghis khan|tamerlane|attila|hannibal|scipio|"
    r"julius caesar|augustus caesar|cleopatra|constantine the great|justinian|"
    r"alexander the great|cyrus the great|darius|xerxes|leonidas|pericles|"
    r"napoleon\w*|napoleonic|bismarck|colonial|coloni[sz]ation|decoloni\w*|"
    r"world war [i1v2]|first world war|second world war|cold war|the great war|"
    r"western front|eastern front|"
    r"history of|early history|archaeolog\w*|excavation|treasure hoard|ancient ruins"
    r")\b",
    re.I,
)

_REJECT = re.compile(
    r"("
    r"\(film\)|\(tv series\)|\(video game\)|\(album\)|\(song\)|\(band\)|\(rapper\)|"
    r"\(singer\)|\(actor\)|\(actress\)|\(footballer\)|discography|filmography|"
    r"\bseason \d|\bepisode \d|"
    r"premier league|\bnba\b|\bnfl\b|\bipl\b|world cup 20|\bfifa\b|\bufc\b|wwe|wrestlemania|"
    r"formula one 20|grand prix 20|netflix|marvel cinematic|box office|"
    r"\b(19[89]\d|20[0-2]\d)\b|"                          # a modern year in the title
    r"\belections?\b|\bsenator\b|\bcongress\b|prime minister of|president of|"
    r"gaza|hamas|hezbollah|west bank|ceasefire|air ?strike|missile strike|drone strike|"
    r"russia[- ]ukraine|russian invasion|war in ukraine|zelensky|\bputin\b|kremlin|"
    r"\btrump\b|\bbiden\b|\bharris\b|\bobama\b|\bmodi\b|imran khan|xi jinping|netanyahu|"
    r"macron|starmer|sunak|erdogan|"
    r"covid|pandemic|vaccine|cryptocurrenc|bitcoin|stock market|\bgpt\b|chatgpt|openai|"
    r"tesla|spacex|celebrity|influencer|tiktok|youtuber|kardashian|taylor swift|bangchan|"
    r"list of|category:|wikipedia:|template:|\.jpg|\.png|deaths in|main page"
    r")",
    re.I,
)

# Wikipedia-category classifier - the real gate for the pageviews list.
_CAT_STRONG = re.compile(
    r"histor|\bancient\b|antiquit|classical antiquity|\bmedieval\b|middle ages|"
    r"\bempires?\b|\bdynast|imperial|\bwars?\b(?! on drugs)|battles|sieges|"
    r"military (history|leaders)|generals|conqueror|crusad|archaeolog|"
    r"century bc|\dth century bc|bc births|bc deaths|\bad \d|"
    r"roman empire|roman republic|ancient rome|byzantine|ottoman|mughal|mongol|"
    r"\bmonarchs\b|\bemperors\b|\bkings\b|\bqueens\b|\btsars\b|pharaohs|caliph|"
    r"kingdoms|principalit|city-states|nobility|aristocrats",
    re.I,
)
_CAT_MODERN = re.compile(
    r"living people|21st-century|(19[5-9]\d|20\d\d) births|"
    r"\bmusicians\b|\bsingers\b|\brappers\b|\bactors\b|\bactresses\b|film actors|"
    r"music industry|record producers|television (actors|personalities)|"
    r"association football|\bfootballers\b|basketball players|cricketers|"
    r"tennis players|\bolympic\b|\bnfl\b|\bnba\b|"
    r"(19[5-9]\d|20\d\d) (songs|albums|films|video games|singles|television)|"
    r"streamers|youtubers|podcasters|social media|models \(|beauty pageant",
    re.I,
)

_ERA_SEEDS = [
    "roman empire", "ancient rome", "ancient greece", "byzantine empire",
    "ancient persia", "achaemenid empire", "ottoman empire", "mongol empire",
    "norman conquest", "viking history", "islamic golden age", "the crusades",
    "ancient egypt", "medieval england", "british empire", "napoleonic wars",
    "cold war history", "world war 1 history", "carthage rome war",
]

# --- keyless curated bank (dramatic, lesser-known angles) ------------------
_FALLBACK_BANK = [
    "the Byzantine weapon that terrified enemies for 700 years",
    "why Rome really pulled its legions out of Britain",
    "the Persian royal road that let news cross an empire in a week",
    "the forgotten Norman kingdom that ruled Sicily",
    "how a sandstorm may have swallowed a whole Persian army",
    "the Roman emperor who was sold the throne at auction",
    "why the Library of Alexandria's fall is mostly a myth",
    "the Greek fire recipe the Byzantines took to their grave",
    "the Mongol postal system that outran every medieval kingdom",
    "how Constantinople's walls held for a thousand years",
    "the Roman concrete formula that still baffles engineers",
    "why Sparta almost never built city walls",
    "the Viking who became a Roman emperor's bodyguard",
    "the treaty that split the unknown world between Spain and Portugal",
    "how the Black Death quietly ended serfdom in Western Europe",
    "the Egyptian queen who ruled as pharaoh and was erased from records",
    "why Hannibal's elephants barely survived the Alps",
    "the Chinese admiral whose fleet dwarfed Columbus by decades",
    "the Roman road network that still shapes European motorways",
    "how a single bad harvest helped topple the Western Roman Empire",
    "the Ottoman siege gun so big it took 60 oxen to move",
    "the Anglo-Saxon king who paid the Vikings to leave, again and again",
    "why the Battle of Tours mattered far less than legend says",
    "the Persian king who dug a canal Alexander would later use",
    "how Venice ran a maritime empire from a swamp",
    "the Roman fort in Scotland the empire abandoned twice",
    "the lost Roman legion that vanished in Britain",
    "why Genghis Khan's tomb has never been found",
    "the medieval Baghdad house of wisdom that saved Greek science",
    "how the printing press broke the Church's monopoly on knowledge",
    "the Spartan mirage: how much of the legend was propaganda",
    "the Byzantine princess who wrote the first real history by a woman",
    "why the Great Wall failed to stop the Mongols",
    "the Roman emperor who never lost a battle and is forgotten",
    "how salt built and broke ancient trade empires",
    "the Norman survey that recorded all of England in one year",
    "the Carthaginian navy that ruled the Mediterranean before Rome",
    "why Alexander's empire shattered within a generation",
    "the Islamic scholars who calculated Earth's size in the 9th century",
    "the medieval Icelandic parliament older than England's",
    "how the stirrup may have created the mounted knight",
    "the Roman grain fleet that fed a million people",
    "the Persian immortals: elite unit or clever exaggeration",
    "why Julius Caesar's calendar still runs our year",
    "the Viking trade route from Sweden to Baghdad",
    "the fall of Constantinople and the cannon that ended an age",
    "how Rome recycled its own monuments into churches",
    "the Mongol siege tactic that emptied entire cities without a fight",
    "the Anglo-Saxon treasure hoard found under a farmer's field",
    "why the Roman Republic never recovered from its own generals",
]


def _clean(text: str) -> str:
    t = html.unescape(text or "").replace("_", " ").strip()
    return re.sub(r"\s+", " ", t).strip(" .")


def _looks_like_niche(title: str) -> bool:
    if _REJECT.search(title):
        return False
    return bool(_ALLOW.search(title))


def _category_map(titles: list[str]) -> dict[str, str]:
    """One batched call: {normalised title -> joined category string} for up to
    ~50 titles. Missing from the map == lookup failed for that title."""
    out: dict[str, str] = {}
    for group in (titles[i:i + 45] for i in range(0, len(titles), 45)):
        try:
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "prop": "categories", "cllimit": "500",
                        "clshow": "!hidden", "titles": "|".join(group), "format": "json"},
                headers=UA, timeout=20,
            )
            data = r.json().get("query", {})
        except Exception as exc:  # noqa: BLE001
            print(f"[trends] category batch failed: {exc}")
            continue
        norm = {n["from"]: n["to"] for n in data.get("normalized", [])}
        for p in data.get("pages", {}).values():
            key = p.get("title", "")
            cats = " ; ".join(c.get("title", "") for c in p.get("categories", []))
            out[key] = cats
        # map the pre-normalisation titles too, so callers can look up either form
        for raw, to in norm.items():
            if to in out:
                out[raw] = out[to]
    return out


def _cats_are_history(cats: str | None) -> bool:
    if cats is None:
        return False
    if _CAT_MODERN.search(cats):
        return False
    return bool(_CAT_STRONG.search(cats))


def _wikipedia_top() -> list[dict]:
    day = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
           f"en.wikipedia/all-access/{day:%Y/%m/%d}")
    try:
        data = requests.get(url, headers=UA, timeout=20).json()
        articles = data["items"][0]["articles"]
    except Exception as exc:  # noqa: BLE001
        print(f"[trends] wikipedia pageviews failed: {exc}")
        return []

    pre = []
    for a in articles:
        title = a.get("article", "")
        if not title or title in ("Main_Page",) or title.startswith(("Special:", "Wikipedia:")):
            continue
        nice = _clean(title)
        if 6 <= len(nice) <= 90 and _looks_like_niche(nice):
            pre.append({"topic": nice, "raw": title.replace("_", " "), "score": int(a.get("views", 0))})
    pre.sort(key=lambda c: -c["score"])
    pre = pre[:40]

    cats = _category_map([c["raw"] for c in pre])
    out = []
    for c in pre:
        verdict = cats.get(c["raw"])
        # if the category lookup worked, require a history verdict; if it failed
        # entirely (no map), fall back to trusting the keyword pre-filter.
        if (verdict is not None and _cats_are_history(verdict)) or (not cats):
            out.append({
                "topic": c["topic"], "score": c["score"], "source": "wikipedia-top",
                "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(c['raw'].replace(' ', '_'))}",
            })
        if len(out) >= 12:
            break
    print(f"[trends] wikipedia-top: {len(out)}/{len(pre)} candidates pass "
          f"(top score {out[0]['score'] if out else 0})")
    return out


def _on_this_day() -> list[dict]:
    now = _dt.datetime.now(_dt.timezone.utc)
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{now:%m/%d}"
    try:
        events = requests.get(url, headers=UA, timeout=20).json().get("events", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[trends] on-this-day failed: {exc}")
        return []
    out = []
    for e in events:
        year = e.get("year")
        pages = e.get("pages") or []
        if not year or year > now.year - 60:          # keep it firmly historical
            continue
        text = _clean(e.get("text", ""))
        anchor = _clean((pages[0].get("normalizedtitle") if pages else "") or text)
        blob = f"{anchor} {text}"
        if not _looks_like_niche(blob):
            continue
        topic = f"{anchor} in {year}" if anchor and str(year) not in anchor else anchor or text
        page_url = ""
        if pages:
            page_url = (pages[0].get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        out.append({"topic": topic[:90], "score": 500, "source": "on-this-day", "url": page_url})
    random.shuffle(out)
    print(f"[trends] on-this-day: {len(out)} history events for {now:%m-%d}")
    return out[:8]


def youtube_suggest(seed: str, limit: int = 10) -> list[str]:
    """Real YouTube autocomplete phrases for a seed - proven search demand."""
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "ds": "yt", "hl": "en",
                    "gl": config.GEO or "US", "q": seed},
            headers=UA, timeout=15,
        )
        arr = json.loads(r.text)
        sugg = [s for s in (arr[1] if len(arr) > 1 else []) if isinstance(s, str)]
        return sugg[:limit]
    except Exception as exc:  # noqa: BLE001
        print(f"[trends] youtube-suggest({seed!r}) failed: {exc}")
        return []


def _suggest_candidates() -> list[dict]:
    seeds = random.sample(_ERA_SEEDS, k=min(6, len(_ERA_SEEDS)))
    out: list[dict] = []
    for seed in seeds:
        for phrase in youtube_suggest(seed, limit=8):
            p = _clean(phrase)
            if 12 <= len(p) <= 90 and not _REJECT.search(p) and _ALLOW.search(p):
                out.append({"topic": p, "score": 300, "source": "youtube-suggest", "url": ""})
    print(f"[trends] youtube-suggest: {len(out)} phrases from {len(seeds)} era seeds")
    return out


def seo_terms_for(topic: str) -> list[str]:
    """A handful of real YouTube search phrases related to the chosen topic,
    for the script writer to fold into the title / tags / description."""
    terms: list[str] = []
    for s in (topic, " ".join(topic.split()[:3])):
        terms += youtube_suggest(s, limit=6)
    seen: set[str] = set()
    uniq = []
    for t in terms:
        k = _clean(t).lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(_clean(t))
    return uniq[:8]


def pick_topic() -> dict:
    """Return {topic, source, url, score, boosted, seo}."""
    if config.TOPIC_OVERRIDE:
        picked = {"topic": config.TOPIC_OVERRIDE, "source": "override", "url": "",
                  "score": 0, "boosted": False}
        picked["seo"] = seo_terms_for(picked["topic"])
        return picked

    pool: list[dict] = []
    pool += _wikipedia_top()
    pool += _on_this_day()
    pool += _suggest_candidates()

    fresh = [c for c in pool if state.is_fresh(c["topic"])]
    print(f"[trends] {len(pool)} candidates, {len(fresh)} fresh after de-dup")

    if fresh:
        # Weight by demand: pageviews dominate, suggest/on-this-day get a flat
        # base. A small jitter keeps successive days from being identical.
        for c in fresh:
            c["rank"] = c["score"] * random.uniform(0.85, 1.15)
        fresh.sort(key=lambda c: c["rank"], reverse=True)
        best = fresh[0]
        best["boosted"] = best["source"] != "wikipedia-top"
    else:
        bank = [t for t in _FALLBACK_BANK if state.is_fresh(t)] or _FALLBACK_BANK
        best = {"topic": random.choice(bank), "source": "fallback-bank", "url": "",
                "score": 0, "boosted": False}

    best["seo"] = seo_terms_for(best["topic"])
    print(f"[trends] chosen: {best['topic']!r}  ({best['source']}, "
          f"score={best.get('score', 0)}, seo={best['seo'][:3]})")
    return best
