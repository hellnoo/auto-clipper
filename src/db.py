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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);
"""


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
    with conn() as c:
        cur = c.execute(
            """INSERT INTO clips (video_id, idx, start_sec, end_sec, hook, caption, hashtags, score, path, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


if __name__ == "__main__":
    init()
    print(f"DB initialized at {config.DB_PATH}")
