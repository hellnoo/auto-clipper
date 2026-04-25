import os
from pathlib import Path
from loguru import logger
import yt_dlp
from . import config

# Optional cookies file for sites that block datacenter IPs (YouTube on HF Spaces, etc.)
COOKIES_FILE = os.getenv("YT_COOKIES_FILE", "/data/cookies.txt")


def _base_opts() -> dict:
    opts: dict = {
        "format": f"bestvideo[height<={config.VIDEO_QUALITY}]+bestaudio/best[height<={config.VIDEO_QUALITY}]",
        "merge_output_format": "mp4",
        "outtmpl": str(config.RAW_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE
        logger.info(f"using cookies: {COOKIES_FILE}")
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
    raise RuntimeError(
        f"yt-dlp failed after {len(YT_CLIENT_FALLBACKS)} attempts. "
        f"If on a cloud host (HF Spaces, etc.), YouTube likely blocked the datacenter IP. "
        f"Upload a cookies.txt to /data/cookies.txt (or set YT_COOKIES_FILE). "
        f"Last error: {last_err}"
    )


if __name__ == "__main__":
    import sys
    print(download(sys.argv[1]))
