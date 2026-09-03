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
    # --- Islamic world + subcontinent (the channel's 60%) ------------------
    r"islamic|rashidun|umayyads?|abbasids?|fatimids?|ayyubids?|mamluks?|"
    r"seljuks?|ghaznavids?|ghurids?|safavids?|timurids?|emirates?|imamate|"
    r"al-andalus|andalusi|nasrid|almohad|almoravid|cordoba|granada|"
    r"delhi sultanate|bahmani|deccan|vijayanagara|marathas?|rajputs?|"
    r"sikh empire|maurya\w*|gupta empire|chola\w*|mysore|golconda|hyderabad|"
    r"british raj|east india company|company rule|sepoy|partition of (india|british india)|"
    r"prophet muhammad|abu bakr|\bumar\b|uthman|ali ibn abi talib|khalid ibn|"
    r"salah al-din|nur al-din|baibars|mehmed (ii|the conqueror)|suleiman the magnificent|"
    r"\bbabur\b|humayun|\bakbar\b|jahangir|shah jahan|aurangzeb|tipu sultan|"
    r"nadir shah|ahmad shah durrani|mahmud of ghazni|muhammad of ghor|"
    r"razia sultana|sher shah suri|prithviraj|tariq ibn ziyad|"
    r"battle of (yarmouk|qadisiyya|nahavand|ain jalut|talas|panipat|plassey|buxar)|"
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
    r"kingdoms|principalit|city-states|nobility|aristocrats|"
    r"sultanate|mamluk|abbasid|umayyad|fatimid|ayyubid|seljuk|al-andalus|"
    r"delhi sultanate|maratha|rajput|sikh empire|british raj|east india company|"
    r"islamic history|history of pakistan|history of india|history of iran|"
    r"medieval islam|muslim history",
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

# =====================================================================
#  Region mix - the channel runs ~60% Islamic world + subcontinent,
#  ~20% Europe/America, ~20% other history. Buckets are picked by these
#  weights; a bucket with no fresh candidate that day falls to its bank.
# =====================================================================
_WEIGHTS = [("islamic", 0.60), ("west", 0.20), ("other", 0.20)]

_R_ISLAMIC = re.compile(
    r"islam|muslim|\bquran\b|caliph|rashidun|umayyad|abbasid|fatimid|ayyubid|"
    r"mamluk|seljuk|ghaznavid|ghurid|safavid|timurid|ottoman|sultan|emirate|"
    r"al-andalus|andalus|nasrid|almohad|almoravid|cordoba|granada|moor|"
    r"mughal|delhi sultanate|bahmani|deccan|vijayanagara|maratha|rajput|"
    r"sikh empire|maurya|gupta empire|chola|mysore|hyderabad|golconda|"
    r"british raj|east india company|sepoy|partition of (india|british india)|"
    r"\bpakistan\b|hindustan|\bsindh\b|\bpunjab\b|bengal|afghan|"
    r"persia|persian|\biran\b|arab|arabia|mecca|medina|baghdad|damascus|cairo|"
    r"muhammad|abu bakr|\bumar\b|uthman|\bali ibn|khalid ibn|saladin|salah al-din|"
    r"nur al-din|baibars|suleiman|mehmed|\bakbar\b|\bbabur\b|humayun|jahangir|"
    r"shah jahan|aurangzeb|tipu|nadir shah|ahmad shah|mahmud of ghazni|"
    r"muhammad of ghor|razia|sher shah|tariq ibn ziyad|"
    r"crusade|constantinople|\b1453\b|panipat|plassey|buxar|"
    r"yarmouk|qadisiyya|nahavand|ain jalut|siege of vienna|talas",
    re.I,
)
_R_WEST = re.compile(
    r"\brome\b|roman|greek|greece|byzantin|athen|sparta|\bgaul\b|celt|hellen|"
    r"norman|saxon|viking|norse|\bfrank|charlemagne|carolingian|merovingian|"
    r"britain|british|england|english|scotland|ireland|\bwales\b|france|french|"
    r"spain|spanish|portugal|portuguese|german|prussia|austria|habsburg|"
    r"italy|italian|venice|florence|genoa|papal|\bpope\b|holy roman|"
    r"russia|russian|\btsar|romanov|poland|polish|sweden|swedish|dutch|"
    r"united states|america|american revolution|confedera|\bunion\b|"
    r"napoleon|waterloo|bismarck|world war|western front|cold war|nazi|"
    r"churchill|lincoln|washington|jefferson|tudor|stuart|plantagenet",
    re.I,
)


def _region(topic: str) -> str:
    t = (topic or "").lower()
    if _R_ISLAMIC.search(t):
        return "islamic"
    if _R_WEST.search(t):
        return "west"
    return "other"


def _weighted_order(weights: list[tuple[str, float]]) -> list[str]:
    """Random ordering of the region names, biased by weight (islamic first ~60%)."""
    items = list(weights)
    order: list[str] = []
    while items:
        total = sum(w for _, w in items)
        pick = random.uniform(0, total)
        acc = 0.0
        for idx, (name, w) in enumerate(items):
            acc += w
            if pick <= acc:
                order.append(name)
                items.pop(idx)
                break
        else:
            order.append(items.pop()[0])
    return order


_SEEDS = {
    "islamic": [
        "ottoman empire history", "mughal empire", "islamic golden age",
        "abbasid caliphate", "rise of islam", "salahuddin ayyubi", "al andalus history",
        "delhi sultanate", "tipu sultan", "aurangzeb", "battle of badr", "crusades history",
        "fall of constantinople 1453", "nadir shah", "maratha empire", "sikh empire",
        "partition of india", "mahmud of ghazni", "battle of plassey", "mamluk sultanate",
        "spread of islam in india", "ahmad shah abdali panipat",
    ],
    "west": [
        "roman empire history", "ancient greece", "byzantine empire", "viking history",
        "norman conquest 1066", "napoleonic wars", "british empire", "world war 1 history",
    ],
    "other": [
        "ancient egypt", "mongol empire", "ancient persia", "han dynasty china",
        "samurai history", "aztec empire", "kingdom of mali", "carthage vs rome",
    ],
}

# Curated famous-topic banks (used only when the day yields no fresh candidate
# for a bucket). Dramatic, well-known, high search volume.
_BANKS = {
    "islamic": [
        "how Salahuddin retook Jerusalem in 1187",
        "the night the Abbasid Caliphate fell to the Mongols in 1258",
        "why the fall of Constantinople in 1453 ended the Middle Ages",
        "the untold story of the Battle of Badr",
        "how Tariq ibn Ziyad conquered Spain in 711",
        "the House of Wisdom that made Baghdad the center of science",
        "how Muhammad bin Qasim brought Islam to Sindh at seventeen",
        "why the Third Battle of Panipat broke the Maratha Empire",
        "how Babur won India at Panipat with 12,000 men",
        "the six months Aurangzeb spent chasing one Maratha fort",
        "how Tipu Sultan's rockets shocked the British army",
        "the Battle of Plassey: how a bribe handed Bengal to a company",
        "how Nadir Shah emptied Delhi's treasury in 57 days",
        "why Akbar invented his own religion",
        "the Ottoman siege gun so huge it took 60 oxen to move",
        "how the Janissaries went from elite slaves to kingmakers",
        "the lost Muslim kingdom of Sicily",
        "how Al-Andalus kept flushing toilets while Europe forgot them",
        "why the Reconquista took almost 800 years",
        "the Mamluks: slave soldiers who stopped the Mongols at Ain Jalut",
        "how Mansa Musa's hajj crashed the economy of Egypt",
        "the Mughal throne made of a tonne of gold and jewels",
        "why Sher Shah Suri's five-year reign reshaped India",
        "how the East India Company went from traders to rulers",
        "the real reason the Ottoman Empire was called 'the sick man of Europe'",
        "how Razia Sultana became the only woman to rule Delhi",
        "the Siege of Vienna that stopped the Ottomans at Europe's door",
        "how paper reached Europe through the Islamic world",
        "why Timur built towers out of skulls",
        "the Battle of Talas that gave the Muslim world papermaking",
    ],
    "west": [
        "why Rome really pulled its legions out of Britain",
        "the Greek fire the Byzantines took to their grave",
        "how Constantinople's walls held for a thousand years",
        "why Hannibal's elephants barely survived the Alps",
        "the lost Roman legion that vanished in Britain",
        "how the Black Death quietly ended serfdom in Europe",
        "the Norman survey that recorded all of England in a year",
        "why Sparta almost never built city walls",
        "how one bad harvest helped topple the Western Roman Empire",
        "the Roman concrete formula that still baffles engineers",
        "why Julius Caesar's calendar still runs our year",
        "the Viking who became the Byzantine emperor's bodyguard",
    ],
    "other": [
        "why Genghis Khan's tomb has never been found",
        "the Chinese admiral whose fleet dwarfed Columbus by decades",
        "the Egyptian queen who ruled as pharaoh and was erased",
        "how the Mongol postal system outran every medieval kingdom",
        "why the Great Wall failed to stop the Mongols",
        "how salt built and broke ancient trade empires",
        "the Persian royal road that crossed an empire in a week",
        "how a sandstorm may have swallowed a whole Persian army",
        "why Alexander's empire shattered within a generation",
        "the Aztec capital that was bigger than any city in Europe",
    ],
}


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
    # ~60/20/20 seed sampling so demand data itself skews to the target mix
    seeds = (random.sample(_SEEDS["islamic"], k=min(5, len(_SEEDS["islamic"])))
             + random.sample(_SEEDS["west"], k=2)
             + random.sample(_SEEDS["other"], k=2))
    out: list[dict] = []
    for seed in seeds:
        for phrase in youtube_suggest(seed, limit=8):
            p = _clean(phrase)
            if 12 <= len(p) <= 90 and not _REJECT.search(p) and _ALLOW.search(p):
                out.append({"topic": p, "score": 300, "source": "youtube-suggest", "url": ""})
    print(f"[trends] youtube-suggest: {len(out)} phrases from {len(seeds)} seeds")
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
    for c in fresh:
        c["region"] = _region(c["topic"])
        c["rank"] = c["score"] * random.uniform(0.85, 1.15)
    buckets = {
        r: sorted((c for c in fresh if c["region"] == r), key=lambda c: -c["rank"])
        for r, _ in _WEIGHTS
    }
    print(f"[trends] {len(fresh)} fresh: "
          + ", ".join(f"{r}={len(buckets[r])}" for r, _ in _WEIGHTS))

    order = _weighted_order(_WEIGHTS)   # e.g. ['islamic','other','west']
    best: dict | None = None
    for r in order:
        if buckets[r]:
            best = buckets[r][0]
            break
    if best is None:
        for r in order:                # nothing fresh anywhere -> curated bank
            bank = [t for t in _BANKS[r] if state.is_fresh(t)] or _BANKS[r]
            best = {"topic": random.choice(bank), "source": f"bank-{r}",
                    "url": "", "score": 0}
            break

    assert best is not None
    best["region"] = _region(best["topic"])
    best.setdefault("boosted", best.get("source") != "wikipedia-top")
    best["seo"] = seo_terms_for(best["topic"])
    print(f"[trends] chosen: {best['topic']!r}  "
          f"({best['source']}, region={best['region']}, "
          f"score={best.get('score', 0)}, seo={best['seo'][:3]})")
    return best
