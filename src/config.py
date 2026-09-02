"""Central configuration. Reads environment (and a local .env if present).

Design: the CONTENT pipeline is 100% keyless. The only credentials required
are the ones for uploading to YOUR OWN YouTube channel (Google mandates the
channel owner authorise the app once). Optional keys, if present, upgrade
quality but are never required.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"          # scratch space for a single run (git-ignored)
OUT = ROOT / "out"            # final deliverables (git-ignored)
DATA = ROOT / "data"          # small persisted state (committed back by CI)
ASSETS = ROOT / "assets"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_int(name: str, default: int) -> int:
    try:
        return int(float(get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def get_float(name: str, default: float) -> float:
    try:
        return float(get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def get_bool(name: str, default: bool) -> bool:
    raw = get(name, "").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def require(name: str) -> str:
    value = get(name)
    if not value:
        raise SystemExit(
            f"[config] Missing required environment variable: {name}\n"
            f"         Set it as a GitHub Actions secret (or in .env locally)."
        )
    return value


# --- REQUIRED: your YouTube upload credentials (from scripts/get-token.mjs) --
GOOGLE_CLIENT_ID = lambda: require("GOOGLE_CLIENT_ID")          # noqa: E731
GOOGLE_CLIENT_SECRET = lambda: require("GOOGLE_CLIENT_SECRET")  # noqa: E731
GOOGLE_REFRESH_TOKEN = lambda: require("GOOGLE_REFRESH_TOKEN")  # noqa: E731

# --- OPTIONAL: quality upgrades. Blank = that source is skipped. ----------
GEMINI_API_KEY = get("GEMINI_API_KEY")     # better scripts + AI images if set
PEXELS_API_KEY = get("PEXELS_API_KEY")     # real stock VIDEO + photos if set
PIXABAY_API_KEY = get("PIXABAY_API_KEY")   # extra stock video + photo source
COVERR_API_KEY = get("COVERR_API_KEY")     # cinematic free stock video if set
DRIVE_FOLDER_ID = get("DRIVE_FOLDER_ID")   # also copy finished video here

# --- knobs -------------------------------------------------------------
VOICE = get("VOICE", "en-US-AndrewNeural")
GEO = get("GEO", "US")

YT_CATEGORY_ID = get("YT_CATEGORY_ID", "27")   # 27 = Education
if not YT_CATEGORY_ID.isdigit():
    print(f"[config] YT_CATEGORY_ID={YT_CATEGORY_ID!r} is not numeric; using '27'")
    YT_CATEGORY_ID = "27"

PRIVACY = get("PRIVACY", "private").lower()
if PRIVACY not in ("private", "unlisted", "public"):
    print(f"[config] PRIVACY={PRIVACY!r} invalid (private|unlisted|public); using 'private'")
    PRIVACY = "private"

WHISPER_MODEL = get("WHISPER_MODEL", "small")
TOPIC_OVERRIDE = get("TOPIC_OVERRIDE")
DRY_RUN = get("DRY_RUN", "").lower() in ("1", "true", "yes")   # build but do not upload

# Prefer real motion footage over stills when a source has any.
PREFER_VIDEO = get_bool("PREFER_VIDEO", True)

# --- video spec ------------------------------------------------------
WIDTH, HEIGHT = 1080, 1920
FPS = 30

# --- length target -----------------------------------------------------
# The finished short is kept inside this window: script_gen sizes the narration
# to roughly TARGET_SECONDS, tts.py nudges the speaking rate to land in range,
# and video.py pads / trims the tail so the rendered file is ALWAYS between MIN
# and MAX. YouTube now allows Shorts up to 3 minutes, so 50-80s is still a Short.
TARGET_SECONDS_MIN = get_int("TARGET_SECONDS_MIN", 50)
TARGET_SECONDS_MAX = get_int("TARGET_SECONDS_MAX", 80)
TARGET_SECONDS = get_int("TARGET_SECONDS", 0) or round(
    (TARGET_SECONDS_MIN + TARGET_SECONDS_MAX) / 2
)
# Measured average for edge-tts neural voices at TTS_RATE. Only used to size the
# script; the real duration is always measured back from the rendered audio.
SPEAKING_WPS = get_float("SPEAKING_WPS", 2.6)
TTS_RATE = get_int("TTS_RATE", 8)   # base edge-tts rate, percent (+ = faster)

# NOTE: youtube.upload and drive.file cannot be granted in the same
# unverified-app consent request, so the default token only carries
# youtube.upload. Drive review copies (drive_upload.py) simply no-op with a
# clear log line unless you mint a second token with drive.file and wire it
# up separately - see README.
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def ensure_dirs() -> None:
    for d in (WORK, OUT, DATA):
        d.mkdir(parents=True, exist_ok=True)
