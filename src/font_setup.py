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
    # Display fonts
    "Bangers-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf",
    "Anton-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
    "Righteous-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/righteous/Righteous-Regular.ttf",
    "Bungee-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/bungee/Bungee-Regular.ttf",
    # Handwritten / scripty for watermark
    "PermanentMarker-Regular.ttf":
        "https://github.com/google/fonts/raw/main/apache/permanentmarker/PermanentMarker-Regular.ttf",
    "Caveat-Bold.ttf":
        "https://github.com/google/fonts/raw/main/ofl/caveat/Caveat%5Bwght%5D.ttf",
    "Pacifico-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/pacifico/Pacifico-Regular.ttf",
    "Lobster-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/lobster/Lobster-Regular.ttf",
    # Retro
    "PressStart2P-Regular.ttf":
        "https://github.com/google/fonts/raw/main/ofl/pressstart2p/PressStart2P-Regular.ttf",
}


# Mapping from friendly dropdown name to (ASS-fontname, recommended-size, recommended-rotation-deg).
# ASS-fontname must match what's INSIDE the .ttf 'Name' table — usually the
# family name without the file extension.
WATERMARK_FONT_PRESETS: dict[str, tuple[str, int, int]] = {
    "Permanent Marker (handwritten)": ("Permanent Marker", 80, -8),
    "Caveat (cursive bold)":           ("Caveat",            96, -6),
    "Pacifico (flowy script)":         ("Pacifico",          84, -10),
    "Lobster (elegant script)":        ("Lobster",           90, -7),
    "Bangers (chunky display)":        ("Bangers",           88, -6),
    "Anton (narrow bold)":             ("Anton",             92, -8),
    "Righteous (block display)":       ("Righteous",         84, -5),
    "Bungee (3D chunky)":              ("Bungee",            78, -4),
    "Press Start 2P (pixel retro)":    ("Press Start 2P",    52,  0),
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
