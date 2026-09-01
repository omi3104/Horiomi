"""Optional: copy the finished video + script JSON into a Google Drive folder
so you can review from your phone. No-op unless DRIVE_FOLDER_ID is set.
Uses the same OAuth credentials as the YouTube upload (drive.file scope).
"""
from __future__ import annotations

import json

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
    return _build("drive", "v3", credentials=creds, cache_discovery=False)


def _put(svc, name: str, path: str, mime: str) -> None:
    meta = {"name": name, "parents": [config.DRIVE_FOLDER_ID]}
    media = MediaFileUpload(path, mimetype=mime, resumable=True)
    f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
    print(f"[drive] uploaded {name} -> {f.get('webViewLink', f.get('id'))}")


def maybe_upload(video_path: str, script: dict) -> None:
    if not config.DRIVE_FOLDER_ID:
        print("[drive] DRIVE_FOLDER_ID not set - skipping")
        return
    try:
        svc = _service()
        stem = script["title"][:60].replace("/", "-")
        _put(svc, f"{stem}.mp4", video_path, "video/mp4")
        script_path = config.WORK / "script.json"
        script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        _put(svc, f"{stem}.json", str(script_path), "application/json")
    except Exception as exc:  # noqa: BLE001 - never fail the run over the review copy
        print(f"[drive] upload skipped due to error: {exc}")
