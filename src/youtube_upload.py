"""Upload the finished short to the owner's YouTube channel.

Auth = OAuth refresh token minted once via scripts/get-token.mjs and stored as
GitHub secrets. Uploads with privacyStatus from config.PRIVACY (default private).
The resumable upload retries transient 5xx / connection errors with backoff so a
single network blip does not lose the day's run.
"""
from __future__ import annotations

import datetime as _dt
import random
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as _build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config

_RETRIABLE_STATUS = {500, 502, 503, 504}
_MAX_RETRIES = 5


def _service():
    creds = Credentials(
        token=None,
        refresh_token=config.GOOGLE_REFRESH_TOKEN(),
        client_id=config.GOOGLE_CLIENT_ID(),
        client_secret=config.GOOGLE_CLIENT_SECRET(),
        token_uri=config.GOOGLE_TOKEN_URI,
        scopes=config.GOOGLE_SCOPES,
    )
    creds.refresh(Request())
    return _build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(video_path: str, script: dict) -> dict:
    title = script["title"][:100]
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"[:100]

    body = {
        "snippet": {
            "title": title,
            "description": script["description"][:4900],
            "tags": script["tags"][:15],
            "categoryId": config.YT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=5 * 1024 * 1024, resumable=True)
    request = _service().videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"[youtube] upload {int(status.progress() * 100)}%")
        except HttpError as exc:
            if exc.resp.status in _RETRIABLE_STATUS and retries < _MAX_RETRIES:
                retries += 1
                wait = min(60, 2 ** retries) + random.random()
                print(f"[youtube] transient {exc.resp.status}; retry {retries}/{_MAX_RETRIES} in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            if retries < _MAX_RETRIES:
                retries += 1
                wait = min(60, 2 ** retries) + random.random()
                print(f"[youtube] {type(exc).__name__}; retry {retries}/{_MAX_RETRIES} in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    print(f"[youtube] done: {url}  (privacy={config.PRIVACY})")
    return {
        "video_id": vid,
        "url": url,
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
        "uploaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
