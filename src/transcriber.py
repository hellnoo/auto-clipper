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


def transcribe(audio_path: str) -> dict:
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
    return {
        "language": info.language,
        "duration": float(info.duration),
        "segments": segments,
        "words": words_all,
    }


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
