"""Speaker diarization via speechbrain (no HF gate, no token needed).

Uses Whisper segments as the VAD result, embeds each with speechbrain's
ECAPA-TDNN encoder (the open spkrec-ecapa-voxceleb checkpoint, downloads
without auth), then clusters via Agglomerative + auto-detects speaker
count via silhouette score.

Quality target: ~75-85% of pyannote's accuracy on dialogue podcasts —
good enough to color captions per speaker reliably for the common
2-3 speaker case.
"""
from __future__ import annotations

import json
from pathlib import Path
from loguru import logger

from . import config


_encoder = None  # None = unloaded, False = tried + failed, else encoder
_torch = None
_torchaudio = None
_np = None


def _diarize_cache_path(audio_path: str) -> Path:
    p = Path(audio_path)
    return p.with_suffix(p.suffix + ".diarize.json")


def _load_encoder():
    global _encoder, _torch, _torchaudio, _np
    if _encoder is False:
        return None
    if _encoder is not None:
        return _encoder

    try:
        import torch  # type: ignore
        import torchaudio  # type: ignore
        import numpy as np  # type: ignore
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore
    except ImportError as e:
        logger.warning(
            f"diarization stack not installed ({e}). "
            "Install with: pip install torch torchaudio speechbrain scikit-learn"
        )
        _encoder = False
        return None

    _torch = torch
    _torchaudio = torchaudio
    _np = np

    cache_dir = Path(__file__).resolve().parent.parent / "output" / ".models" / "spkrec"
    cache_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    base_kwargs = dict(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(cache_dir),
        run_opts={"device": device},
    )
    # Windows without Developer Mode (or admin) can't create symlinks.
    # speechbrain 1.x lets us force a copy via local_strategy="copy".
    # Try the newer API first; fall back if the kwarg isn't recognised.
    encoder = None
    last_err = None
    for kw in (
        {**base_kwargs, "local_strategy": "copy"},
        {**base_kwargs, "huggingface_cache_dir": str(cache_dir)},
        base_kwargs,
    ):
        try:
            encoder = EncoderClassifier.from_hparams(**kw)
            break
        except TypeError as e:
            last_err = e  # unknown kwarg; try next variant
            continue
        except OSError as e:
            if getattr(e, "winerror", None) == 1314 or "1314" in str(e):
                last_err = e
                logger.warning(
                    "speechbrain symlink blocked by Windows. Will copy instead "
                    "or fall back to next strategy..."
                )
                continue
            last_err = e
            break
        except Exception as e:
            last_err = e
            break

    if encoder is None:
        logger.warning(
            f"failed to load speechbrain encoder: {last_err}. "
            "If this is WinError 1314, enable Windows Developer Mode "
            "(Settings -> System -> For developers -> Developer Mode ON) "
            "or run the launcher as administrator once to seed the cache."
        )
        _encoder = False
        return None
    _encoder = encoder
    logger.info(f"speechbrain ECAPA encoder loaded on {device}")

    return _encoder


def _load_audio_16k_mono(audio_path: str):
    """Returns (waveform_tensor, sample_rate) at 16 kHz mono."""
    waveform, sr = _torchaudio.load(audio_path)
    if waveform.shape[0] > 1:  # stereo -> mono
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != 16000:
        waveform = _torchaudio.transforms.Resample(sr, 16000)(waveform)
        sr = 16000
    return waveform, sr


def diarize(
    audio_path: str,
    transcript: dict | None = None,
    expected_speakers: int | None = None,
) -> list[dict] | None:
    """Returns list of {start, end, speaker} or None when diarization can't run.

    Requires `transcript['segments']` — we embed each Whisper segment instead
    of running our own VAD."""
    cache = _diarize_cache_path(audio_path)
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, list):
                logger.success(f"loaded cached diarization: {cache.name}")
                return data
        except Exception as e:
            logger.warning(f"diarize cache read failed ({e}), re-running")

    if not transcript or not transcript.get("segments"):
        logger.warning("diarize: no transcript segments to embed, skipping")
        return None

    encoder = _load_encoder()
    if encoder is None:
        return None

    segments = transcript["segments"]
    logger.info(f"diarize: embedding {len(segments)} Whisper segments...")

    try:
        waveform, sr = _load_audio_16k_mono(audio_path)
    except Exception as e:
        logger.warning(f"diarize: audio load failed ({e})")
        return None

    embeddings: list = []
    valid_idx: list[int] = []
    min_len = int(sr * 0.5)  # need >= 0.5s of audio per segment to embed

    for i, seg in enumerate(segments):
        s = int(seg["start"] * sr)
        e = int(seg["end"] * sr)
        chunk = waveform[:, s:e]
        if chunk.shape[1] < min_len:
            continue
        try:
            with _torch.no_grad():
                emb = encoder.encode_batch(chunk).squeeze().detach().cpu().numpy()
            embeddings.append(emb)
            valid_idx.append(i)
        except Exception as ex:
            logger.debug(f"embed seg {i} failed: {ex}")

    if len(embeddings) < 2:
        logger.warning("diarize: <2 embeddable segments, skipping")
        return None

    X = _np.stack(embeddings)

    try:
        from sklearn.cluster import AgglomerativeClustering  # type: ignore
        from sklearn.metrics import silhouette_score  # type: ignore
    except ImportError:
        logger.warning("scikit-learn missing; falling back to single-speaker labeling")
        labels = _np.zeros(len(X), dtype=int)
        best_k = 1
    else:
        # Per-call override beats global config beats auto.
        if expected_speakers is not None and expected_speakers > 0:
            forced = int(expected_speakers)
        else:
            forced = max(0, int(config.EXPECTED_SPEAKERS or 0))
        if forced >= 1:
            # User-provided hint — skip the silhouette search, just trust it.
            k_eff = min(forced, len(X))
            if k_eff <= 1:
                labels = _np.zeros(len(X), dtype=int)
                best_k = 1
            else:
                clusterer = AgglomerativeClustering(
                    n_clusters=k_eff, metric="cosine", linkage="average"
                )
                labels = clusterer.fit_predict(X)
                best_k = k_eff
            logger.info(f"diarize: EXPECTED_SPEAKERS={forced} → forcing k={best_k}")
        else:
            # Auto-detect: try k=2..6 and prefer the highest silhouette.
            # Threshold tuned low because 3+ speaker silhouettes are naturally weaker.
            best_k = 1
            best_score = -2.0
            best_labels = _np.zeros(len(X), dtype=int)
            max_k = min(6, len(X) - 1)
            scores: list[tuple[int, float]] = []
            for k in range(2, max_k + 1):
                try:
                    clusterer = AgglomerativeClustering(
                        n_clusters=k, metric="cosine", linkage="average"
                    )
                    labels_k = clusterer.fit_predict(X)
                    if len(set(labels_k)) < k:
                        continue
                    score = silhouette_score(X, labels_k, metric="cosine")
                    scores.append((k, score))
                    if score > best_score:
                        best_score = score
                        best_k = k
                        best_labels = labels_k
                except Exception:
                    continue
            if best_score < 0.05:
                best_k = 1
                best_labels = _np.zeros(len(X), dtype=int)
                logger.info(f"diarize: silhouette={best_score:.3f} → mono speaker (set EXPECTED_SPEAKERS=N to force)")
            else:
                logger.info(
                    f"diarize: best silhouette={best_score:.3f} at k={best_k} "
                    f"(all: {scores}). Override with EXPECTED_SPEAKERS=N if wrong."
                )
            labels = best_labels

    # Map cluster ids back to segments. Segments we couldn't embed inherit
    # from the previous embedded one (or 0).
    idx_to_speaker = {valid_idx[i]: int(labels[i]) for i in range(len(labels))}
    last_spk = idx_to_speaker.get(valid_idx[0], 0)

    raw: list[dict] = []
    for i, seg in enumerate(segments):
        if i in idx_to_speaker:
            last_spk = idx_to_speaker[i]
        raw.append({
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "speaker": f"SPEAKER_{last_spk:02d}",
        })

    # Merge consecutive same-speaker segments separated by < 1s into single turns.
    turns: list[dict] = []
    for a in raw:
        if turns and turns[-1]["speaker"] == a["speaker"] and a["start"] - turns[-1]["end"] < 1.0:
            turns[-1]["end"] = a["end"]
        else:
            turns.append(dict(a))

    speakers = sorted({t["speaker"] for t in turns})
    logger.success(f"diarize: {len(turns)} turns across {len(speakers)} speakers ({', '.join(speakers)})")

    try:
        cache.write_text(json.dumps(turns), encoding="utf-8")
    except Exception as e:
        logger.warning(f"diarize cache write failed: {e}")

    return turns


def assign_speakers(words: list[dict], turns: list[dict]) -> None:
    """In-place: tag each word with 'speaker' based on which turn its midpoint
    falls in. Falls back to nearest turn for words landing in tiny gaps."""
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
            nearest = min(
                sorted_turns,
                key=lambda t: min(abs(mid - t["start"]), abs(mid - t["end"])),
            )
            match = nearest["speaker"]
        w["speaker"] = match


if __name__ == "__main__":  # smoke test
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.diarizer <audio_path>")
        sys.exit(1)
    # Standalone test needs a transcript; fake it with one big segment
    fake = {"segments": [{"start": 0.0, "end": 60.0}]}
    out = diarize(sys.argv[1], fake)
    if out:
        for t in out[:10]:
            print(f"{t['start']:7.2f}-{t['end']:7.2f}  {t['speaker']}")
