import html
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Queue

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src import config, db


def _esc(s) -> str:
    """HTML-escape any value for safe interpolation into the dashboard."""
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


job_queue: "Queue[tuple[str, str | int]]" = Queue()
_current: dict = {"label": None}


def _worker() -> None:
    from src.main import process_url, regenerate_video, setup_logging
    setup_logging()
    while True:
        kind, payload = job_queue.get()
        label = f"regen vid={payload}" if kind == "regen" else str(payload)
        _current["label"] = label
        try:
            logger.info(f"[worker] start {label}")
            if kind == "url":
                process_url(payload)  # type: ignore[arg-type]
            elif kind == "regen":
                regenerate_video(int(payload))
            else:
                logger.warning(f"[worker] unknown job kind: {kind}")
            logger.success(f"[worker] done {label}")
        except Exception:
            logger.exception(f"[worker] failed {label}")
        finally:
            _current["label"] = None
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
<script>
// Poll /api/videos every 15s; only reload the page if nothing is currently
// playing AND something actually changed (new clip count or status diff).
// Keeps videos from getting yanked mid-playback.
(function() {{
  let lastSig = null;

  function makeSig(data) {{
    return (data.videos || []).map(v =>
      `${{v.id}}:${{v.status || ''}}:${{(v.duration || 0) | 0}}`
    ).join('|') + '||q=' + (data.queue_size || 0) + ':c=' + (data.current || '');
  }}

  function anyPlaying() {{
    return [...document.querySelectorAll('video')].some(v => !v.paused && !v.ended);
  }}

  async function tick() {{
    try {{
      const r = await fetch('/api/videos', {{ cache: 'no-store' }});
      if (!r.ok) return;
      const data = await r.json();
      const sig = makeSig(data);
      if (lastSig === null) {{ lastSig = sig; return; }}
      if (sig === lastSig) return;
      if (anyPlaying()) return;  // user is watching, defer
      location.reload();
    }} catch (e) {{ /* swallow — try again next tick */ }}
  }}

  setInterval(tick, 15000);
  // Also re-check when a video pauses/ends so we don't make the user wait
  // up to 15s extra after they finish watching.
  document.addEventListener('pause', () => setTimeout(tick, 500), true);
  document.addEventListener('ended', () => setTimeout(tick, 500), true);
}})();
</script>
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
        download_name = _esc(Path(c["path"]).name)
        video_tag = f'<video src="{_esc(url)}" controls preload="metadata"></video>'
        download_btn = (
            f'<a href="{_esc(url)}" download="{download_name}" '
            f'style="display:inline-block;padding:4px 10px;background:#063;color:#fff;'
            f'border-radius:4px;text-decoration:none;font-size:11px;font-weight:600">⬇ download</a>'
        )
    else:
        video_tag = '<div style="padding:20px;text-align:center;color:#666">no file yet</div>'
        download_btn = ''
    size_html = f'<span style="font-size:11px;color:#888;margin-left:8px">{_esc(size)}</span>' if size else ''
    tags = " ".join(f"#{_esc(t)}" for t in (c["hashtags"] or "").split(",") if t)

    # Emoji chips (parsed from JSON column)
    emoji_html = ""
    raw_emojis = c.get("emojis") if isinstance(c, dict) else None
    if raw_emojis:
        try:
            arr = json.loads(raw_emojis) if isinstance(raw_emojis, str) else raw_emojis
            if arr:
                chips = "".join(
                    f'<span title="{_esc(e.get("word",""))}" style="font-size:18px;margin-right:4px">{_esc(e.get("emoji",""))}</span>'
                    for e in arr[:8] if isinstance(e, dict)
                )
                emoji_html = f'<div style="margin-top:6px">{chips}</div>'
        except Exception:
            pass

    st = c["status"] or "pending"
    st_class = "done" if st == "done" else ("error" if st.startswith("error") else "running" if "render" in st else "")
    return (
        f'<div class="clip">{video_tag}'
        f'<div class="hook">{_esc(c["hook"])}</div>'
        f'<div class="caption">{_esc(c["caption"])}</div>'
        f'<div class="tags">{tags}</div>'
        f'{emoji_html}'
        f'<div style="margin-top:6px"><span class="status {st_class}">{_esc(st)}</span> '
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

    # Regenerate is only useful when source is on disk
    can_regen = bool(v.get("path")) and Path(v["path"]).exists() if v.get("path") else False
    regen_btn = (
        f'<form method="post" action="/regenerate/{v["id"]}" style="display:inline">'
        f'<button type="submit" style="padding:4px 10px;background:#222;color:#6af;'
        f'border:1px solid #444;border-radius:4px;font-size:11px;cursor:pointer" '
        f'title="Re-run analyze + render using cached source/transcript">↻ regenerate</button>'
        f'</form>'
        if can_regen else ''
    )

    return (
        f'<div class="video">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">'
        f'<h2 style="margin:0">{_esc(v["title"] or v["url"])}</h2>'
        f'{regen_btn}'
        f'</div>'
        f'<div class="meta" style="margin-top:8px">'
        f'<span class="status {st_class}">{_esc(st)}</span> | '
        f'lang: {_esc(v["language"] or "?")} | '
        f'{(v["duration"] or 0):.0f}s | '
        f'<a href="{_esc(v["url"])}" target="_blank" rel="noopener">source</a>'
        f'</div>'
        f'<div class="clips">{clip_html}</div>'
        f'</div>'
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    videos = db.list_videos()
    qsize = job_queue.qsize()
    cur = _current["label"]
    if cur:
        queue_info = f'⚙️  Processing: <b>{_esc(cur)}</b>' + (f' (+{qsize} queued)' if qsize else '')
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
    job_queue.put(("url", url))
    logger.info(f"queued: {url} (qsize={job_queue.qsize()})")
    return RedirectResponse("/", status_code=303)


@app.post("/regenerate/{video_id}")
def regenerate(video_id: int) -> RedirectResponse:
    v = db.get_video(video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if not v.get("path") or not Path(v["path"]).exists():
        raise HTTPException(409, "source mp4 missing on disk; submit the URL again")
    job_queue.put(("regen", video_id))
    logger.info(f"queued regen: video_id={video_id} (qsize={job_queue.qsize()})")
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
