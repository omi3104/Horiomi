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
PEXELS_API_KEY = get("PEXELS_API_KEY")     # nicer stock video if set
PIXABAY_API_KEY = get("PIXABAY_API_KEY")   # extra stock source if set
DRIVE_FOLDER_ID = get("DRIVE_FOLDER_ID")   # also copy finished video here

# --- knobs -------------------------------------------------------------
VOICE = get("VOICE", "en-US-AndrewNeural")
GEO = get("GEO", "US")
YT_CATEGORY_ID = get("YT_CATEGORY_ID", "27")   # 27 = Education
PRIVACY = get("PRIVACY", "private").lower()
WHISPER_MODEL = get("WHISPER_MODEL", "small")
TOPIC_OVERRIDE = get("TOPIC_OVERRIDE")
DRY_RUN = get("DRY_RUN", "").lower() in ("1", "true", "yes")   # build but do not upload

# --- video spec ------------------------------------------------------
WIDTH, HEIGHT = 1080, 1920
FPS = 30
TARGET_SECONDS_MIN = 20
TARGET_SECONDS_MAX = 58

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
