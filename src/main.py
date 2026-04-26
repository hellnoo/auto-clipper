import argparse
import sys
from pathlib import Path
from loguru import logger

from . import config, db, downloader, transcriber, analyzer, editor


def _snap_clip_to_words(clip: dict, words: list[dict], pad_start: float = 0.05, pad_end: float = 0.30) -> dict:
    """Nudge clip start/end to the nearest word boundary so we never cut mid-syllable."""
    if not words:
        return clip
    start, end = float(clip["start"]), float(clip["end"])

    snapped_start = start
    for w in words:
        if w["start"] >= start - 0.5:
            snapped_start = max(0.0, w["start"] - pad_start)
            break

    candidates = [w for w in words if w["end"] <= end + 0.5]
    snapped_end = candidates[-1]["end"] + pad_end if candidates else end

    if snapped_end - snapped_start < 5.0:
        return clip
    return {**clip, "start": snapped_start, "end": snapped_end}


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(config.OUTPUT_DIR / "auto_clipper.log", rotation="10 MB", retention=3, level="DEBUG")


def _analyze_and_render(url: str, source_path: str, transcript: dict) -> int:
    """Run the analyze + render half of the pipeline. Reused by regenerate."""
    clips = analyzer.analyze(transcript)
    video_id = db.upsert_video(url, status="rendering")
    source_stem = Path(source_path).stem
    for i, clip in enumerate(clips):
        out = config.FINAL_DIR / f"{source_stem}_clip{i+1}.mp4"
        clip = _snap_clip_to_words(clip, transcript["words"])
        clip_id = db.insert_clip(video_id, i + 1, {**clip, "status": "rendering"})
        try:
            editor.render_clip(source_path, clip, transcript["words"], out)
            editor.write_caption_file(clip, out.with_suffix(".txt"))
            db.set_clip_status(clip_id, "done", str(out))
            logger.success(f"Clip {i+1}/{len(clips)} -> {out.name}")
        except Exception as e:
            logger.exception(f"clip {i+1} failed")
            db.set_clip_status(clip_id, f"error: {e}")
    db.set_video_status(video_id, "done")
    return len(clips)


def process_url(url: str) -> None:
    db.init()
    vid = db.upsert_video(url, status="downloading")
    try:
        info = downloader.download(url)
        db.upsert_video(url, title=info["title"], path=info["path"], duration=info["duration"], status="transcribing")

        t = transcriber.transcribe(info["path"])
        db.upsert_video(url, language=t["language"], status="analyzing")

        n = _analyze_and_render(url, info["path"], t)
        logger.success(f"Done: {info['title']} -> {n} clips")
    except Exception as e:
        logger.exception("pipeline failed")
        db.set_video_status(vid, "error", str(e))
        raise


def regenerate_video(video_id: int) -> None:
    """Re-run analyze + render on an existing video using cached source + transcript.
    Deletes prior clips for this video first so the dashboard shows the fresh batch."""
    db.init()
    v = db.get_video(video_id)
    if not v:
        raise ValueError(f"video {video_id} not found")
    source_path = v.get("path")
    if not source_path or not Path(source_path).exists():
        raise FileNotFoundError(f"source mp4 missing for video {video_id}: {source_path}")

    db.set_video_status(video_id, "analyzing")
    # Wipe old clip rows so the new batch isn't appended.
    with db.conn() as c:
        c.execute("DELETE FROM clips WHERE video_id=?", (video_id,))

    t = transcriber.transcribe(source_path)
    try:
        n = _analyze_and_render(v["url"], source_path, t)
        logger.success(f"Regenerated: {v['title'] or v['url']} -> {n} clips")
    except Exception as e:
        logger.exception("regenerate failed")
        db.set_video_status(video_id, "error", str(e))
        raise


def process_batch(batch_file: str) -> None:
    urls = [l.strip() for l in Path(batch_file).read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    logger.info(f"Batch: {len(urls)} urls")
    for url in urls:
        try:
            process_url(url)
        except Exception:
            logger.error(f"skipping {url}")


def main() -> None:
    setup_logging()
    editor.check_ffmpeg()

    ap = argparse.ArgumentParser(prog="auto-clipper")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="single video URL")
    g.add_argument("--batch", help="path to file with one URL per line")
    args = ap.parse_args()

    if args.url:
        process_url(args.url)
    else:
        process_batch(args.batch)


if __name__ == "__main__":
    main()
