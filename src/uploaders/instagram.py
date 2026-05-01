"""Instagram uploader (unofficial, via instagrapi).

Uses instagrapi (https://github.com/subzeroid/instagrapi) which talks to the
private mobile API. No app-review or HF-style gated approval needed — works
on any personal IG account.

Trade-off: this is unofficial. Heavy automation can trigger checkpoints
(IG asking for verification, temp ban, password reset). For personal-use
clipping (a few uploads/day) it's fine.

Login flow:
  1. User submits username + password to /uploaders/instagram/connect
  2. instagrapi.Client.login(...). If 2FA enabled, raises TwoFactorRequired.
  3. On success, session cookies persist to config/instagram_session.json
  4. Subsequent uploads reuse the session — password never stored on disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from loguru import logger

from .. import config


IG_SESSION_PATH = Path(config.ROOT) / "config" / "instagram_session.json"


class _InstagramError(RuntimeError):
    """Surfaces user-fixable issues with actionable messages."""


_client = None


def _get_client():
    """Return a logged-in instagrapi.Client, raising if no session exists or
    the saved session is stale."""
    global _client
    if _client is not None:
        return _client

    if not IG_SESSION_PATH.exists():
        raise _InstagramError(
            "Instagram not connected. Click 'Connect Instagram' in the dashboard."
        )

    try:
        from instagrapi import Client  # type: ignore
    except ImportError:
        raise _InstagramError(
            "instagrapi not installed. The launcher should pip-install it on "
            "next run.bat. Manual: pip install 'instagrapi>=2.1.0'"
        )

    cl = Client()
    cl.load_settings(str(IG_SESSION_PATH))

    # Verify the session is still valid — instagrapi caches but IG can revoke.
    try:
        cl.get_timeline_feed()
    except Exception as e:
        raise _InstagramError(
            f"IG session expired ({e}). Reconnect via the dashboard."
        )

    _client = cl
    return _client


def is_connected() -> bool:
    return IG_SESSION_PATH.exists()


def disconnect() -> None:
    global _client
    _client = None
    if IG_SESSION_PATH.exists():
        IG_SESSION_PATH.unlink()


def connect_account(
    username: str,
    password: str,
    verification_code: str | None = None,
) -> dict:
    """Log in and persist session cookies. Returns {'username','user_id'} on
    success. Raises with code='2fa_required' if IG asks for a 2FA code —
    caller should resubmit with verification_code set."""
    global _client
    try:
        from instagrapi import Client  # type: ignore
        from instagrapi.exceptions import TwoFactorRequired  # type: ignore
    except ImportError:
        raise _InstagramError(
            "instagrapi not installed. Run the launcher to pip-install it."
        )

    cl = Client()
    try:
        if verification_code:
            cl.login(username, password, verification_code=verification_code)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        err = _InstagramError("2FA code required")
        err.code = "2fa_required"  # type: ignore[attr-defined]
        raise err
    except Exception as e:
        msg = str(e)
        # Common failure modes worth surfacing as actionable errors
        if "challenge" in msg.lower() or "checkpoint" in msg.lower():
            raise _InstagramError(
                "IG checkpoint — open the IG app, complete any pending "
                "verification, then retry."
            )
        if "incorrect" in msg.lower() or "password" in msg.lower():
            raise _InstagramError("Username or password incorrect.")
        raise _InstagramError(f"login failed: {msg}")

    IG_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(str(IG_SESSION_PATH))
    _client = cl
    user_info = {
        "username": cl.username or username,
        "user_id": str(cl.user_id) if cl.user_id else None,
    }
    logger.success(f"connected Instagram: @{user_info['username']}")
    return user_info


def upload_reel(
    file_path: str,
    caption: str = "",
    hashtags: list[str] | None = None,
) -> dict:
    """Upload a 9:16 mp4 as a Reel. Returns {'id','code','url'}.

    IG Reels constraints:
      - Aspect 9:16 (or 4:5, 1:1). Our pipeline outputs 9:16. ✓
      - Length 3-90 s for Reels. Our 30-60 s clips fit. ✓
      - Min resolution 720p. Our 1080×1920 fits. ✓
      - Max file size ~250 MB. Our clips are well under. ✓
    """
    cl = _get_client()
    full_caption = (caption or "").strip()
    tags = [t.strip().lstrip("#") for t in (hashtags or []) if t.strip()]
    if tags:
        # IG hides hashtags in line breaks at end — clean separator
        full_caption = (full_caption + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    full_caption = full_caption[:2200]  # IG hard cap

    logger.info(f"uploading IG Reel: {Path(file_path).name}")
    try:
        media = cl.clip_upload(Path(file_path), caption=full_caption)
    except Exception as e:
        raise _InstagramError(f"upload failed: {e}")

    code = media.code if hasattr(media, "code") else None
    pk = str(media.pk) if hasattr(media, "pk") else None
    url = f"https://www.instagram.com/reel/{code}/" if code else None
    logger.success(f"IG Reel uploaded: {url}")
    return {"id": pk, "code": code, "url": url}


if __name__ == "__main__":
    import sys
    import getpass
    if len(sys.argv) > 1 and sys.argv[1] == "--connect":
        u = input("IG username: ").strip()
        p = getpass.getpass("IG password: ")
        try:
            info = connect_account(u, p)
            print(f"Connected: @{info['username']}")
        except _InstagramError as e:
            if getattr(e, "code", "") == "2fa_required":
                code = input("2FA code: ").strip()
                info = connect_account(u, p, verification_code=code)
                print(f"Connected: @{info['username']}")
            else:
                print(f"ERROR: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        print("usage: python -m src.uploaders.instagram --connect")
