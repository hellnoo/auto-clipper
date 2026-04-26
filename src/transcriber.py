import json
import os
import sys
from pathlib import Path
from loguru import logger


def _register_cuda_dll_paths() -> None:
    """pip-installed nvidia-cudnn-cu12 / nvidia-cublas-cu12 put their DLLs under
    site-packages/nvidia/<lib>/bin on Windows, but ctranslate2 won't find them
    unless we add those dirs to the DLL search path. Safe no-op if the packages
    or directories don't exist.

    Note: `nvidia` is a PEP 420 namespace package, so it has no __init__.py
    and `nvidia.__file__` is None — we have to walk __path__ instead."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import nvidia  # type: ignore
    except ImportError:
        return
    bases = list(getattr(nvidia, "__path__", []))
    for base_str in bases:
        base = Path(base_str)
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = base / sub / "bin"
            if d.exists():
                try:
                    os.add_dll_directory(str(d))
                except (OSError, FileNotFoundError):
                    pass
                # Also prepend to PATH as a belt-and-braces measure for
                # libraries that bypass the AddDllDirectory API.
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dll_paths()
from faster_whisper import WhisperModel  # noqa: E402

from . import config  # noqa: E402

_model: WhisperModel | None = None


def _resolve_device() -> tuple[str, str]:
    """Pick the best (device, compute_type) pair.

    If WHISPER_DEVICE is left at 'auto' (or empty), try CUDA first and only
    fall back to CPU when CUDA actually fails to load. Float16 on GPU is
    5-10x faster than int8 on CPU for whisper-small / medium.
    """
    dev = (config.WHISPER_DEVICE or "auto").lower()
    comp = config.WHISPER_COMPUTE or ""

    if dev in ("cuda", "auto"):
        # Probe CUDA cheaply by trying to load a tiny model on GPU.
        try:
            probe = WhisperModel("tiny", device="cuda", compute_type=comp or "float16")
            del probe
            return "cuda", (comp or "float16")
        except Exception as e:
            if dev == "cuda":
                # User explicitly asked for CUDA — surface the error.
                raise
            logger.warning(f"CUDA probe failed ({type(e).__name__}: {str(e)[:120]}), falling back to CPU")

    return "cpu", (comp or "int8")


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        device, compute = _resolve_device()
        logger.info(f"Loading Whisper model '{config.WHISPER_MODEL}' ({device}/{compute})")
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=device,
            compute_type=compute,
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
