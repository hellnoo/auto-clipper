import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    path TEXT,
    language TEXT,
    duration REAL,
    status TEXT DEFAULT 'pending',
    error TEXT,
    expected_speakers INTEGER DEFAULT 0,
    watermark TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    hook TEXT,
    caption TEXT,
    hashtags TEXT,
    score REAL,
    path TEXT,
    status TEXT DEFAULT 'pending',
    emojis TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);
"""

# Migrations for older DBs that pre-date a column. Each entry is a column name
# we expect, paired with the ALTER TABLE statement to add it.
_MIGRATIONS = [
    ("clips", "emojis", "ALTER TABLE clips ADD COLUMN emojis TEXT"),
    ("videos", "expected_speakers", "ALTER TABLE videos ADD COLUMN expected_speakers INTEGER DEFAULT 0"),
    ("videos", "watermark", "ALTER TABLE videos ADD COLUMN watermark TEXT"),
]


_UPLOAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    platform TEXT NOT NULL,                 -- 'youtube' | 'instagram' | 'tiktok'
    status TEXT DEFAULT 'pending',          -- pending|uploading|done|error
    remote_id TEXT,
    remote_url TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(clip_id) REFERENCES clips(id)
);
CREATE INDEX IF NOT EXISTS idx_uploads_clip ON clip_uploads(clip_id);
"""


def insert_upload(clip_id: int, platform: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO clip_uploads (clip_id, platform, status) VALUES (?, ?, 'pending')",
            (clip_id, platform),
        )
        return cur.lastrowid


def set_upload_status(upload_id: int, status: str, *, remote_id: str | None = None,
                      remote_url: str | None = None, error: str | None = None) -> None:
    with conn() as c:
        c.execute(
            "UPDATE clip_uploads SET status=?, remote_id=?, remote_url=?, error=?, "
            "updated_at=? WHERE id=?",
            (status, remote_id, remote_url, error, datetime.utcnow().isoformat(), upload_id),
        )


def list_uploads_for_clip(clip_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM clip_uploads WHERE clip_id=? ORDER BY id DESC",
            (clip_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def _apply_migrations(c: sqlite3.Connection) -> None:
    for table, col, sql in _MIGRATIONS:
        cols = {row["name"] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            c.execute(sql)


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        c.executescript(_UPLOAD_SCHEMA)
        _apply_migrations(c)


def upsert_video(url: str, **fields: Any) -> int:
    with conn() as c:
        row = c.execute("SELECT id FROM videos WHERE url=?", (url,)).fetchone()
        if row:
            vid = row["id"]
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(
                    f"UPDATE videos SET {sets}, updated_at=? WHERE id=?",
                    (*fields.values(), datetime.utcnow().isoformat(), vid),
                )
            return vid
        cols = ["url"] + list(fields.keys())
        placeholders = ",".join(["?"] * len(cols))
        cur = c.execute(
            f"INSERT INTO videos ({','.join(cols)}) VALUES ({placeholders})",
            (url, *fields.values()),
        )
        return cur.lastrowid


def set_video_status(video_id: int, status: str, error: str | None = None) -> None:
    with conn() as c:
        c.execute(
            "UPDATE videos SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, datetime.utcnow().isoformat(), video_id),
        )


def insert_clip(video_id: int, idx: int, data: dict) -> int:
    emojis_json = json.dumps(data.get("emojis") or []) if data.get("emojis") else None
    with conn() as c:
        cur = c.execute(
            """INSERT INTO clips (video_id, idx, start_sec, end_sec, hook, caption, hashtags, score, path, status, emojis)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                idx,
                data["start"],
                data["end"],
                data.get("hook"),
                data.get("caption"),
                ",".join(data.get("hashtags") or []),
                data.get("score"),
                data.get("path"),
                data.get("status", "pending"),
                emojis_json,
            ),
        )
        return cur.lastrowid


def set_clip_status(clip_id: int, status: str, path: str | None = None) -> None:
    with conn() as c:
        if path:
            c.execute("UPDATE clips SET status=?, path=? WHERE id=?", (status, path, clip_id))
        else:
            c.execute("UPDATE clips SET status=? WHERE id=?", (status, clip_id))


def list_videos() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def list_clips(video_id: int | None = None) -> list[dict]:
    with conn() as c:
        if video_id:
            rows = c.execute("SELECT * FROM clips WHERE video_id=? ORDER BY idx", (video_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM clips ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_video(video_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return dict(r) if r else None


def delete_video(video_id: int, also_files: bool = False) -> dict:
    """Delete a video, all its clips, and all upload records.
    If also_files=True, also remove the rendered clip files + caption .txt
    from output/final/ AND the source mp4 + transcript caches in output/raw/.
    Returns a summary of what got removed."""
    from pathlib import Path
    deleted_files: list[str] = []
    with conn() as c:
        v = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not v:
            return {"deleted": False, "reason": "not found"}
        v = dict(v)
        clips = c.execute("SELECT * FROM clips WHERE video_id=?", (video_id,)).fetchall()
        clip_paths = [dict(r).get("path") for r in clips]
        # Cascade-delete in DB
        clip_ids = [dict(r)["id"] for r in clips]
        if clip_ids:
            placeholders = ",".join("?" * len(clip_ids))
            c.execute(f"DELETE FROM clip_uploads WHERE clip_id IN ({placeholders})", clip_ids)
        c.execute("DELETE FROM clips WHERE video_id=?", (video_id,))
        c.execute("DELETE FROM videos WHERE id=?", (video_id,))

    if also_files:
        # Final clips + their .txt caption files + .ass subtitle files
        for p in clip_paths:
            if not p:
                continue
            for path in (Path(p), Path(p).with_suffix(".txt"), Path(p).with_suffix(".ass")):
                try:
                    if path.exists():
                        path.unlink()
                        deleted_files.append(path.name)
                except Exception:
                    pass
        # Source mp4 + transcript caches + diarize cache
        src = v.get("path")
        if src:
            src_path = Path(src)
            if src_path.exists():
                try:
                    src_path.unlink()
                    deleted_files.append(src_path.name)
                except Exception:
                    pass
            # Caches sit next to the source with extra suffixes
            stem = src_path.with_suffix("")
            for ext in (".transcript.tiny.json", ".transcript.base.json",
                        ".transcript.small.json", ".transcript.medium.json",
                        ".transcript.large-v3.json", ".diarize.json"):
                f = Path(str(src_path) + ext.replace(".transcript", ".transcript"))
                if f.exists():
                    try:
                        f.unlink()
                        deleted_files.append(f.name)
                    except Exception:
                        pass

    return {
        "deleted": True,
        "video_id": video_id,
        "clips_removed": len(clip_paths),
        "files_removed": len(deleted_files),
        "files": deleted_files[:20],
    }


def delete_all_done() -> dict:
    """Bulk-delete all videos with status='done' or 'error' (DB only, files preserved)."""
    with conn() as c:
        rows = c.execute("SELECT id FROM videos WHERE status IN ('done','error')").fetchall()
        ids = [dict(r)["id"] for r in rows]
    removed = 0
    for vid in ids:
        r = delete_video(vid, also_files=False)
        if r.get("deleted"):
            removed += 1
    return {"removed": removed}


if __name__ == "__main__":
    init()
    print(f"DB initialized at {config.DB_PATH}")
