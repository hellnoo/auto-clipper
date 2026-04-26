"""Face-aware crop helper.

Samples N frames across a clip range, runs face detection on each, and
returns a normalized horizontal centroid (0.0 = far left, 1.0 = far right)
for ffmpeg to crop around. Falls back to None when no faces or OpenCV
isn't usable, in which case the caller should center-crop.

Uses YuNet (OpenCV's bundled state-of-the-art ONNX detector) when the
model file is available; falls back to Haar cascade chain (frontal +
profile) otherwise. YuNet is ~5x more accurate than Haar especially for
angled faces, low light, and partial occlusion.
"""
from __future__ import annotations

from pathlib import Path
from loguru import logger

try:
    import cv2  # type: ignore
    _CV_AVAILABLE = True
except Exception as e:  # pragma: no cover
    logger.warning(f"opencv unavailable, face-aware crop disabled: {e}")
    _CV_AVAILABLE = False
    cv2 = None  # type: ignore

# YuNet ONNX model (300KB) — bundled OpenCV repo asset.
_YUNET_URL = "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
_YUNET_PATH = Path(__file__).resolve().parent.parent / "output" / ".models" / "yunet.onnx"

_yunet = None
_haar_frontal = None
_haar_profile = None
_yunet_input_size = (320, 320)


def _get_yunet():
    """Load YuNet detector, downloading the model on first call. Returns None
    if download fails or cv2.FaceDetectorYN isn't available."""
    global _yunet
    if _yunet is not None:
        return _yunet if _yunet is not False else None
    if not hasattr(cv2, "FaceDetectorYN"):
        _yunet = False
        return None
    if not _YUNET_PATH.exists():
        try:
            import urllib.request
            _YUNET_PATH.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"downloading YuNet face model -> {_YUNET_PATH.name} (~300 KB, one-time)")
            urllib.request.urlretrieve(_YUNET_URL, _YUNET_PATH)
        except Exception as e:
            logger.warning(f"YuNet download failed ({e}); falling back to Haar")
            _yunet = False
            return None
    try:
        _yunet = cv2.FaceDetectorYN.create(
            str(_YUNET_PATH), "", _yunet_input_size, score_threshold=0.6, nms_threshold=0.3, top_k=10
        )
    except Exception as e:
        logger.warning(f"YuNet load failed ({e}); falling back to Haar")
        _yunet = False
        return None
    return _yunet


def _get_haar():
    global _haar_frontal, _haar_profile
    if _haar_frontal is None:
        _haar_frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        _haar_profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    return _haar_frontal, _haar_profile


def _detect_faces_yunet(frame, det) -> list[tuple[float, float, float, float]]:
    """Returns list of (x, y, w, h) in original-frame coords."""
    h, w = frame.shape[:2]
    det.setInputSize((w, h))
    _retval, results = det.detect(frame)
    if results is None:
        return []
    out = []
    for r in results:
        x, y, fw, fh = float(r[0]), float(r[1]), float(r[2]), float(r[3])
        out.append((x, y, fw, fh))
    return out


def _detect_faces_haar(frame, fc, pc) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = list(fc.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(80, 80)))
    # Profile cascade catches side views the frontal one misses.
    profiles = list(pc.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(80, 80)))
    return faces + profiles


def detect_face_center_x(
    video_path: str,
    start_sec: float,
    end_sec: float,
    samples: int = 24,
) -> float | None:
    """Return weighted face center across `samples` evenly-spaced frames,
    normalized to [0.0, 1.0]. Frames in the first ~5s of the clip get
    double weight because the hook is the most critical visual moment.
    None if cv2 unavailable, capture failed, or fewer than 30% of sampled
    frames produced a face (so we don't follow noise to a weird crop)."""
    if not _CV_AVAILABLE:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    yunet = _get_yunet()
    haar_frontal, haar_profile = (None, None)
    if yunet is None:
        haar_frontal, haar_profile = _get_haar()

    try:
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        if width <= 0:
            return None
        duration = max(0.1, end_sec - start_sec)
        # (centroid_x_norm, weight) per detected frame
        weighted: list[tuple[float, float]] = []
        attempts = 0

        for i in range(samples):
            t_offset = (i + 0.5) * duration / samples
            t = start_sec + t_offset
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            attempts += 1
            faces = (
                _detect_faces_yunet(frame, yunet) if yunet is not None
                else _detect_faces_haar(frame, haar_frontal, haar_profile)
            )
            if not faces:
                continue
            # Largest face = most likely speaker
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            cx = (x + fw / 2.0) / width
            # Hook is the first ~5s — weight those frames 2x so the framing
            # locks on whoever's talking at the moment that matters most.
            weight = 2.0 if t_offset <= 5.0 else 1.0
            weighted.append((float(cx), weight))

        if not weighted:
            logger.info(f"face crop: no faces in {attempts} sampled frames, using center")
            return None
        if len(weighted) < max(3, attempts * 0.3):
            logger.info(
                f"face crop: only {len(weighted)}/{attempts} frames detected — not confident, using center"
            )
            return None

        total_w = sum(w for _, w in weighted)
        avg = sum(c * w for c, w in weighted) / total_w
        clamped = max(0.20, min(0.80, avg))
        logger.debug(
            f"face crop: {len(weighted)}/{attempts} hits, weighted avg={avg:.3f} -> {clamped:.3f}"
        )
        return clamped
    finally:
        cap.release()


if __name__ == "__main__":  # smoke test: python -m src.cropper <file> <start> <end>
    import sys
    if len(sys.argv) < 4:
        print("usage: python -m src.cropper <video> <start_sec> <end_sec>")
        sys.exit(1)
    cx = detect_face_center_x(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
    print(f"face center x: {cx}")
