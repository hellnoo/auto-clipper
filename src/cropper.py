"""Face-aware crop helper.

Samples N frames across a clip range, detects the largest face in each,
and returns a normalized horizontal centroid (0.0 = far left, 1.0 = far
right) for ffmpeg to crop around. Falls back to None when no faces or
OpenCV isn't usable, in which case the caller should center-crop.
"""
from __future__ import annotations

from loguru import logger

try:
    import cv2  # type: ignore
    _CV_AVAILABLE = True
except Exception as e:  # pragma: no cover
    logger.warning(f"opencv unavailable, face-aware crop disabled: {e}")
    _CV_AVAILABLE = False
    cv2 = None  # type: ignore


_cascade = None


def _get_cascade():
    global _cascade
    if _cascade is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _cascade = cv2.CascadeClassifier(path)
        if _cascade.empty():
            raise RuntimeError(f"failed to load Haar cascade from {path}")
    return _cascade


def detect_face_center_x(
    video_path: str,
    start_sec: float,
    end_sec: float,
    samples: int = 12,
) -> float | None:
    """Return median horizontal face center across `samples` evenly-spaced frames,
    normalized to [0.0, 1.0]. None if no faces found or cv2 unavailable."""
    if not _CV_AVAILABLE:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        if width <= 0:
            return None
        cascade = _get_cascade()
        duration = max(0.1, end_sec - start_sec)
        centers: list[float] = []
        for i in range(samples):
            t = start_sec + (i + 0.5) * duration / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.15, minNeighbors=5, minSize=(80, 80)
            )
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            cx = (x + w / 2.0) / width
            centers.append(float(cx))
        if not centers:
            return None
        centers.sort()
        median = centers[len(centers) // 2]
        # Clamp to a sane band so we never crop hard against an edge.
        return max(0.20, min(0.80, median))
    finally:
        cap.release()


if __name__ == "__main__":  # smoke test: python -m src.cropper <file> <start> <end>
    import sys
    if len(sys.argv) < 4:
        print("usage: python -m src.cropper <video> <start_sec> <end_sec>")
        sys.exit(1)
    cx = detect_face_center_x(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
    print(f"face center x: {cx}")
