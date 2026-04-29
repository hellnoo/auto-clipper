"""YouTube uploader.

Uses Google's official YouTube Data API v3. The OAuth flow is the
'installed application' / loopback-redirect style — the user clicks
'Connect YouTube' once, browser pops the consent screen, refresh token
is saved to config/youtube_token.json. Subsequent uploads are silent.

User one-time setup (~5 min, free):
  1. Create Google Cloud project at https://console.cloud.google.com
  2. APIs & Services -> Library -> enable 'YouTube Data API v3'
  3. APIs & Services -> Credentials -> Create OAuth client ID
       Application type: Desktop app
       Name: auto-clipper (anything)
  4. Download the JSON, save as config/youtube_client.json
  5. Click 'Connect YouTube' in the dashboard
"""
from __future__ import annotations

import json
from pathlib import Path
from loguru import logger

from .. import config


YT_CLIENT_PATH = Path(config.ROOT) / "config" / "youtube_client.json"
YT_TOKEN_PATH = Path(config.ROOT) / "config" / "youtube_token.json"
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class _ConfigError(RuntimeError):
    """Surfaces user-fixable configuration issues with actionable messages."""


def _load_client_config() -> dict:
    if not YT_CLIENT_PATH.exists():
        raise _ConfigError(
            f"missing {YT_CLIENT_PATH}. Setup steps:\n"
            "  1. https://console.cloud.google.com -> create project\n"
            "  2. Enable 'YouTube Data API v3'\n"
            "  3. Create OAuth 2.0 client (Desktop app)\n"
            "  4. Download JSON -> save as config/youtube_client.json"
        )
    return json.loads(YT_CLIENT_PATH.read_text(encoding="utf-8"))


def _load_credentials():
    """Return refreshed Google credentials, raising _ConfigError if unconnected."""
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    if not YT_TOKEN_PATH.exists():
        raise _ConfigError(
            "YouTube account not connected. Click 'Connect YouTube' in the "
            "dashboard or run: python -m src.uploaders.youtube --connect"
        )
    creds = Credentials.from_authorized_user_file(str(YT_TOKEN_PATH), YT_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YT_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        raise _ConfigError("YouTube credentials invalid; reconnect.")
    return creds


def connect_account(headless: bool = False) -> str:
    """Run the loopback OAuth flow. Saves token, returns the connected channel name."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    cfg = _load_client_config()  # raises if missing
    flow = InstalledAppFlow.from_client_config(cfg, YT_SCOPES)
    if headless:
        # Console fallback when there's no browser handy
        creds = flow.run_console()  # type: ignore[attr-defined]
    else:
        creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
    YT_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    YT_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    name = _channel_title_from_creds(creds) or "(unknown channel)"
    logger.success(f"connected YouTube channel: {name}")
    return name


def _channel_title_from_creds(creds) -> str | None:
    try:
        from googleapiclient.discovery import build  # type: ignore
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if items:
            return items[0]["snippet"]["title"]
    except Exception as e:
        logger.debug(f"channel lookup failed: {e}")
    return None


def is_connected() -> bool:
    return YT_TOKEN_PATH.exists()


def upload(
    file_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",  # private | unlisted | public
    category_id: str = "22",  # People & Blogs
    made_for_kids: bool = False,
) -> dict:
    """Upload a video file. Returns {'id': videoId, 'url': watch_url}.

    privacy_status defaults to 'private' so the user can review before going
    public. Switch to 'unlisted' or 'public' as you trust the output."""
    from googleapiclient.discovery import build  # type: ignore
    from googleapiclient.http import MediaFileUpload  # type: ignore
    from googleapiclient.errors import HttpError  # type: ignore

    creds = _load_credentials()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    # YouTube limits: title 100 chars, description 5000, tags <500 chars total
    title_safe = (title or "Clip")[:100]
    desc_safe = (description or "")[:4900]
    tags_clean = [t.strip().lstrip("#") for t in (tags or []) if t and t.strip()]
    # Trim total tag string length
    total = 0
    capped: list[str] = []
    for t in tags_clean:
        if total + len(t) + 1 > 480:
            break
        capped.append(t)
        total += len(t) + 1

    body = {
        "snippet": {
            "title": title_safe,
            "description": desc_safe,
            "tags": capped,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info(f"uploading to YouTube: {Path(file_path).name} ({privacy_status})")
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"  upload {int(status.progress() * 100)}%")
    except HttpError as e:
        raise RuntimeError(f"YouTube API error: {e._get_reason()}") from e

    vid = response["id"]
    url = f"https://youtu.be/{vid}"
    logger.success(f"uploaded -> {url}")
    return {"id": vid, "url": url, "privacy": privacy_status}


if __name__ == "__main__":  # CLI
    import sys
    if "--connect" in sys.argv:
        try:
            name = connect_account()
            print(f"\nConnected YouTube channel: {name}")
            print(f"Token saved to {YT_TOKEN_PATH}")
        except _ConfigError as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            sys.exit(1)
    elif "--upload" in sys.argv:
        if len(sys.argv) < 4:
            print("usage: python -m src.uploaders.youtube --upload <video_path> <title> [description]")
            sys.exit(1)
        path = sys.argv[2]
        title = sys.argv[3]
        desc = sys.argv[4] if len(sys.argv) > 4 else ""
        result = upload(path, title=title, description=desc)
        print(json.dumps(result, indent=2))
    else:
        print("usage: python -m src.uploaders.youtube --connect")
        print("       python -m src.uploaders.youtube --upload <path> <title> [desc]")
        sys.exit(1)
