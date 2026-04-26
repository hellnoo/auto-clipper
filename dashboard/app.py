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
<html lang="en"><head><meta charset="utf-8"><title>Auto-Clipper</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
 *{{box-sizing:border-box}}
 :root{{
   --bg:#0a0a0f; --surface:#13131a; --surface2:#1c1c25; --border:#26262f;
   --text:#e8e8f0; --muted:#7d7d8c; --dim:#525260;
   --cyan:#22d3ee; --magenta:#e879f9; --yellow:#facc15; --green:#22c55e; --red:#ef4444; --orange:#fb923c;
   --grad: linear-gradient(135deg,#22d3ee 0%,#a78bfa 50%,#e879f9 100%);
 }}
 html,body{{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:'Inter',system-ui,-apple-system,sans-serif;font-feature-settings:'cv02','cv03','cv04','cv11';-webkit-font-smoothing:antialiased}}
 body{{min-height:100vh;background:radial-gradient(ellipse 80% 50% at 50% -20%,rgba(34,211,238,0.10),transparent 70%),radial-gradient(ellipse 60% 50% at 80% 100%,rgba(232,121,249,0.07),transparent 60%),var(--bg)}}
 a{{color:var(--cyan);text-decoration:none}}
 a:hover{{color:var(--text)}}
 button{{font-family:inherit;cursor:pointer;border:0}}

 /* HEADER */
 header{{padding:20px 32px;border-bottom:1px solid var(--border);background:rgba(10,10,15,0.7);backdrop-filter:blur(20px);position:sticky;top:0;z-index:50;display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap}}
 .brand{{display:flex;align-items:center;gap:12px;font-weight:800;font-size:18px;letter-spacing:-0.02em}}
 .brand-mark{{width:32px;height:32px;border-radius:8px;background:var(--grad);display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 0 24px rgba(168,139,250,0.35)}}
 .brand-text{{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}}
 .brand-by{{color:var(--dim);font-weight:500;font-size:12px;margin-left:4px}}
 form.submit{{display:flex;gap:0;flex:1;max-width:640px;min-width:280px;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;transition:border-color 0.2s,box-shadow 0.2s}}
 form.submit:focus-within{{border-color:var(--cyan);box-shadow:0 0 0 4px rgba(34,211,238,0.10)}}
 form.submit input{{flex:1;padding:12px 16px;background:transparent;border:0;color:var(--text);font-size:14px;font-family:inherit;outline:none}}
 form.submit input::placeholder{{color:var(--dim)}}
 form.submit button{{padding:12px 20px;background:var(--grad);color:#0a0a0f;font-weight:700;font-size:13px;letter-spacing:0.02em;text-transform:uppercase;transition:opacity 0.2s}}
 form.submit button:hover{{opacity:0.9}}

 /* QUEUE STRIP */
 .queue{{padding:14px 32px;font-size:13px;color:var(--muted);font-family:'JetBrains Mono',monospace;display:flex;align-items:center;gap:8px;border-bottom:1px solid rgba(38,38,47,0.5)}}
 .queue b{{color:var(--cyan);font-weight:500}}
 .queue .dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 1.6s ease-in-out infinite}}
 @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.5;transform:scale(0.85)}}}}

 /* MAIN */
 main{{padding:32px;max-width:1280px;margin:0 auto}}
 .empty{{color:var(--dim);text-align:center;padding:80px 20px;font-size:15px}}
 .empty-emoji{{font-size:48px;margin-bottom:16px;display:block;filter:grayscale(0.3)}}

 /* VIDEO CARD */
 .video{{background:linear-gradient(180deg,var(--surface) 0%,rgba(19,19,26,0.6) 100%);border:1px solid var(--border);border-radius:16px;padding:24px;margin-bottom:24px;position:relative;overflow:hidden}}
 .video::before{{content:'';position:absolute;inset:0;border-radius:16px;padding:1px;background:linear-gradient(135deg,rgba(34,211,238,0.18),transparent 40%);-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}}
 .video-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:12px}}
 .video h2{{margin:0;font-size:18px;font-weight:700;letter-spacing:-0.01em;line-height:1.3}}
 .meta{{font-size:12px;color:var(--muted);font-family:'JetBrains Mono',monospace;display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:20px}}
 .meta .sep{{color:var(--dim)}}

 /* STATUS PILL */
 .status{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;background:rgba(125,125,140,0.15);color:var(--muted);font-family:'Inter',sans-serif;letter-spacing:0.02em;text-transform:uppercase}}
 .status::before{{content:'';width:6px;height:6px;border-radius:50%;background:currentColor}}
 .status.done{{background:rgba(34,197,94,0.12);color:var(--green)}}
 .status.error{{background:rgba(239,68,68,0.12);color:var(--red)}}
 .status.running{{background:rgba(251,146,60,0.12);color:var(--orange)}}
 .status.running::before{{animation:pulse 1.4s ease-in-out infinite}}

 /* REGEN BUTTON */
 .btn-regen{{padding:8px 14px;background:rgba(34,211,238,0.08);color:var(--cyan);border:1px solid rgba(34,211,238,0.2);border-radius:8px;font-size:12px;font-weight:600;transition:all 0.15s;font-family:inherit}}
 .btn-regen:hover{{background:rgba(34,211,238,0.16);border-color:rgba(34,211,238,0.4)}}

 /* CLIP GRID */
 .clips{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
 .clip{{background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:14px;transition:transform 0.2s,border-color 0.2s,box-shadow 0.2s}}
 .clip:hover{{transform:translateY(-2px);border-color:rgba(34,211,238,0.3);box-shadow:0 12px 32px -12px rgba(0,0,0,0.6)}}
 .clip video{{width:100%;border-radius:8px;background:#000;aspect-ratio:9/16;object-fit:cover}}
 .clip-no-video{{padding:40px 20px;text-align:center;color:var(--dim);background:#000;border-radius:8px;font-size:12px;aspect-ratio:9/16;display:flex;align-items:center;justify-content:center}}

 .hook{{font-weight:700;margin:12px 0 6px;font-size:14px;line-height:1.35;letter-spacing:-0.01em;color:var(--text)}}
 .caption{{font-size:12px;color:var(--muted);line-height:1.45}}

 /* TAGS as chips */
 .tags{{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}}
 .tag{{font-size:10px;padding:2px 7px;border-radius:6px;background:rgba(34,211,238,0.08);color:var(--cyan);font-weight:500;font-family:'JetBrains Mono',monospace}}

 .emojis{{display:flex;flex-wrap:wrap;gap:2px;margin-top:8px;font-size:18px}}
 .emojis span{{transition:transform 0.15s}}
 .emojis span:hover{{transform:scale(1.3)}}

 /* SCORE BAR */
 .score-row{{display:flex;align-items:center;gap:10px;margin-top:10px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted)}}
 .score-bar{{flex:1;height:4px;background:rgba(125,125,140,0.15);border-radius:2px;overflow:hidden;position:relative}}
 .score-fill{{height:100%;background:var(--grad);border-radius:2px;transition:width 0.4s}}
 .score-num{{font-weight:600;color:var(--text);min-width:24px;text-align:right}}
 .score-time{{color:var(--dim);white-space:nowrap}}

 .clip-foot{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid rgba(38,38,47,0.6)}}
 .btn-dl{{padding:6px 12px;background:var(--surface);color:var(--cyan);border:1px solid rgba(34,211,238,0.25);border-radius:6px;font-size:11px;font-weight:600;text-decoration:none;transition:all 0.15s;display:inline-flex;align-items:center;gap:4px}}
 .btn-dl:hover{{background:rgba(34,211,238,0.12);color:var(--cyan)}}
 .filesize{{font-size:11px;color:var(--dim);font-family:'JetBrains Mono',monospace}}

 @media (max-width:600px){{
   header{{padding:14px 16px}}
   main{{padding:16px}}
   .video{{padding:16px}}
   .queue{{padding:10px 16px;font-size:12px}}
 }}
</style></head>
<body>
<header>
 <div class="brand">
   <div class="brand-mark">🎬</div>
   <span class="brand-text">AUTO-CLIPPER</span>
   <span class="brand-by">kanz × claude</span>
 </div>
 <form class="submit" action="/submit" method="post">
  <input type="url" name="url" required placeholder="paste a YouTube / TikTok URL…" autocomplete="off">
  <button type="submit">Generate</button>
 </form>
</header>
<div class="queue"><span class="dot"></span><span>{queue_info}</span></div>
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
            f'<a class="btn-dl" href="{_esc(url)}" download="{download_name}">'
            f'⬇ <span>download</span></a>'
        )
    else:
        video_tag = '<div class="clip-no-video">⏳ rendering…</div>'
        download_btn = '<span></span>'

    size_html = f'<span class="filesize">{_esc(size)}</span>' if size else '<span></span>'

    tags_html = ""
    tag_list = [t.strip() for t in (c["hashtags"] or "").split(",") if t.strip()]
    if tag_list:
        chips = "".join(f'<span class="tag">#{_esc(t)}</span>' for t in tag_list)
        tags_html = f'<div class="tags">{chips}</div>'

    emoji_html = ""
    raw_emojis = c.get("emojis") if isinstance(c, dict) else None
    if raw_emojis:
        try:
            arr = json.loads(raw_emojis) if isinstance(raw_emojis, str) else raw_emojis
            if arr:
                chips = "".join(
                    f'<span title="{_esc(e.get("word",""))}">{_esc(e.get("emoji",""))}</span>'
                    for e in arr[:8] if isinstance(e, dict)
                )
                emoji_html = f'<div class="emojis">{chips}</div>'
        except Exception:
            pass

    st = c["status"] or "pending"
    st_class = "done" if st == "done" else ("error" if st.startswith("error") else "running" if "render" in st else "")

    score = float(c["score"] or 0)
    score_pct = max(0.0, min(100.0, score))
    score_html = (
        f'<div class="score-row">'
        f'<div class="score-bar"><div class="score-fill" style="width:{score_pct:.0f}%"></div></div>'
        f'<span class="score-num">{score:.0f}</span>'
        f'<span class="score-time">{c["start_sec"]:.0f}s–{c["end_sec"]:.0f}s</span>'
        f'</div>'
    )

    return (
        f'<div class="clip">{video_tag}'
        f'<div class="hook">{_esc(c["hook"])}</div>'
        f'<div class="caption">{_esc(c["caption"])}</div>'
        f'{tags_html}'
        f'{emoji_html}'
        f'{score_html}'
        f'<div class="clip-foot">{download_btn}<span class="status {st_class}">{_esc(st)}</span>{size_html}</div>'
        f'</div>'
    )


def _render_video(v: dict) -> str:
    clips = db.list_clips(v["id"])
    clip_html = "".join(_render_clip(c) for c in clips) or '<div style="color:#666">no clips yet</div>'
    st = v["status"] or "pending"
    terminal = {"done", "error"}
    st_class = "done" if st == "done" else ("error" if st == "error" else "running" if st not in terminal else "")

    can_regen = bool(v.get("path")) and Path(v["path"]).exists() if v.get("path") else False
    regen_btn = (
        f'<form method="post" action="/regenerate/{v["id"]}" style="display:inline">'
        f'<button type="submit" class="btn-regen" '
        f'title="Re-run analyze + render using cached source/transcript">↻ regenerate</button>'
        f'</form>'
        if can_regen else ''
    )

    dur = (v["duration"] or 0)
    dur_str = f'{int(dur//60)}m {int(dur%60)}s' if dur >= 60 else f'{int(dur)}s'

    return (
        f'<div class="video">'
        f'<div class="video-head">'
        f'<h2>{_esc(v["title"] or v["url"])}</h2>'
        f'{regen_btn}'
        f'</div>'
        f'<div class="meta">'
        f'<span class="status {st_class}">{_esc(st)}</span>'
        f'<span class="sep">·</span>'
        f'<span>{_esc(v["language"] or "?")}</span>'
        f'<span class="sep">·</span>'
        f'<span>{dur_str}</span>'
        f'<span class="sep">·</span>'
        f'<a href="{_esc(v["url"])}" target="_blank" rel="noopener">source ↗</a>'
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
        queue_info = f'processing <b>{_esc(cur)}</b>' + (f' &nbsp;·&nbsp; +{qsize} queued' if qsize else '')
    elif qsize:
        queue_info = f'{qsize} job(s) queued'
    else:
        queue_info = 'idle &nbsp;·&nbsp; paste a URL above to start'

    if not videos:
        return PAGE.format(
            queue_info=queue_info,
            body=(
                '<div class="empty">'
                '<span class="empty-emoji">🎬</span>'
                'no clips yet — paste a video URL up top'
                '</div>'
            ),
        )
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
    return {"videos": db.list_videos(), "queue_size": job_queue.qsize(), "current": _current["label"]}


@app.get("/api/videos/{video_id}/clips")
def api_clips(video_id: int) -> dict:
    v = db.get_video(video_id)
    if not v:
        raise HTTPException(404)
    return {"video": v, "clips": db.list_clips(video_id)}
