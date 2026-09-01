"""Upload the finished short to the owner's YouTube channel.

Auth = OAuth refresh token minted once via scripts/get-token.mjs and stored as
GitHub secrets. Uploads with privacyStatus from config.PRIVACY (default private).
"""
from __future__ import annotations

import datetime as _dt

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as _build
from googleapiclient.http import MediaFileUpload

from . import config


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
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=-1, resumable=True)
    request = _service().videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[youtube] upload {int(status.progress() * 100)}%")

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    print(f"[youtube] done: {url}  (privacy={config.PRIVACY})")
    return {
        "video_id": vid,
        "url": url,
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
        "uploaded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
