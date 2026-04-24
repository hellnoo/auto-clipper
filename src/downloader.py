from pathlib import Path
from loguru import logger
import yt_dlp
from . import config


def download(url: str) -> dict:
    outtmpl = str(config.RAW_DIR / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": f"bestvideo[height<={config.VIDEO_QUALITY}]+bestaudio/best[height<={config.VIDEO_QUALITY}]",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    logger.info(f"Downloading {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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


if __name__ == "__main__":
    import sys
    r = download(sys.argv[1])
    print(r)
