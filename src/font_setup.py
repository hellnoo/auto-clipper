"""On-demand font downloader.

We ship 3 free fonts (OFL/Apache licenses) bundled into output/.fonts/ so
libass can render captions + watermark with a TikTok-grade vibe instead of
falling back to Arial. First call downloads them from Google Fonts'
official repo (~150 KB each, one-time).
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from loguru import logger

from . import config


FONTS_DIR = Path(config.ROOT) / "output" / ".fonts"
FONTS: dict[str, str] = {
    # TikTok-classic display font for captions
    "Bangers-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf",
    # Handwritten marker style for watermark
    "PermanentMarker-Regular.ttf":
        "https://github.com/google/fonts/raw/main/apache/permanentmarker/PermanentMarker-Regular.ttf",
    # Narrow bold backup for hooks
    "Anton-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
}


def ensure_fonts() -> Path:
    """Make sure all bundled fonts are on disk. Returns the fonts directory."""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    missing = [(n, u) for n, u in FONTS.items() if not (FONTS_DIR / n).exists()]
    if not missing:
        return FONTS_DIR
    for name, url in missing:
        path = FONTS_DIR / name
        try:
            logger.info(f"downloading font: {name}")
            urllib.request.urlretrieve(url, path)
            if path.stat().st_size < 5_000:
                # Probably an LFS pointer or 404 page — drop it
                path.unlink(missing_ok=True)
                logger.warning(f"  font download too small, skipped: {name}")
        except Exception as e:
            logger.warning(f"  font download failed for {name}: {e}")
    return FONTS_DIR


if __name__ == "__main__":
    d = ensure_fonts()
    print(f"fonts in: {d}")
    for f in sorted(d.glob("*.ttf")):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
