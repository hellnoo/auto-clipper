import base64
import os
import re
import subprocess
from pathlib import Path
from loguru import logger
import requests
import yt_dlp
from . import config

# Cookies for sites that block datacenter IPs (YouTube on HF Spaces, etc.).
# Resolution order:
#   1. YT_COOKIES_FILE env var (explicit path)
#   2. YT_COOKIES_B64 env var (base64-encoded contents, decoded to /tmp/cookies.txt)
#   3. Common fallback paths
_COOKIES_CACHE: str | None | bool = False  # False = unresolved, None = none found, str = path


def _resolve_cookies_file() -> str | None:
    global _COOKIES_CACHE
    if _COOKIES_CACHE is not False:
        return _COOKIES_CACHE  # type: ignore[return-value]

    explicit = os.getenv("YT_COOKIES_FILE", "").strip()
    if explicit and Path(explicit).exists():
        _COOKIES_CACHE = explicit
        return explicit

    b64 = os.getenv("YT_COOKIES_B64", "").strip()
    if b64:
        path = "/tmp/cookies.txt"
        try:
            Path(path).write_bytes(base64.b64decode(b64))
            logger.info("decoded YT_COOKIES_B64 -> /tmp/cookies.txt")
            _COOKIES_CACHE = path
            return path
        except Exception as e:
            logger.warning(f"YT_COOKIES_B64 decode failed: {e}")

    for p in ("/data/cookies.txt", "/app/cookies.txt", "cookies.txt"):
        if Path(p).exists():
            _COOKIES_CACHE = p
            return p

    _COOKIES_CACHE = None
    return None


def _base_opts() -> dict:
    opts: dict = {
        "format": f"bestvideo[height<={config.VIDEO_QUALITY}]+bestaudio/best[height<={config.VIDEO_QUALITY}]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(config.RAW_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
        logger.info(f"using cookies: {cookies}")
    return opts


# Try multiple YouTube player clients — datacenter IPs get blocked on default 'web'
# but sometimes 'mweb' / 'tv' / 'ios' slip through. Order = cheapest first.
YT_CLIENT_FALLBACKS = [
    None,  # default
    {"youtube": {"player_client": ["mweb"]}},
    {"youtube": {"player_client": ["tv"]}},
    {"youtube": {"player_client": ["ios"]}},
    {"youtube": {"player_client": ["android"]}},
]


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _yt_video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _ffprobe_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


# cobalt.tools fallback — used when yt-dlp fails (typically YouTube on datacenter IPs).
# Public instance often rate-limits or requires auth; allow override via env.
def _cobalt_fallback(url: str) -> dict:
    api_url = os.getenv("COBALT_API_URL", "https://api.cobalt.tools").rstrip("/")
    logger.info(f"trying cobalt fallback at {api_url}")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key = os.getenv("COBALT_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    payload = {
        "url": url,
        "videoQuality": str(config.VIDEO_QUALITY),
        "filenameStyle": "basic",
        "downloadMode": "auto",
    }
    r = requests.post(api_url, json=payload, headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"cobalt HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    status = data.get("status", "")
    if status not in ("tunnel", "redirect", "stream"):
        err = data.get("error", {})
        msg = err.get("code") if isinstance(err, dict) else (data.get("text") or status)
        raise RuntimeError(f"cobalt status={status}: {msg}")

    media_url = data.get("url")
    if not media_url:
        raise RuntimeError(f"cobalt: missing url in response keys={list(data)}")

    vid_id = _yt_video_id(url) or "cobalt"
    out_path = config.RAW_DIR / f"{vid_id}.mp4"
    logger.info(f"streaming cobalt media -> {out_path.name}")
    with requests.get(media_url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)

    duration = _ffprobe_duration(out_path)
    title = data.get("filename") or vid_id
    logger.success(f"cobalt downloaded: {out_path.name} ({duration:.0f}s)")
    return {
        "path": str(out_path),
        "title": title,
        "duration": duration,
        "id": vid_id,
        "url": url,
    }


def download(url: str) -> dict:
    logger.info(f"Downloading {url}")
    last_err: Exception | None = None
    for i, extractor_args in enumerate(YT_CLIENT_FALLBACKS):
        opts = _base_opts()
        if extractor_args:
            opts["extractor_args"] = extractor_args
            logger.info(f"attempt {i+1}: extractor_args={extractor_args}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if not path.exists():
                guess = next(config.RAW_DIR.glob(f"{info['id']}.*"), None)
                if guess:
                    path = guess
            logger.success(f"Downloaded: {path.name} ({info.get('duration', 0):.0f}s)")
            return {
                "path": str(path),
                "title": info.get("title"),
                "duration": info.get("duration"),
                "id": info.get("id"),
                "url": url,
            }
        except Exception as e:
            last_err = e
            msg = str(e).splitlines()[-1][:200]
            logger.warning(f"attempt {i+1} failed: {msg}")

    if _is_youtube(url):
        try:
            return _cobalt_fallback(url)
        except Exception as e:
            logger.warning(f"cobalt fallback failed: {str(e)[:200]}")
            last_err = e

    raise RuntimeError(
        f"All download paths failed. yt-dlp failed after {len(YT_CLIENT_FALLBACKS)} attempts"
        + (" and cobalt fallback also failed" if _is_youtube(url) else "")
        + f". If on a cloud host (HF Spaces, etc.), YouTube likely blocked the datacenter IP. "
        f"Try setting COBALT_API_URL to a working instance, COBALT_API_KEY if required, "
        f"or upload cookies.txt to /data/cookies.txt. Last error: {last_err}"
    )


if __name__ == "__main__":
    import sys
    print(download(sys.argv[1]))
