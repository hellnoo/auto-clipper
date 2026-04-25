import json
from pathlib import Path
from loguru import logger
from faster_whisper import WhisperModel
from . import config

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        logger.info(f"Loading Whisper model '{config.WHISPER_MODEL}' ({config.WHISPER_DEVICE}/{config.WHISPER_COMPUTE})")
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
    return _model


def _cache_path(audio_path: str) -> Path:
    p = Path(audio_path)
    return p.with_suffix(p.suffix + f".transcript.{config.WHISPER_MODEL}.json")


def transcribe(audio_path: str) -> dict:
    cache = _cache_path(audio_path)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            logger.success(f"Loaded cached transcript: {cache.name}")
            return data
        except Exception as e:
            logger.warning(f"cache read failed ({e}), retranscribing")

    model = get_model()
    logger.info(f"Transcribing {Path(audio_path).name}")
    segments_iter, info = model.transcribe(
        audio_path,
        word_timestamps=True,
        vad_filter=True,
        beam_size=1,
    )
    segments: list[dict] = []
    words_all: list[dict] = []
    for seg in segments_iter:
        seg_words = []
        for w in (seg.words or []):
            wd = {"start": float(w.start), "end": float(w.end), "word": w.word.strip()}
            seg_words.append(wd)
            words_all.append(wd)
        segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip(),
            "words": seg_words,
        })
    logger.success(f"Transcribed: lang={info.language}, {len(segments)} segments, {len(words_all)} words")
    result = {
        "language": info.language,
        "duration": float(info.duration),
        "segments": segments,
        "words": words_all,
    }
    try:
        cache.write_text(json.dumps(result), encoding="utf-8")
        logger.info(f"cached transcript -> {cache.name}")
    except Exception as e:
        logger.warning(f"cache write failed: {e}")
    return result


def to_srt(segments: list[dict]) -> str:
    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text']}\n")
    return "\n".join(out)


if __name__ == "__main__":
    import sys, json
    r = transcribe(sys.argv[1])
    print(json.dumps({"language": r["language"], "n_segments": len(r["segments"])}, indent=2))
