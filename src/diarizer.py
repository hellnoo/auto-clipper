"""Speaker diarization via pyannote-audio.

Lazy-imports pyannote so the rest of the app loads fine when it's not
installed. Caches results next to the source audio so re-runs are instant.
"""
from __future__ import annotations

import json
from pathlib import Path
from loguru import logger

from . import config


_pipeline = None  # None = unloaded, False = tried + failed, else Pipeline instance


def _diarize_cache_path(audio_path: str) -> Path:
    p = Path(audio_path)
    return p.with_suffix(p.suffix + ".diarize.json")


def _load_pipeline():
    global _pipeline
    if _pipeline is False:
        return None
    if _pipeline is not None:
        return _pipeline

    if not config.HF_TOKEN:
        logger.warning(
            "DIARIZE_ENABLED=1 but HF_TOKEN missing. "
            "Get a token at https://huggingface.co/settings/tokens, "
            "and accept terms at https://huggingface.co/pyannote/speaker-diarization-3.1 "
            "+ https://huggingface.co/pyannote/segmentation-3.0"
        )
        _pipeline = False
        return None

    try:
        from pyannote.audio import Pipeline  # type: ignore
    except ImportError:
        logger.warning(
            "pyannote.audio not installed. Skipping diarization. "
            "Install with: pip install \"pyannote.audio>=3.1\""
        )
        _pipeline = False
        return None

    try:
        pipe = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=config.HF_TOKEN,
        )
    except Exception as e:
        logger.warning(
            f"Failed to load pyannote pipeline ({e}). "
            "Did you accept the model terms at "
            "https://huggingface.co/pyannote/speaker-diarization-3.1 ?"
        )
        _pipeline = False
        return None

    # Move to GPU if available — diarization is much faster.
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            pipe.to(torch.device("cuda"))
            logger.info("pyannote diarization: using CUDA")
        else:
            logger.info("pyannote diarization: using CPU (slower)")
    except Exception:
        pass

    _pipeline = pipe
    return _pipeline


def diarize(audio_path: str) -> list[dict] | None:
    """Returns list of {start, end, speaker} or None if unavailable.
    Caches result to <audio>.diarize.json for instant re-use."""
    cache = _diarize_cache_path(audio_path)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, list):
                logger.success(f"loaded cached diarization: {cache.name}")
                return data
        except Exception as e:
            logger.warning(f"diarize cache read failed ({e}), re-running")

    pipe = _load_pipeline()
    if pipe is None:
        return None

    logger.info(f"diarizing {Path(audio_path).name} (this can take a minute)...")
    try:
        result = pipe(audio_path)
    except Exception as e:
        logger.warning(f"diarize failed: {e}")
        return None

    turns: list[dict] = []
    for turn, _, spk in result.itertracks(yield_label=True):
        turns.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": str(spk),
        })

    speakers = sorted({t["speaker"] for t in turns})
    logger.success(f"diarized: {len(turns)} turns across {len(speakers)} speakers ({', '.join(speakers)})")

    try:
        cache.write_text(json.dumps(turns), encoding="utf-8")
    except Exception as e:
        logger.warning(f"diarize cache write failed: {e}")

    return turns


def assign_speakers(words: list[dict], turns: list[dict]) -> None:
    """In-place: tag each word with 'speaker' based on which turn its midpoint falls in.
    Falls back to nearest turn for words landing in gaps."""
    if not words or not turns:
        return
    sorted_turns = sorted(turns, key=lambda t: t["start"])
    for w in words:
        mid = (w["start"] + w["end"]) / 2.0
        match = None
        for t in sorted_turns:
            if t["start"] <= mid <= t["end"]:
                match = t["speaker"]
                break
        if match is None:
            # nearest turn by edge distance
            nearest = min(
                sorted_turns,
                key=lambda t: min(abs(mid - t["start"]), abs(mid - t["end"])),
            )
            match = nearest["speaker"]
        w["speaker"] = match


if __name__ == "__main__":  # smoke test: python -m src.diarizer <audio>
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.diarizer <audio_path>")
        sys.exit(1)
    out = diarize(sys.argv[1])
    if out:
        for t in out[:10]:
            print(f"{t['start']:7.2f}-{t['end']:7.2f}  {t['speaker']}")
