import threading
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Queue

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src import config, db


job_queue: "Queue[str]" = Queue()
_current: dict = {"url": None}


def _worker() -> None:
    from src.main import process_url, setup_logging
    setup_logging()
    while True:
        url = job_queue.get()
        _current["url"] = url
        try:
            logger.info(f"[worker] start {url}")
            process_url(url)
            logger.success(f"[worker] done {url}")
        except Exception:
            logger.exception(f"[worker] failed {url}")
        finally:
            _current["url"] = None
            job_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    t = threading.Thread(target=_worker, daemon=True, name="auto-clipper-worker")
    t.start()
    logger.info("worker thread started")
    yield


app = FastAPI(title="Auto-Clipper Dashboard", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(config.OUTPUT_DIR)), name="media")


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Auto-Clipper</title>
<meta http-equiv="refresh" content="15">
<style>
 body{{font-family:system-ui,sans-serif;margin:0;background:#111;color:#eee}}
 header{{padding:16px 24px;background:#000;border-bottom:1px solid #333;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}}
 h1{{margin:0;font-size:20px}}
 main{{padding:24px;max-width:1200px;margin:0 auto}}
 form{{display:flex;gap:8px;flex-wrap:wrap;flex:1;min-width:300px;max-width:700px}}
 input[type=url]{{flex:1;padding:10px;background:#222;color:#eee;border:1px solid #444;border-radius:4px;font-size:14px;min-width:200px}}
 button{{padding:10px 18px;background:#063;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:14px;font-weight:600}}
 button:hover{{background:#085}}
 .queue{{font-size:12px;color:#888;margin:0 24px 16px}}
 .queue b{{color:#6f9}}
 .video{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin-bottom:16px}}
 .video h2{{margin:0 0 8px;font-size:16px}}
 .meta{{font-size:12px;color:#888;margin-bottom:12px}}
 .clips{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}}
 .clip{{background:#222;border:1px solid #333;border-radius:6px;padding:10px}}
 .clip video{{width:100%;border-radius:4px;background:#000}}
 .hook{{font-weight:600;margin:8px 0 4px;font-size:13px}}
 .caption{{font-size:12px;color:#bbb}}
 .tags{{font-size:11px;color:#6af;margin-top:6px}}
 .status{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#333}}
 .status.done{{background:#063;color:#6f9}}
 .status.error{{background:#600;color:#f99}}
 .status.running{{background:#640;color:#fc6}}
 .empty{{color:#666;text-align:center;padding:40px}}
 a{{color:#6af}}
</style></head>
<body>
<header>
 <h1>Auto-Clipper</h1>
 <form action="/submit" method="post">
  <input type="url" name="url" required placeholder="https://www.youtube.com/watch?v=..." autocomplete="off">
  <button type="submit">Generate clips</button>
 </form>
</header>
<div class="queue">{queue_info}</div>
<main>{body}</main>
</body></html>
"""


def _media_url(abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    try:
        rel = Path(abs_path).resolve().relative_to(config.OUTPUT_DIR.resolve())
        return f"/media/{rel.as_posix()}"
    except ValueError:
        return None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _file_size(abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    p = Path(abs_path)
    if not p.exists():
        return None
    return _human_size(p.stat().st_size)


def _render_clip(c: dict) -> str:
    url = _media_url(c["path"])
    size = _file_size(c["path"])
    if url:
        download_name = Path(c["path"]).name
        video_tag = f'<video src="{url}" controls preload="metadata"></video>'
        download_btn = (
            f'<a href="{url}" download="{download_name}" '
            f'style="display:inline-block;padding:4px 10px;background:#063;color:#fff;'
            f'border-radius:4px;text-decoration:none;font-size:11px;font-weight:600">⬇ download</a>'
        )
    else:
        video_tag = '<div style="padding:20px;text-align:center;color:#666">no file yet</div>'
        download_btn = ''
    size_html = f'<span style="font-size:11px;color:#888;margin-left:8px">{size}</span>' if size else ''
    tags = " ".join(f"#{t}" for t in (c["hashtags"] or "").split(",") if t)
    st = c["status"] or "pending"
    st_class = "done" if st == "done" else ("error" if st.startswith("error") else "running" if "render" in st else "")
    return (
        f'<div class="clip">{video_tag}'
        f'<div class="hook">{c["hook"] or ""}</div>'
        f'<div class="caption">{c["caption"] or ""}</div>'
        f'<div class="tags">{tags}</div>'
        f'<div style="margin-top:6px"><span class="status {st_class}">{st}</span> '
        f'<span style="font-size:11px;color:#888">score: {(c["score"] or 0):.0f} | {c["start_sec"]:.1f}-{c["end_sec"]:.1f}s</span></div>'
        f'<div style="margin-top:8px;display:flex;align-items:center">{download_btn}{size_html}</div>'
        f'</div>'
    )


def _render_video(v: dict) -> str:
    clips = db.list_clips(v["id"])
    clip_html = "".join(_render_clip(c) for c in clips) or '<div style="color:#666">no clips yet</div>'
    st = v["status"] or "pending"
    terminal = {"done", "error"}
    st_class = "done" if st == "done" else ("error" if st == "error" else "running" if st not in terminal else "")
    return (
        f'<div class="video"><h2>{v["title"] or v["url"]}</h2>'
        f'<div class="meta">'
        f'<span class="status {st_class}">{st}</span> | '
        f'lang: {v["language"] or "?"} | '
        f'{(v["duration"] or 0):.0f}s | '
        f'<a href="{v["url"]}" target="_blank">source</a>'
        f'</div>'
        f'<div class="clips">{clip_html}</div>'
        f'</div>'
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    videos = db.list_videos()
    qsize = job_queue.qsize()
    cur = _current["url"]
    if cur:
        queue_info = f'⚙️  Processing: <b>{cur}</b>' + (f' (+{qsize} queued)' if qsize else '')
    elif qsize:
        queue_info = f'⏳ {qsize} job(s) queued'
    else:
        queue_info = 'Idle. Paste a URL above to generate clips.'

    if not videos:
        return PAGE.format(queue_info=queue_info, body='<div class="empty">No videos yet. Submit a URL above to get started.</div>')
    return PAGE.format(queue_info=queue_info, body="".join(_render_video(v) for v in videos))


@app.post("/submit")
def submit(url: str = Form(...)) -> RedirectResponse:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    job_queue.put(url)
    logger.info(f"queued: {url} (qsize={job_queue.qsize()})")
    return RedirectResponse("/", status_code=303)


@app.get("/api/videos")
def api_videos() -> dict:
    return {"videos": db.list_videos(), "queue_size": job_queue.qsize(), "current": _current["url"]}


@app.get("/api/videos/{video_id}/clips")
def api_clips(video_id: int) -> dict:
    v = db.get_video(video_id)
    if not v:
        raise HTTPException(404)
    return {"video": v, "clips": db.list_clips(video_id)}
